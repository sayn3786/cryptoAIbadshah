"""
Deterministic regression tests for flag-pattern detection & scoring correctness.

Detection / lifecycle:
  A. The newest CLOSED candle can confirm a breakout (no internal candles[:-1]).
  B. Backtest (build_price_analysis) and detect_flags see the same newest candle,
     and the production forming-candle remover (app._split_closed) keeps it too.
  C. A wrong-side breakout is permanently invalid (no resurrection on recovery).
  D. A breakout candle is never swallowed into the consolidation window.
  E. Channel-geometry contract: neutral/counter-trend only (strict NEUTRAL_SLOPE_PCT).
  F. Pole impulse-quality rejection (oscillatory) and acceptance (clean).
  G. Lifecycle fields for forming and confirmed flags.
  H. Target-hit (bull & bear) and adverse-price flags do not remain active.

Selection ranking (a confirmed flag must never be discarded by a stronger
forming flag — forming flags score zero, so that would delete the trade signal):
  R1. _flag_selection_rank priority + strength tie-break + legacy compatibility.
  R2. detect_flags() per-pole dedup preserves a confirmed flag over a stronger
      forming sibling from the same pole.
  R3. pick_dominant_flags() per-(direction,timeframe) dedup: confirmed beats
      stronger forming; two confirmed resolve by strength; forming fallback when
      none confirmed; an inactive flag cannot replace an active one.

Scoring:
  S. A surviving confirmed flag scores exactly once; forming flags score zero.

All candles are synthetic OHLC; no live APIs are used.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns                                                         # noqa: E402
from patterns import (                                                  # noqa: E402
    detect_flags, pick_dominant_flags, _flag_selection_rank,
    NEUTRAL_SLOPE_PCT,
)
from signals import generate_signal                                     # noqa: E402
from backtest import build_price_analysis                              # noqa: E402


# ── candle builders ────────────────────────────────────────────────────────────
STEP = 3_600_000
T0 = 1_000_000


def _c(ts, o, cl, half=0.2, v=100.0):
    return {"timestamp": ts, "open": o, "high": max(o, cl) + half,
            "low": min(o, cl) - half, "close": cl, "volume": v}


def build_flag(lead=16, direction="up", pole_closes=None, pole_bars=4,
               pole_step=3.0, flag_bars=5, flag_drift=-0.4, flag_half=0.6,
               flag_closes=None, post_closes=None, start=100.0):
    """lead(flat) + pole + flag(consolidation) + post candles.

    `pole_closes` overrides the pole path; `flag_closes` overrides the flag bar
    closes (else a linear `flag_drift` from the pole close is used).
    """
    out, ts, p = [], T0, start

    def add(o, cl, half=0.2):
        nonlocal ts, p
        out.append(_c(ts, o, cl, half))
        ts += STEP
        p = cl

    for i in range(lead):                               # flat lead (no pole here)
        add(p, p + (0.05 if i % 2 == 0 else -0.05), 0.1)

    if pole_closes is None:
        s = 1 if direction == "up" else -1
        pole_closes = [start + s * pole_step * (i + 1) for i in range(pole_bars)]
    for cl in pole_closes:
        add(p, cl, 0.2)

    if flag_closes is None:
        fbase = p
        flag_closes = [fbase + flag_drift * (i + 1) for i in range(flag_bars)]
    for cl in flag_closes:
        add(p, cl, flag_half)

    for cl in (post_closes or []):
        add(p, cl, 0.2)
    return out


def _divergence_candles():
    """Candles where ONE pole start yields both a weaker CONFIRMED flag and a
    stronger FORMING flag. Under strength-only dedup the forming one wins (the
    confirmed trade signal disappears); the lifecycle rank must keep the
    confirmed one. Empirically derived and pinned for determinism."""
    out, ts, p = [], T0, 100.0

    def add(cl, half):
        nonlocal ts, p
        out.append(_c(ts, p, cl, half))
        ts += STEP
        p = cl

    for i in range(16):
        add(100 + (0.05 if i % 2 == 0 else -0.05), 0.1)
    for cl in [101.5, 103.0, 104.5]:                            # pole (3 bars, +4.5%)
        add(cl, 0.2)
    for cl in [104.4, 104.3, 104.2, 104.1, 104.0]:             # flag (5 bars, drift -0.1)
        add(cl, 0.4)
    for cl in [105.0]:                                          # post → up-breakout
        add(cl, 0.2)
    return out


def _bull_flags(flags):
    return [f for f in flags if f["direction"] == "bullish"]


def _bear_flags(flags):
    return [f for f in flags if f["direction"] == "bearish"]


def _mk_flag(direction="bullish", timeframe="1W", strength=10.0,
             confirmed=False, is_active=True, tf_weight=1.0):
    return {"direction": direction, "timeframe": timeframe, "strength": strength,
            "confirmed": confirmed, "is_active": is_active, "tf_weight": tf_weight,
            "pole_pct": 10.0, "target": 150.0}   # needed by scoring reason strings


# ── A. newest closed candle can confirm a breakout ──────────────────────────────
def test_newest_closed_candle_confirms_breakout():
    candles = build_flag(direction="up", post_closes=[114.0])
    last_ts = candles[-1]["timestamp"]
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    confirmed = [f for f in _bull_flags(flags)
                 if f["confirmed"] and f["breakout_dir"] == "up"]
    assert confirmed, "final candle should confirm the bullish breakout"
    assert any(f["breakout_ts"] == last_ts for f in confirmed)


# ── B. backtest / detect parity + production closed-candle path ─────────────────
def test_backtest_and_detect_flags_closed_candle_parity():
    candles = build_flag(direction="up", post_closes=[114.0])
    last_ts = candles[-1]["timestamp"]

    direct = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    via_backtest = build_price_analysis(candles, "1D", "TESTX")["flags"]

    d_conf = [f for f in _bull_flags(direct) if f["breakout_ts"] == last_ts]
    p_conf = [f for f in _bull_flags(via_backtest) if f["breakout_ts"] == last_ts]
    assert d_conf and d_conf[0]["confirmed"]
    assert p_conf and p_conf[0]["confirmed"]


def test_production_split_closed_keeps_newest_completed_candle():
    # Exercises the REAL production forming-candle remover used by
    # app.build_analysis. All candles are historic, so none are still forming:
    # _split_closed must keep the newest COMPLETED candle and detect_flags must
    # then confirm a breakout that closes on it.
    pytest.importorskip("flask")
    import app
    candles = build_flag(direction="up", post_closes=[114.0])
    closed, live = app._split_closed(candles, 3600)   # 1h interval (STEP = 3.6e6 ms)
    assert live is None, "no candle is still forming here"
    assert closed[-1]["timestamp"] == candles[-1]["timestamp"], \
        "the newest completed candle must be kept (no second removal)"
    conf = [f for f in _bull_flags(detect_flags(closed, "1D", 1.0, 4.0))
            if f["confirmed"] and f["breakout_ts"] == candles[-1]["timestamp"]]
    assert conf, "production closed set must confirm the newest-candle breakout"


# ── C. wrong-side breakout is permanently invalid ───────────────────────────────
def test_bull_wrong_side_breakout_stays_invalid():
    # First post candle closes BELOW the pole low (unambiguous breakdown), then
    # price recovers ABOVE flag_high. Chronology must lock it invalid.
    candles = build_flag(direction="up", post_closes=[95.0, 120.0])
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    assert not [f for f in _bull_flags(flags)
                if f["confirmed"] and f["breakout_dir"] == "up"], \
        "a bull flag that first broke DOWN must never confirm up"


def test_bear_wrong_side_breakout_stays_invalid():
    candles = build_flag(direction="down", flag_drift=+0.4, start=100.0,
                         post_closes=[105.0, 80.0])
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    assert not [f for f in _bear_flags(flags)
                if f["confirmed"] and f["breakout_dir"] == "down"], \
        "a bear flag that first broke UP must never confirm down"


# ── D. breakout candle is not swallowed into the consolidation ──────────────────
def test_breakout_not_swallowed_into_consolidation():
    candles = build_flag(direction="up", flag_bars=5, flag_drift=-0.3,
                         post_closes=[115.0, 116.0, 117.0])
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    conf = [f for f in _bull_flags(flags) if f["confirmed"]]
    assert conf, "expected a confirmed bullish flag"
    f = conf[0]
    ts_list = [c["timestamp"] for c in candles]
    assert f["breakout_ts"] in ts_list
    assert f["breakout_ts"] > f["flag_end_ts"], "breakout must come after the flag"
    bo_idx = ts_list.index(f["breakout_ts"])
    flag_end_idx = ts_list.index(f["flag_end_ts"])
    assert bo_idx == flag_end_idx + 1, "breakout bar directly follows the flag"
    assert candles[bo_idx]["close"] > f["flag_high"]


# ── E. geometry contract: neutral/counter-trend only ────────────────────────────
def test_geometry_enforces_neutral_or_countertrend():
    # Contract (strict): NO returned bull flag may slope above +NEUTRAL_SLOPE_PCT
    # and NO bear flag below -NEUTRAL_SLOPE_PCT — a with-trend channel beyond the
    # neutral band is a wedge, not a flag. Checked across mild and strong drifts
    # and an explicitly steep ascending/descending channel.
    bull_builds = [
        build_flag(direction="up", flag_bars=5, flag_drift=+0.4, flag_half=0.4),
        build_flag(direction="up", flag_bars=5, flag_drift=+3.0, flag_half=0.4),
        build_flag(direction="up", flag_bars=5, flag_half=0.4,
                   flag_closes=[108.2, 110, 111.8, 113.6, 115.4]),
    ]
    for cs in bull_builds:
        assert all(f["slope_pct_per_bar"] <= NEUTRAL_SLOPE_PCT
                   for f in _bull_flags(detect_flags(cs, "1D", 1.0, 4.0)))

    bear_builds = [
        build_flag(direction="down", flag_bars=5, flag_drift=-0.4, flag_half=0.4),
        build_flag(direction="down", flag_bars=5, flag_drift=-3.0, flag_half=0.4),
    ]
    for cs in bear_builds:
        assert all(f["slope_pct_per_bar"] >= -NEUTRAL_SLOPE_PCT
                   for f in _bear_flags(detect_flags(cs, "1D", 1.0, 4.0)))


def test_geometry_accepts_neutral_and_countertrend():
    bull = build_flag(direction="up", flag_bars=5, flag_drift=-0.4)      # descending
    assert _bull_flags(detect_flags(bull, "1D", 1.0, 4.0))
    bear = build_flag(direction="down", flag_bars=5, flag_drift=+0.4)    # ascending
    assert _bear_flags(detect_flags(bear, "1D", 1.0, 4.0))


# ── F. pole impulse quality ─────────────────────────────────────────────────────
def test_pole_quality_rejects_oscillatory_accepts_clean():
    clean = build_flag(direction="up", pole_closes=[103, 106, 109, 112],
                       flag_bars=5, flag_drift=-0.4)
    osc = build_flag(direction="up",
                     pole_closes=[104, 100, 106, 102, 108, 104, 110, 111],
                     flag_bars=5, flag_drift=-0.4)
    assert _bull_flags(detect_flags(clean, "1D", 1.0, min_pole_pct=10.0))
    assert not _bull_flags(detect_flags(osc, "1D", 1.0, min_pole_pct=10.0))


# ── G. lifecycle fields ─────────────────────────────────────────────────────────
def test_lifecycle_fields_forming_and_confirmed():
    fb = _bull_flags(detect_flags(build_flag(direction="up"), "1D", 1.0, 4.0))
    assert fb
    ff = fb[0]
    assert ff["status"] == "forming"
    assert ff["confirmed"] is False
    assert ff["breakout_dir"] is None
    assert ff["breakout_ts"] is None
    assert ff["invalidation_reason"] is None
    assert ff["is_active"] is True

    cb = [f for f in _bull_flags(detect_flags(
        build_flag(direction="up", post_closes=[114.0]), "1D", 1.0, 4.0))
        if f["confirmed"]]
    assert cb
    cf = cb[0]
    assert cf["status"] == "confirmed"
    assert cf["confirmed"] is True
    assert cf["breakout_dir"] == "up"
    assert cf["breakout_ts"] is not None
    assert cf["is_active"] is True
    assert cf["invalidation_reason"] is None


# ── H. target-hit (bull & bear) and adverse-price are not active ────────────────
def test_target_hit_and_adverse_price_not_active():
    # Adverse bull: price far BELOW the flag low → not active.
    adverse = build_flag(direction="up", post_closes=[95.0])
    assert not [f for f in _bull_flags(detect_flags(adverse, "1D", 1.0, 4.0))
                if f["is_active"]]

    # Bull target already hit: price closes at/above the projected target → the
    # completed pattern must not remain active.
    bull_hit = build_flag(direction="up", post_closes=[130.0])
    assert not [f for f in _bull_flags(detect_flags(bull_hit, "1D", 1.0, 4.0))
                if f["is_active"]]

    # Bear target already hit: price closes at/below the projected target.
    bear_hit = build_flag(direction="down", flag_drift=+0.4, post_closes=[70.0])
    assert not [f for f in _bear_flags(detect_flags(bear_hit, "1D", 1.0, 4.0))
                if f["is_active"]]


# ── S. forming flag scores nothing; confirmed scores once ───────────────────────
def _make_candles(n, up=True, start=100.0):
    out, p = [], start
    for i in range(n):
        cl = p + (0.4 if up else -0.4)
        out.append(_c(T0 + i * STEP, p, cl, 0.3))
        p = cl
    return out


def _neutral_analysis(flags=None):
    a = {
        "symbol": "BTC", "timeframe": "1D",
        "candles": _make_candles(60, up=True),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
        "macd": {"histogram": 0.0, "cross": "none"},
    }
    if flags is not None:
        a["flags"] = flags
    return a


def _signal_flag(confirmed):
    return {
        "direction": "bullish", "timeframe": "1D", "is_active": True,
        "confirmed": confirmed, "dominant": False, "pole_pct": 10.0,
        "target": 150.0, "status": "confirmed" if confirmed else "forming",
        "breakout_dir": "up" if confirmed else None,
    }


def test_forming_flag_adds_no_points_confirmed_scores_once():
    base = generate_signal(_neutral_analysis(flags=[]))
    forming = generate_signal(_neutral_analysis(flags=[_signal_flag(False)]))
    confirmed = generate_signal(_neutral_analysis(flags=[_signal_flag(True)]))

    assert forming["score"] == base["score"]
    assert not any("flag" in r.lower() for r in forming["bullish_reasons"])

    assert confirmed["score"] > base["score"]
    hits = [r for r in confirmed["bullish_reasons"] if "confirmed bullish flag" in r.lower()]
    assert len(hits) == 1, f"expected one confirmed-flag reason, got {hits}"


# ── R1. ranking helper ──────────────────────────────────────────────────────────
def test_flag_selection_rank_priority_and_tiebreak():
    active_confirmed = _mk_flag(confirmed=True, is_active=True, strength=1)
    active_forming = _mk_flag(confirmed=False, is_active=True, strength=100)
    inactive = _mk_flag(confirmed=True, is_active=False, strength=1000)
    # lifecycle dominates strength
    assert _flag_selection_rank(active_confirmed) > _flag_selection_rank(active_forming)
    assert _flag_selection_rank(active_forming) > _flag_selection_rank(inactive)
    # strength breaks ties within a lifecycle tier
    lo = _mk_flag(confirmed=True, is_active=True, strength=5)
    hi = _mk_flag(confirmed=True, is_active=True, strength=6)
    assert _flag_selection_rank(hi) > _flag_selection_rank(lo)
    # legacy object with only confirmed/is_active (no `status`) still ranks
    legacy = {"confirmed": True, "is_active": True, "strength": 3}
    assert _flag_selection_rank(legacy)[0] == 2


# ── R2. detect_flags per-pole dedup preserves confirmed ─────────────────────────
def test_detect_flags_prefers_confirmed_over_stronger_forming_same_pole():
    candles = _divergence_candles()
    real = {f["pole_start_ts"]: f for f in detect_flags(candles, "1D", 1.0, 4.0)}
    confirmed_poles = [ts for ts, f in real.items()
                       if f["confirmed"] and f["is_active"]]
    assert confirmed_poles, "a confirmed active flag must be present"

    # Simulate the OLD strength-only dedup: at least one of those poles would
    # instead surface a FORMING flag — proving the lifecycle rank preserved the
    # confirmed one that a strength-only dedup would have dropped.
    orig = patterns._flag_selection_rank
    try:
        patterns._flag_selection_rank = lambda f: (0, float(f.get("strength", 0) or 0))
        strength_only = {f["pole_start_ts"]: f
                         for f in detect_flags(candles, "1D", 1.0, 4.0)}
    finally:
        patterns._flag_selection_rank = orig

    flipped = [ts for ts in confirmed_poles
               if ts in strength_only and not strength_only[ts]["confirmed"]]
    assert flipped, "rank must keep a confirmed flag a strength-only dedup would drop"


# ── R3. pick_dominant_flags per-(direction,timeframe) dedup ─────────────────────
def test_pick_dominant_confirmed_beats_stronger_forming():
    forming = _mk_flag(strength=50.0, confirmed=False)
    confirmed = _mk_flag(strength=10.0, confirmed=True)
    picked = [f for f in pick_dominant_flags([forming, confirmed])
              if f["direction"] == "bullish" and f["timeframe"] == "1W"]
    assert len(picked) == 1
    assert picked[0]["confirmed"] and picked[0]["strength"] == 10.0


def test_pick_dominant_two_confirmed_resolved_by_strength():
    weak = _mk_flag(strength=10.0, confirmed=True)
    strong = _mk_flag(strength=40.0, confirmed=True)
    picked = [f for f in pick_dominant_flags([weak, strong])
              if f["direction"] == "bullish" and f["timeframe"] == "1W"]
    assert len(picked) == 1 and picked[0]["strength"] == 40.0


def test_pick_dominant_forming_fallback_when_no_confirmed():
    weak = _mk_flag(strength=10.0, confirmed=False)
    strong = _mk_flag(strength=40.0, confirmed=False)
    picked = [f for f in pick_dominant_flags([weak, strong])
              if f["direction"] == "bullish" and f["timeframe"] == "1W"]
    assert len(picked) == 1
    assert not picked[0]["confirmed"] and picked[0]["strength"] == 40.0


def test_pick_dominant_inactive_cannot_replace_active():
    inactive_strong = _mk_flag(strength=99.0, confirmed=True, is_active=False)
    active_forming = _mk_flag(strength=5.0, confirmed=False, is_active=True)
    picked = [f for f in pick_dominant_flags([inactive_strong, active_forming])
              if f["direction"] == "bullish" and f["timeframe"] == "1W"]
    assert len(picked) == 1
    assert picked[0]["is_active"] and picked[0]["strength"] == 5.0


# ── R4. dominance pool: forming flags must not decide `dominant` ────────────────
def _by_dir(flags, direction):
    return [f for f in flags if f["direction"] == direction]


def test_dominance_confirmed_beats_stronger_forming_cross_direction():
    # Same TF: weak confirmed BULL vs strong forming BEAR. The forming bear must
    # not take dominance (it scores zero) — the confirmed bull is dominant and
    # earns the 20-pt base in generate_signal, exactly once.
    conf_bull = _mk_flag("bullish", strength=10.0, confirmed=True)
    form_bear = _mk_flag("bearish", strength=50.0, confirmed=False)
    out = pick_dominant_flags([conf_bull, form_bear])
    bull = _by_dir(out, "bullish")[0]
    bear = _by_dir(out, "bearish")[0]
    assert bull["dominant"] is True, "confirmed bull must be dominant"
    assert bear["dominant"] is False, "forming bear must not be dominant"

    # scoring: the dominant designation gives the confirmed bull the 20-pt BASE
    # (vs 10 secondary), exactly once. The engine's downstream lone-group damping
    # (−15%) scales the final score uniformly, so assert the base by comparing
    # the SAME analysis with dominance stripped: 20×0.85=17 vs 10×0.85≈8.
    base = generate_signal(_neutral_analysis(flags=[]))
    sig = generate_signal(_neutral_analysis(flags=out))
    stripped = [dict(f, dominant=False) for f in out]
    sig_secondary = generate_signal(_neutral_analysis(flags=stripped))
    assert base["score"] == 0
    assert sig["score"] == 17, "dominant 20-pt base after uniform -15% damping"
    assert sig_secondary["score"] == 8, "secondary 10-pt base after same damping"
    assert sig["score"] > sig_secondary["score"] > base["score"]
    hits = [r for r in sig["bullish_reasons"] if "confirmed bullish flag" in r.lower()]
    assert len(hits) == 1 and "dominant" in hits[0].lower()


def test_dominance_higher_tf_forming_does_not_demote_confirmed():
    # Confirmed flag on a LOWER timeframe vs a stronger forming flag on a HIGHER
    # timeframe. Confirmed flags exist → only they form the dominance pool, so
    # the low-TF confirmed flag stays dominant.
    conf_low = _mk_flag("bullish", timeframe="1D", strength=8.0,
                        confirmed=True, tf_weight=0.75)
    form_high = _mk_flag("bearish", timeframe="1W", strength=60.0,
                         confirmed=False, tf_weight=1.0)
    out = pick_dominant_flags([conf_low, form_high])
    assert _by_dir(out, "bullish")[0]["dominant"] is True
    assert _by_dir(out, "bearish")[0]["dominant"] is False


def test_dominance_multiple_confirmed_highest_tier_and_strength():
    # Only confirmed flags participate; the highest tf_weight confirmed tier
    # decides, and strength picks the direction inside that tier.
    conf_low_bull  = _mk_flag("bullish", timeframe="1D", strength=90.0,
                              confirmed=True, tf_weight=0.75)
    conf_high_bull = _mk_flag("bullish", timeframe="1W", strength=10.0,
                              confirmed=True, tf_weight=1.0)
    conf_high_bear = _mk_flag("bearish", timeframe="1W", strength=30.0,
                              confirmed=True, tf_weight=1.0)
    form_high_bull = _mk_flag("bullish", timeframe="2W", strength=500.0,
                              confirmed=False, tf_weight=1.2)   # must be ignored
    out = pick_dominant_flags([conf_low_bull, conf_high_bull,
                               conf_high_bear, form_high_bull])
    by = {(f["direction"], f["timeframe"]): f for f in out}
    # bear wins the confirmed 1W tier (30 > 10); the huge 2W forming flag is
    # excluded from the pool entirely
    assert by[("bearish", "1W")]["dominant"] is True
    assert by[("bullish", "1W")]["dominant"] is False
    assert by[("bullish", "1D")]["dominant"] is False, "lower confirmed tier"
    assert by[("bullish", "2W")]["dominant"] is False, "forming never dominant here"


def test_dominance_forming_only_fallback_display_but_zero_points():
    # No confirmed flags → forming flags may take `dominant` for the dashboard,
    # but they still contribute zero points in generate_signal.
    form_bull = _mk_flag("bullish", strength=40.0, confirmed=False)
    form_bear = _mk_flag("bearish", strength=10.0, confirmed=False)
    out = pick_dominant_flags([form_bull, form_bear])
    assert _by_dir(out, "bullish")[0]["dominant"] is True
    assert _by_dir(out, "bearish")[0]["dominant"] is False

    base = generate_signal(_neutral_analysis(flags=[]))
    sig = generate_signal(_neutral_analysis(flags=out))
    assert sig["score"] == base["score"], "forming flags add zero points"
    assert not any("flag" in r.lower() for r in sig["bullish_reasons"])


def test_dominance_all_inactive_deterministic_no_crash():
    # Inactive flags must not override active ones; and when EVERYTHING is
    # inactive the fallback still assigns dominance deterministically.
    inact_bull = _mk_flag("bullish", strength=20.0, confirmed=True, is_active=False)
    inact_bear = _mk_flag("bearish", strength=5.0, confirmed=True, is_active=False)
    out = pick_dominant_flags([inact_bull, inact_bear])
    assert _by_dir(out, "bullish")[0]["dominant"] is True   # strength 20 > 5
    assert _by_dir(out, "bearish")[0]["dominant"] is False

    # an active forming flag beats inactive flags for the dominance pool
    active_form_bear = _mk_flag("bearish", timeframe="1D", strength=1.0,
                                confirmed=False, is_active=True, tf_weight=0.75)
    out2 = pick_dominant_flags([inact_bull, active_form_bear])
    by2 = {(f["direction"], f["timeframe"]): f for f in out2}
    assert by2[("bearish", "1D")]["dominant"] is True, \
        "active forming flag forms the pool ahead of inactive flags"
    assert by2[("bullish", "1W")]["dominant"] is False


def test_dominance_tie_resolves_bullish():
    # Equal bull/bear strength within the eligible highest-weight tier → bullish.
    conf_bull = _mk_flag("bullish", strength=25.0, confirmed=True)
    conf_bear = _mk_flag("bearish", strength=25.0, confirmed=True)
    out = pick_dominant_flags([conf_bull, conf_bear])
    assert _by_dir(out, "bullish")[0]["dominant"] is True
    assert _by_dir(out, "bearish")[0]["dominant"] is False
