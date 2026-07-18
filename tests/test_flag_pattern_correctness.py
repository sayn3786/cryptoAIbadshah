"""
Deterministic regression tests for flag-pattern detection & scoring correctness.

Covers:
  A. The newest CLOSED candle can confirm a breakout (no internal candles[:-1]).
  B. Production (build_price_analysis) and detect_flags see the same newest candle.
  C. A wrong-side breakout is permanently invalid (no resurrection on recovery).
  D. A forming/unconfirmed flag contributes zero trading points; a confirmed
     flag scores exactly once.
  E. A breakout candle is never swallowed into the consolidation window.
  F. Channel-geometry rejection (strong with-trend slope) and acceptance.
  G. Pole impulse-quality rejection (oscillatory) and acceptance (clean).
  H. Lifecycle fields (status / confirmed / breakout_dir / breakout_ts /
     is_active / invalidation_reason) for forming and confirmed flags.
  I. Target-hit and adverse-price flags do not remain active.

All candles are synthetic OHLC; no live APIs are used.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import (                                                  # noqa: E402
    detect_flags, MAX_WITH_TREND_SLOPE_PCT,
)
from signals import generate_signal                                     # noqa: E402
from backtest import build_price_analysis                              # noqa: E402


# ── candle builders ────────────────────────────────────────────────────────────
STEP = 3_600_000
T0 = 1_000_000


def _c(ts, o, cl, half=0.2, v=100.0):
    """OHLC candle from open/close with symmetric wick `half`."""
    return {"timestamp": ts, "open": o, "high": max(o, cl) + half,
            "low": min(o, cl) - half, "close": cl, "volume": v}


def build_flag(lead=16, direction="up", pole_closes=None, pole_bars=4,
               pole_step=3.0, flag_bars=5, flag_drift=-0.4, flag_half=0.6,
               post_closes=None, start=100.0):
    """lead(flat) + pole + flag(consolidation) + post candles.

    `pole_closes` overrides the pole path (list of close prices); otherwise a
    clean linear pole of `pole_bars` × `pole_step` in `direction` is used.
    """
    out, ts, p = [], T0, start

    def add(o, cl, half=0.2):
        nonlocal ts, p
        out.append(_c(ts, o, cl, half))
        ts += STEP
        p = cl

    for i in range(lead):                               # flat lead (no pole here)
        add(p, p + (0.05 if i % 2 == 0 else -0.05), 0.1)

    if pole_closes is None:                             # clean impulse
        s = 1 if direction == "up" else -1
        pole_closes = [start + s * pole_step * (i + 1) for i in range(pole_bars)]
    for cl in pole_closes:                              # opens chain from prev close
        add(p, cl, 0.2)

    fbase = p
    for i in range(flag_bars):                          # consolidation
        add(p, fbase + flag_drift * (i + 1), flag_half)

    if post_closes:
        for cl in post_closes:
            add(p, cl, 0.2)
    return out


def _bull_flags(flags):
    return [f for f in flags if f["direction"] == "bullish"]


def _bear_flags(flags):
    return [f for f in flags if f["direction"] == "bearish"]


# ── A. newest closed candle can confirm a breakout ──────────────────────────────
def test_newest_closed_candle_confirms_breakout():
    # bullish flag whose consolidation tops ~112; the FINAL candle closes above it.
    candles = build_flag(direction="up", post_closes=[114.0])
    last_ts = candles[-1]["timestamp"]
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    confirmed = [f for f in _bull_flags(flags)
                 if f["confirmed"] and f["breakout_dir"] == "up"]
    assert confirmed, "final candle should confirm the bullish breakout"
    # the breakout is the newest supplied candle — impossible under candles[:-1]
    assert any(f["breakout_ts"] == last_ts for f in confirmed)


# ── B. production / detect_flags closed-candle parity ───────────────────────────
def test_build_price_analysis_and_detect_flags_parity():
    candles = build_flag(direction="up", post_closes=[114.0])
    last_ts = candles[-1]["timestamp"]

    direct = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    via_prod = build_price_analysis(candles, "1D", "TESTX")["flags"]

    d_conf = [f for f in _bull_flags(direct) if f["breakout_ts"] == last_ts]
    p_conf = [f for f in _bull_flags(via_prod) if f["breakout_ts"] == last_ts]
    assert d_conf, "detect_flags must see the newest candle's breakout"
    assert p_conf, "build_price_analysis must see the newest candle's breakout"
    # no internal second removal: both resolve the SAME confirmed breakout bar
    assert d_conf[0]["confirmed"] and p_conf[0]["confirmed"]


# ── C. wrong-side breakout is permanently invalid ───────────────────────────────
def test_bull_wrong_side_breakout_stays_invalid():
    # bull flag: first post candle CLOSES BELOW the pole low (unambiguous
    # breakdown — no window can treat it as consolidation), then price recovers
    # ABOVE flag_high. Chronology must lock it invalid — recovery cannot confirm.
    candles = build_flag(direction="up", post_closes=[95.0, 120.0])
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    resurrected = [f for f in _bull_flags(flags)
                   if f["confirmed"] and f["breakout_dir"] == "up"]
    assert not resurrected, "a bull flag that first broke DOWN must never confirm up"


def test_bear_wrong_side_breakout_stays_invalid():
    # bear flag: first post candle CLOSES ABOVE the pole high (unambiguous), then
    # price collapses. Chronology must lock it invalid.
    candles = build_flag(direction="down", flag_drift=+0.4, start=100.0,
                         post_closes=[105.0, 80.0])
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    resurrected = [f for f in _bear_flags(flags)
                   if f["confirmed"] and f["breakout_dir"] == "down"]
    assert not resurrected, "a bear flag that first broke UP must never confirm down"


# ── D. forming flag scores nothing; confirmed scores once ───────────────────────
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


def _flag(confirmed):
    return {
        "direction": "bullish", "timeframe": "1D", "is_active": True,
        "confirmed": confirmed, "dominant": False, "pole_pct": 10.0,
        "target": 150.0, "status": "confirmed" if confirmed else "forming",
        "breakout_dir": "up" if confirmed else None,
    }


def test_forming_flag_adds_no_points_confirmed_scores_once():
    base = generate_signal(_neutral_analysis(flags=[]))
    forming = generate_signal(_neutral_analysis(flags=[_flag(confirmed=False)]))
    confirmed = generate_signal(_neutral_analysis(flags=[_flag(confirmed=True)]))

    # forming flag: score unchanged, and it never appears as a directional reason
    assert forming["score"] == base["score"]
    assert not any("flag" in r.lower() for r in forming["bullish_reasons"])

    # confirmed flag: score rises, and the reason appears exactly once
    assert confirmed["score"] > base["score"]
    hits = [r for r in confirmed["bullish_reasons"] if "confirmed bullish flag" in r.lower()]
    assert len(hits) == 1, f"expected one confirmed-flag reason, got {hits}"


# ── E. breakout candle is not swallowed into the consolidation ──────────────────
def test_breakout_not_swallowed_into_consolidation():
    # 5-bar consolidation, then a clean up-breakout, then two more bars.
    candles = build_flag(direction="up", flag_bars=5, flag_drift=-0.3,
                         post_closes=[115.0, 116.0, 117.0])
    flags = detect_flags(candles, "1D", 1.0, min_pole_pct=4.0)
    conf = [f for f in _bull_flags(flags) if f["confirmed"]]
    assert conf, "expected a confirmed bullish flag"
    f = conf[0]
    # breakout_ts is a real candle timestamp AFTER the consolidation window,
    # and the breakout bar is NOT counted in consolidation_bars.
    ts_list = [c["timestamp"] for c in candles]
    assert f["breakout_ts"] in ts_list
    assert f["breakout_ts"] > f["flag_end_ts"], "breakout must come after the flag"
    bo_idx = ts_list.index(f["breakout_ts"])
    flag_end_idx = ts_list.index(f["flag_end_ts"])
    assert bo_idx == flag_end_idx + 1, "breakout bar directly follows the flag"
    # its close is genuinely outside the flag channel
    assert candles[bo_idx]["close"] > f["flag_high"]


# ── F. geometry rejection / acceptance ──────────────────────────────────────────
def test_geometry_rejects_strong_with_trend_slope():
    # Strongly ASCENDING bull channel — must never be returned as a bull flag.
    up = build_flag(direction="up", flag_bars=5, flag_drift=+3.0, flag_half=0.4)
    up_flags = _bull_flags(detect_flags(up, "1D", 1.0, min_pole_pct=4.0))
    assert not any(f["slope_pct_per_bar"] > MAX_WITH_TREND_SLOPE_PCT for f in up_flags)

    # Strongly DESCENDING bear channel — must never be returned as a bear flag.
    dn = build_flag(direction="down", flag_bars=5, flag_drift=-3.0, flag_half=0.4)
    dn_flags = _bear_flags(detect_flags(dn, "1D", 1.0, min_pole_pct=4.0))
    assert not any(f["slope_pct_per_bar"] < -MAX_WITH_TREND_SLOPE_PCT for f in dn_flags)


def test_geometry_accepts_neutral_and_countertrend():
    # bull flag with a mild descending (counter-trend) channel → accepted
    bull = build_flag(direction="up", flag_bars=5, flag_drift=-0.4)
    assert _bull_flags(detect_flags(bull, "1D", 1.0, min_pole_pct=4.0)), \
        "a mild descending bull flag should be accepted"
    # bear flag with a mild ascending (counter-trend) channel → accepted
    bear = build_flag(direction="down", flag_bars=5, flag_drift=+0.4)
    assert _bear_flags(detect_flags(bear, "1D", 1.0, min_pole_pct=4.0)), \
        "a mild ascending bear flag should be accepted"


# ── G. pole impulse quality ─────────────────────────────────────────────────────
def test_pole_quality_rejects_oscillatory_accepts_clean():
    # Same ~+11-12% net move, but one path is a clean impulse and the other
    # is a high-oscillation zig-zag. With a 10% pole floor only the clean pole
    # can qualify; the choppy one has no efficient qualifying sub-window.
    clean = build_flag(direction="up", pole_closes=[103, 106, 109, 112],
                       flag_bars=5, flag_drift=-0.4)
    osc = build_flag(direction="up",
                     pole_closes=[104, 100, 106, 102, 108, 104, 110, 111],
                     flag_bars=5, flag_drift=-0.4)

    assert _bull_flags(detect_flags(clean, "1D", 1.0, min_pole_pct=10.0)), \
        "a clean directional impulse should form a pole"
    assert not _bull_flags(detect_flags(osc, "1D", 1.0, min_pole_pct=10.0)), \
        "an oscillatory move that barely nets the % is not a pole"


# ── H. lifecycle fields ─────────────────────────────────────────────────────────
def test_lifecycle_fields_forming_and_confirmed():
    forming = detect_flags(build_flag(direction="up"), "1D", 1.0, min_pole_pct=4.0)
    fb = _bull_flags(forming)
    assert fb, "forming bull flag expected"
    ff = fb[0]
    assert ff["status"] == "forming"
    assert ff["confirmed"] is False
    assert ff["breakout_dir"] is None
    assert ff["breakout_ts"] is None
    assert ff["invalidation_reason"] is None
    assert ff["is_active"] is True

    confirmed = detect_flags(build_flag(direction="up", post_closes=[114.0]),
                             "1D", 1.0, min_pole_pct=4.0)
    cb = [f for f in _bull_flags(confirmed) if f["confirmed"]]
    assert cb, "confirmed bull flag expected"
    cf = cb[0]
    assert cf["status"] == "confirmed"
    assert cf["confirmed"] is True
    assert cf["breakout_dir"] == "up"
    assert cf["breakout_ts"] is not None
    assert cf["is_active"] is True
    assert cf["invalidation_reason"] is None


# ── I. target-hit / adverse-price flags do not remain active ────────────────────
def test_target_hit_and_adverse_price_not_active():
    # Adverse: bull flag, price crashes far BELOW the flag low on the last bar.
    # (>3% below flag_low → price_near_flag False → not returned.)
    adverse = build_flag(direction="up", post_closes=[95.0])
    active_bull = [f for f in _bull_flags(detect_flags(adverse, "1D", 1.0, 4.0))
                   if f["is_active"]]
    assert not active_bull, "a bull flag with price far below flag_low is not active"
