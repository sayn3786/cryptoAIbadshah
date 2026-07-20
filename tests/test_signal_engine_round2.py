"""
Deterministic regression tests for signal-engine correctness round 2.

Covers (one section per defect):
  1. Confluence sign inversion — Flow+Trend / Momentum+Trend bonuses take their
     sign from the agreeing groups, never the running score; contradiction
     adjustments reduce confidence in the TREND direction with matching text.

All candles/CVD are synthetic; no live APIs.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import generate_signal                                     # noqa: E402

STEP = 3_600_000
T0 = 1_000_000


# ── shared builders ────────────────────────────────────────────────────────────
def mk_candles(n, up=True, start=100.0):
    out, p = [], start
    for i in range(n):
        cl = p + (0.4 if up else -0.4)
        out.append({"timestamp": T0 + i * STEP, "open": p,
                    "high": max(p, cl) + 0.3, "low": min(p, cl) - 0.3,
                    "close": cl, "volume": 10.0})
        p = cl
    return out


def mk_cvd(deltas, trend):
    s, c = [], 0.0
    for i, d in enumerate(deltas):
        c += d
        s.append({"timestamp": T0 + i * STEP, "cvd": round(c, 2), "delta": d})
    return {"current": round(c, 2), "trend": trend, "series": s, "unit": "usd"}


# ── 1. confluence sign inversion ───────────────────────────────────────────────
def _confluence_fixture(bull=True, opposing=False):
    """Bullish (or bearish) TREND + FLOW agreement; `opposing=True` adds enough
    opposite-direction sentiment+pattern to push the pre-combo score across
    zero — the exact setup where the old score-keyed sign inverted the bonus."""
    d = 1 if bull else -1
    a = {
        "symbol": "ETH", "timeframe": "1D", "candles": mk_candles(60, up=bull),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1 * d, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [50, 200] if bull else [],
                      "below": [] if bull else [50, 200],
                      "aligned": "bullish" if bull else "bearish",
                      "ema50": 100, "ema21": 101},
        "supertrend": {"direction": "bullish" if bull else "bearish", "value": 98},
        "macd": {"histogram": 0.0, "cross": "none"},
        "spot_cvd": mk_cvd([5 * d] * 12, "bullish" if bull else "bearish"),
    }
    if opposing:
        a["long_short"] = ({"ratio": 3.0, "long_pct": 75, "short_pct": 25} if bull
                           else {"ratio": 0.5, "long_pct": 25, "short_pct": 75})
        a["fear_greed"] = ({"value": 90, "label": "Extreme Greed"} if bull
                           else {"value": 10, "label": "Extreme Fear"})
        a["engulfing"] = [{"direction": "bearish" if bull else "bullish",
                           "confirmed": True, "candles_ago": 1, "body_ratio": 2.0}]
    return a


def test_bullish_agreement_cannot_deepen_a_negative_score():
    # Bullish trend+flow, but heavy bearish sentiment+pattern → overall negative.
    # The +12 Flow+Trend bonus must still be BULLISH: label in bull_reasons and
    # the score must sit 24 points above what the old sign-inverted code gave.
    s = generate_signal(_confluence_fixture(bull=True, opposing=True))
    assert s["score"] < 0, "fixture must remain net bearish overall"
    assert any("Flow+Trend" in r for r in s["bullish_reasons"]), \
        "bullish group agreement must be reported as a bullish reason"
    assert not any("Flow+Trend" in r for r in s["bearish_reasons"]), \
        "bullish group agreement must never appear as a bearish reason"


def test_bearish_agreement_cannot_boost_a_positive_score():
    s = generate_signal(_confluence_fixture(bull=False, opposing=True))
    assert s["score"] > 0, "mirror fixture must remain net bullish overall"
    assert any("Flow+Trend" in r for r in s["bearish_reasons"])
    assert not any("Flow+Trend" in r for r in s["bullish_reasons"])


def test_confluence_mirror_symmetry():
    # Clean fixtures (no opposing groups): LONG and SHORT mirrors must produce
    # exactly opposite scores, with the combo labels in the matching lists.
    b = generate_signal(_confluence_fixture(bull=True))
    r = generate_signal(_confluence_fixture(bull=False))
    assert b["score"] == -r["score"], "mirror fixtures must be symmetric"
    assert any("Flow+Trend" in x for x in b["bullish_reasons"])
    assert any("Flow+Trend" in x for x in r["bearish_reasons"])
    # inversion fixtures are symmetric too
    bo = generate_signal(_confluence_fixture(bull=True, opposing=True))
    ro = generate_signal(_confluence_fixture(bull=False, opposing=True))
    assert bo["score"] == -ro["score"]


def _contradiction_fixture(trend_bull: bool):
    """TREND one way, FLOW the other, plus same-direction-as-flow sentiment so
    the running score crosses to the flow side — the case where the old
    score-keyed message called a bearish trend an 'uptrend'."""
    d = 1 if trend_bull else -1
    a = {
        "symbol": "ETH", "timeframe": "1D", "candles": mk_candles(60, up=trend_bull),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1 * d, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [50, 200] if trend_bull else [],
                      "below": [] if trend_bull else [50, 200],
                      "aligned": "bullish" if trend_bull else "bearish",
                      "ema50": 100, "ema21": 101},
        "supertrend": {"direction": "bullish" if trend_bull else "bearish", "value": 98},
        "macd": {"histogram": 0.0, "cross": "none"},
        # flow OPPOSES the trend
        "spot_cvd": mk_cvd([-5 * d] * 12, "bearish" if trend_bull else "bullish"),
        # sentiment pushes the overall score to the flow side
        "long_short": ({"ratio": 3.0, "long_pct": 75, "short_pct": 25} if trend_bull
                       else {"ratio": 0.5, "long_pct": 25, "short_pct": 75}),
        "fear_greed": ({"value": 90, "label": "Extreme Greed"} if trend_bull
                       else {"value": 10, "label": "Extreme Fear"}),
    }
    return a


def test_contradiction_message_matches_actual_trend_direction():
    # Bearish trend + bullish flow: the divergence must be described as
    # contradicting a DOWNTREND (bullish caution), never an "uptrend".
    s = generate_signal(_contradiction_fixture(trend_bull=False))
    all_reasons = s["bullish_reasons"] + s["bearish_reasons"]
    div = [r for r in all_reasons if "Flow-Trend divergence" in r]
    assert div, "divergence reason expected"
    assert all("downtrend" in r for r in div), f"got: {div}"
    assert any("Flow-Trend divergence" in r for r in s["bullish_reasons"]), \
        "reduced confidence in a downtrend is a bullish-side caution"

    # Mirror: bullish trend + bearish flow → 'uptrend' wording, bearish side.
    m = generate_signal(_contradiction_fixture(trend_bull=True))
    mdiv = [r for r in m["bullish_reasons"] + m["bearish_reasons"]
            if "Flow-Trend divergence" in r]
    assert mdiv and all("uptrend" in r for r in mdiv)
    assert any("Flow-Trend divergence" in r for r in m["bearish_reasons"])


def test_contradiction_mirrors_have_equal_absolute_adjustment():
    b = generate_signal(_contradiction_fixture(trend_bull=True))
    r = generate_signal(_contradiction_fixture(trend_bull=False))
    assert b["score"] == -r["score"], "contradiction mirrors must be symmetric"


# ── 2. permanent FVG lifecycle ─────────────────────────────────────────────────
from indicators import detect_fvg                                       # noqa: E402
from backtest import build_price_analysis                              # noqa: E402


def _ohlc(i, o, h, l, c):
    return {"timestamp": T0 + i * STEP, "open": o, "high": h, "low": l,
            "close": c, "volume": 10.0}


def _bull_gap_series(post):
    """Flat lead, then a 3-candle bullish (up) FVG: prev.high=100.5 < nxt.low=103.
    Gap bottom=100.5, top=103 (2.49% > 1.5% min). `post` = list of (h, l) wick
    pairs appended after the formation (closes mid-range)."""
    cs = [_ohlc(i, 100, 100.5, 99.5, 100) for i in range(8)]
    n = len(cs)
    cs.append(_ohlc(n,     100, 100.5, 99.5, 100.2))   # prev
    cs.append(_ohlc(n + 1, 100.4, 104, 100.2, 103.8))  # curr (big impulse)
    cs.append(_ohlc(n + 2, 103.8, 105, 103.0, 104.5))  # nxt (low 103 > 100.5)
    for k, (h, l) in enumerate(post):
        cs.append(_ohlc(n + 3 + k, (h + l) / 2, h, l, (h + l) / 2))
    return cs


def _bear_gap_series(post):
    """Mirror: bearish (down) FVG: prev.low=99.5 > nxt.high=97. Gap top=99.5."""
    cs = [_ohlc(i, 100, 100.5, 99.5, 100) for i in range(8)]
    n = len(cs)
    cs.append(_ohlc(n,     100, 100.5, 99.5, 99.8))    # prev
    cs.append(_ohlc(n + 1, 99.6, 99.8, 96.0, 96.2))    # curr
    cs.append(_ohlc(n + 2, 96.2, 97.0, 95.0, 95.5))    # nxt (high 97 < 99.5)
    for k, (h, l) in enumerate(post):
        cs.append(_ohlc(n + 3 + k, (h + l) / 2, h, l, (h + l) / 2))
    return cs


_FORMATION_TS = T0 + 9 * STEP    # `curr` of the crafted 3-candle gap in both builders


def _gaps(cs, typ):
    # select the ORIGINAL crafted gap by its formation timestamp — the post
    # candles in some fixtures create additional gaps and distance-sorting
    # reorders the list.
    return [f for f in detect_fvg(cs)
            if f["type"] == typ and f["timestamp"] == _FORMATION_TS]


def test_fvg_bullish_unfilled():
    # price stays above the gap bottom (100.5) forever → unfilled
    cs = _bull_gap_series(post=[(105, 103.5), (106, 104)])
    g = _gaps(cs, "bullish")
    assert g and g[0]["filled"] is False and g[0]["filled_at"] is None


def test_fvg_bearish_unfilled():
    cs = _bear_gap_series(post=[(96.5, 95.0), (96.0, 94.5)])
    g = _gaps(cs, "bearish")
    assert g and g[0]["filled"] is False and g[0]["filled_at"] is None


def test_fvg_bullish_filled_stays_filled_after_recovery():
    # a later candle trades AT the gap bottom (low 100.5) → filled; price then
    # rallies far above the gap — it must STAY filled (no resurrection).
    cs = _bull_gap_series(post=[(104, 100.5), (108, 106), (112, 110)])
    g = _gaps(cs, "bullish")
    assert g and g[0]["filled"] is True
    assert g[0]["filled_at"] == cs[-3]["timestamp"], "filled by the touching candle"


def test_fvg_bearish_filled_stays_filled_after_recovery():
    # later candle wicks to the gap top (high 99.5) → filled; price then dumps
    # far below — remains filled.
    cs = _bear_gap_series(post=[(99.5, 96.0), (92, 90), (88, 86)])
    g = _gaps(cs, "bearish")
    assert g and g[0]["filled"] is True
    assert g[0]["filled_at"] == cs[-3]["timestamp"]


def test_fvg_formation_candles_do_not_fill_themselves():
    # Gap formed by the LAST three candles — no post-formation candle exists,
    # so the gap cannot be filled by its own structure.
    cs = _bull_gap_series(post=[])
    g = _gaps(cs, "bullish")
    assert g and g[0]["filled"] is False and g[0]["filled_at"] is None


def test_filled_fvg_contributes_zero_signal_points():
    a = {
        "symbol": "ETH", "timeframe": "1D", "candles": mk_candles(60, up=True),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
        "macd": {"histogram": 0.0, "cross": "none"},
    }
    base = generate_signal(dict(a, fvgs=[]))
    cur = a["candles"][-1]["close"]
    filled_gap = {"type": "bullish", "gap_type": "fvg", "top": cur * 0.99,
                  "bottom": cur * 0.97, "midpoint": cur * 0.98, "size_pct": 2.0,
                  "timestamp": T0, "filled": True, "filled_at": T0 + STEP,
                  "distance_pct": -2.0}
    with_filled = generate_signal(dict(a, fvgs=[filled_gap]))
    assert with_filled["score"] == base["score"], "filled gaps must score zero"
    # sanity: the identical gap UNFILLED does move the score
    unfilled = dict(filled_gap, filled=False, filled_at=None)
    with_unfilled = generate_signal(dict(a, fvgs=[unfilled]))
    assert with_unfilled["score"] != base["score"]


def test_fvg_production_backtest_parity():
    cs = _bull_gap_series(post=[(104, 100.5), (108, 106)])
    direct = detect_fvg(cs)
    via_backtest = build_price_analysis(cs, "1D", "TESTX")["fvgs"]
    assert direct == via_backtest, "identical lifecycle through both paths"


# ── 3. CVD scoring monotonic across dominance classes ──────────────────────────
from indicators import detect_cvd_divergence                            # noqa: E402


def _mk_cvd_usd(deltas):
    s, c = [], 0.0
    out = []
    for i, d in enumerate(deltas):
        c += d
        out.append({"timestamp": T0 + i * STEP, "cvd": round(c, 2), "delta": d})
    return {"current": round(c, 2), "trend": ("bullish" if sum(deltas) > 0
                                              else "bearish" if sum(deltas) < 0
                                              else "neutral"),
            "series": out, "unit": "usd"}


def _cvd_market(fut_share, rising=True, n=12):
    """Aligned spot/futures USD CVD at an exact futures gross share, with price
    trending ±1% over the last 5 candles."""
    d = 1 if rising else -1
    candles = []
    p = 100.0
    for i in range(n):
        cl = p + 0.4 * d
        candles.append({"timestamp": T0 + i * STEP, "open": p,
                        "high": max(p, cl) + 0.1, "low": min(p, cl) - 0.1,
                        "close": cl, "volume": 10.0})
        p = cl
    f_per = fut_share * 100.0
    s_per = (1.0 - fut_share) * 100.0
    spot = _mk_cvd_usd([s_per * d] * n)
    fut = _mk_cvd_usd([f_per * d] * n)
    return spot, fut, candles


def _flow_score(div):
    """Score contribution of a given divergence dict through generate_signal."""
    a = {
        "symbol": "ETH", "timeframe": "1D", "candles": mk_candles(60, up=True),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
        "macd": {"histogram": 0.0, "cross": "none"},
    }
    base = generate_signal(a)["score"]
    return generate_signal(dict(a, cvd_divergence=div))["score"] - base


def test_cvd_rising_classes_and_no_sign_cliff():
    shares = [0.64, 0.65, 0.79, 0.80, 0.81]
    expected_type = {0.64: "confirmed_up", 0.65: "futures_heavy_up",
                     0.79: "futures_heavy_up", 0.80: "futures_dominated_up",
                     0.81: "futures_dominated_up"}
    scores = {}
    for sh in shares:
        spot, fut, cs = _cvd_market(sh, rising=True)
        div = detect_cvd_divergence(spot, fut, cs)
        assert div["type"] == expected_type[sh], f"share {sh}: {div['type']}"
        assert div["signal"] == "bullish", \
            f"aligned rising flow must stay bullish at share {sh}"
        scores[sh] = _flow_score(div)
    # every class scores POSITIVE (no inversion) and conviction is
    # non-increasing as futures share rises
    assert all(v > 0 for v in scores.values()), scores
    ordered = [scores[s] for s in shares]
    assert all(a >= b for a, b in zip(ordered, ordered[1:])), scores
    # a 1% share move around each boundary cannot flip the sign or jump wildly
    assert abs(scores[0.79] - scores[0.80]) <= 15
    assert abs(scores[0.64] - scores[0.65]) <= 15


def test_cvd_falling_classes_and_no_sign_cliff():
    shares = [0.64, 0.65, 0.79, 0.80, 0.81]
    expected_type = {0.64: "confirmed_down", 0.65: "futures_heavy_down",
                     0.79: "futures_heavy_down", 0.80: "futures_dominated_down",
                     0.81: "futures_dominated_down"}
    scores = {}
    for sh in shares:
        spot, fut, cs = _cvd_market(sh, rising=False)
        div = detect_cvd_divergence(spot, fut, cs)
        assert div["type"] == expected_type[sh], f"share {sh}: {div['type']}"
        assert div["signal"] == "bearish", \
            f"aligned falling flow must stay bearish at share {sh}"
        scores[sh] = _flow_score(div)
    assert all(v < 0 for v in scores.values()), scores
    ordered = [scores[s] for s in shares]
    assert all(a <= b for a, b in zip(ordered, ordered[1:])), scores
    assert abs(scores[0.79] - scores[0.80]) <= 15
    assert abs(scores[0.64] - scores[0.65]) <= 15


def test_cvd_squeeze_risk_is_metadata_not_score_flip():
    spot, fut, cs = _cvd_market(0.85, rising=True)
    div = detect_cvd_divergence(spot, fut, cs)
    assert div["squeeze_risk"] == "long_squeeze_elevated"
    spot, fut, cs = _cvd_market(0.85, rising=False)
    div = detect_cvd_divergence(spot, fut, cs)
    assert div["squeeze_risk"] == "short_squeeze_elevated"
    # spot-side classes carry no squeeze risk
    spot, fut, cs = _cvd_market(0.15, rising=True)
    assert detect_cvd_divergence(spot, fut, cs)["squeeze_risk"] is None


def test_cvd_neutral_legs_have_explicit_types():
    # spot bullish + futures NEUTRAL → explicit spot_only_up, not generic neutral
    _, _, cs = _cvd_market(0.5, rising=True)
    spot = _mk_cvd_usd([50.0] * 12)
    fut_neutral = _mk_cvd_usd([5.0 if i % 2 == 0 else -5.0 for i in range(12)])
    div = detect_cvd_divergence(spot, fut_neutral, cs)
    assert div["type"] == "spot_only_up" and div["signal"] == "bullish"

    # spot NEUTRAL + futures bullish → explicit futures_only_up
    spot_neutral = _mk_cvd_usd([5.0 if i % 2 == 0 else -5.0 for i in range(12)])
    fut = _mk_cvd_usd([50.0] * 12)
    div = detect_cvd_divergence(spot_neutral, fut, cs)
    assert div["type"] == "futures_only_up"

    # falling mirrors
    _, _, csd = _cvd_market(0.5, rising=False)
    div = detect_cvd_divergence(_mk_cvd_usd([-50.0] * 12), fut_neutral, csd)
    assert div["type"] == "spot_only_down" and div["signal"] == "bearish"
    div = detect_cvd_divergence(spot_neutral, _mk_cvd_usd([-50.0] * 12), csd)
    assert div["type"] == "futures_only_down"


# ── 4. closed-candle contract: whale / volume / equal levels / acc range ───────
from indicators import detect_whale_activity, calculate_volume_signal   # noqa: E402
from patterns import (                                                  # noqa: E402
    detect_equal_levels, detect_accumulation_range, detect_choch,
)


def _vol_candles(n=30, vol=10.0, up=True, start=100.0):
    out, p = [], start
    for i in range(n):
        cl = p + (0.4 if up else -0.4)
        out.append({"timestamp": T0 + i * STEP, "open": p,
                    "high": max(p, cl) + 0.05, "low": min(p, cl) - 0.05,
                    "close": cl, "volume": vol,
                    "taker_buy_volume": vol * (0.8 if up else 0.2)})
        p = cl
    return out


def test_whale_activity_sees_newest_closed_candle():
    cs = _vol_candles(30)
    cs[-1]["volume"] = 100.0                 # 10× spike on the NEWEST closed bar
    cs[-1]["taker_buy_volume"] = 80.0
    events = detect_whale_activity(cs)
    assert any(e["timestamp"] == cs[-1]["timestamp"] and e["candles_ago"] == 1
               for e in events), \
        "the newest closed candle's whale spike must be detected"


def test_volume_signal_newest_closed_candle_and_clean_baseline():
    # 10× spike on the newest closed bar (old code dropped it via [:-1]); the
    # baseline must exclude the candidate, so the reported ratio is the full
    # spike vs the PRIOR average, not diluted by its own volume.
    cs = _vol_candles(30)
    cs[-1]["volume"] = 100.0
    out = calculate_volume_signal(cs)
    assert out["signal"] == "bullish"
    assert out["ratio"] >= 9.5, f"candidate must not inflate its own baseline: {out}"


def test_volume_sustained_uses_actual_latest_three():
    # last 3 closed candles at 1.5× volume but indecisive bodies (skip the
    # single-spike branch) — the sustained check must read candles[-3:], not
    # [-4:-1]. candles[-4] is kept at 1× so the old slice would fail the 1.35×.
    cs = _vol_candles(30)
    for c in cs[-3:]:
        c["volume"] = 15.0
        # indecisive body: close ~ open within a tall range
        mid = (c["open"] + c["close"]) / 2
        c["close"] = c["open"] + 0.02
        c["high"], c["low"] = mid + 1.0, mid - 1.0
    out = calculate_volume_signal(cs)
    assert out["signal"] == "bullish", out
    assert out["ratio"] >= 1.4


def test_equal_levels_include_newest_closed_candle():
    cs = _vol_candles(30)
    # two equal highs: one 5 bars back, one on the NEWEST closed candle
    cs[-6]["high"] = 120.0
    cs[-1]["high"] = 120.02
    eq = detect_equal_levels(cs, window=25, tolerance=0.003)
    assert eq["eqh"] is not None
    assert eq["eqh"]["candles_ago"] == 0, \
        "the newest closed candle's touch must count (candles_ago 0)"


def test_accumulation_range_includes_newest_closed_candle():
    # tight flat range; the newest closed candle sets the range HIGH
    cs = []
    p = 100.0
    for i in range(25):
        cl = 100.0 + (0.05 if i % 2 == 0 else -0.05)
        cs.append({"timestamp": T0 + i * STEP, "open": p, "high": cl + 0.2,
                   "low": cl - 0.2, "close": cl, "volume": 10.0})
        p = cl
    cs[-1]["high"] = 101.5                    # range top set by newest closed bar
    acc = detect_accumulation_range(cs, window=20)
    assert acc["high"] == 101.5, "newest closed candle must be inside the window"


# ── 9. MACD: signal-line cross vs centerline cross are independent ─────────────
from indicators import calculate_macd                                   # noqa: E402


def _mirror(closes):
    return [200.0 - c for c in closes]


def _macd_fixtures():
    base = [100 + i * 0.5 for i in range(40)]
    sig_only = base + [base[-1] - 0.5 * (i + 1) for i in range(10)] + [base[-1] + 10.0]
    dec = [100 - i * 0.8 for i in range(40)]
    center_only = dec + [dec[-1] + 0.9 * (i + 1) for i in range(15)]
    flat = [100.0] * 40
    both = flat + [100 - 0.3 * (i + 1) for i in range(8)] + [flat[-1] - 2.4 + 10.0]
    neither = [100 + i * 0.2 for i in range(50)]
    return sig_only, center_only, both, neither


def test_macd_signal_cross_only():
    sig_only, *_ = _macd_fixtures()
    m = calculate_macd(sig_only)
    assert m["cross"] == "bullish" and m["zero_cross"] is None, m
    mm = calculate_macd(_mirror(sig_only))
    assert mm["cross"] == "bearish" and mm["zero_cross"] is None, mm


def test_macd_centerline_cross_only():
    _, center_only, *_ = _macd_fixtures()
    m = calculate_macd(center_only)
    assert m["zero_cross"] == "bullish" and m["cross"] is None, m
    mm = calculate_macd(_mirror(center_only))
    assert mm["zero_cross"] == "bearish" and mm["cross"] is None, mm


def test_macd_both_crosses_same_candle_scores_once():
    *_, both, _ = _macd_fixtures()
    m = calculate_macd(both)
    assert m["cross"] == "bullish" and m["zero_cross"] == "bullish", m
    mm = calculate_macd(_mirror(both))
    assert mm["cross"] == "bearish" and mm["zero_cross"] == "bearish", mm
    # OR-based scoring: both events on one candle score exactly the same as a
    # single signal-line cross (no double-count)
    a = {
        "symbol": "ETH", "timeframe": "1D", "candles": mk_candles(60, up=True),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
    }
    base = generate_signal(dict(a, macd={"histogram": 0.0, "cross": None}))
    one = generate_signal(dict(a, macd={"histogram": 0.5, "cross": "bullish",
                                        "zero_cross": None, "trend": "bullish"}))
    two = generate_signal(dict(a, macd={"histogram": 0.5, "cross": "bullish",
                                        "zero_cross": "bullish", "trend": "bullish"}))
    assert one["score"] == two["score"] != base["score"]


def test_macd_neither_cross():
    *_, neither = _macd_fixtures()
    m = calculate_macd(neither)
    assert m["cross"] is None and m["zero_cross"] is None, m
    mm = calculate_macd(_mirror(neither))
    assert mm["cross"] is None and mm["zero_cross"] is None, mm


# ── 8. recommendation reason selection ─────────────────────────────────────────
def test_recommendation_reasons_match_direction():
    pytest.importorskip("flask")
    import app

    # real signals from the strong mirror fixtures
    long_sig = generate_signal(_confluence_fixture(bull=True))
    short_sig = generate_signal(_confluence_fixture(bull=False))
    assert long_sig["direction"] == "LONG" and short_sig["direction"] == "SHORT"
    # generate_signal has no generic "reasons" field — the old read yielded []
    assert "reasons" not in long_sig

    long_r = app._rec_reasons(long_sig, "LONG")
    short_r = app._rec_reasons(short_sig, "SHORT")
    assert long_r, "LONG card must show reasons (old code always showed none)"
    assert short_r, "SHORT card must show reasons"
    assert long_r == long_sig["bullish_reasons"][:3]
    assert short_r == short_sig["bearish_reasons"][:3]
    assert len(long_r) <= 3 and len(short_r) <= 3, "display count preserved"

    # opposing-side reasons are never the primary card reasons
    mixed = {"bullish_reasons": ["bull A"], "bearish_reasons": ["bear A"]}
    assert app._rec_reasons(mixed, "LONG") == ["bull A"]
    assert app._rec_reasons(mixed, "SHORT") == ["bear A"]


# ── 7. options reason attribution (4-way matrix) ───────────────────────────────
def _options_fixture(long_side: bool, opts_bullish: bool):
    a = _confluence_fixture(bull=long_side)              # strong LONG / SHORT
    a["options_expiry"] = {
        "signal_pts": 10 if opts_bullish else -10,
        "bias": {"bias": "bullish" if opts_bullish else "bearish",
                 "in_window": True},
    }
    return a


def _opts_reason(sig):
    for lst, side in ((sig["bullish_reasons"], "bull"),
                      (sig["bearish_reasons"], "bear")):
        for r in lst:
            if "Options expiry pin" in r:
                return side, r
    return None, None


def test_options_long_bullish_aligned_in_bull_reasons():
    sig = generate_signal(_options_fixture(long_side=True, opts_bullish=True))
    assert sig["direction"] == "LONG"
    side, r = _opts_reason(sig)
    assert side == "bull" and "aligns with LONG" in r


def test_options_long_bearish_opposed_in_bear_reasons():
    sig = generate_signal(_options_fixture(long_side=True, opts_bullish=False))
    assert sig["direction"] == "LONG"
    side, r = _opts_reason(sig)
    assert side == "bear" and "opposes LONG" in r


def test_options_short_bearish_aligned_in_bear_reasons():
    sig = generate_signal(_options_fixture(long_side=False, opts_bullish=False))
    assert sig["direction"] == "SHORT"
    side, r = _opts_reason(sig)
    assert side == "bear" and "aligns with SHORT" in r, (side, r)


def test_options_short_bullish_opposed_in_bull_reasons():
    sig = generate_signal(_options_fixture(long_side=False, opts_bullish=True))
    assert sig["direction"] == "SHORT"
    side, r = _opts_reason(sig)
    assert side == "bull" and "opposes SHORT" in r, (side, r)


def test_options_adjustment_still_applied_exactly_once():
    base = generate_signal(_confluence_fixture(bull=True))
    sig = generate_signal(_options_fixture(long_side=True, opts_bullish=True))
    assert sig["options_applied"] is True
    assert sig["options_adjustment"] == 10
    assert sig["strength"] - base["strength"] == 10, "aligned magnitude unchanged"
    opp = generate_signal(_options_fixture(long_side=True, opts_bullish=False))
    assert opp["options_adjustment"] == -5, "opposed formula (-mag/2) unchanged"


# ── 6. VWAP cross detection ────────────────────────────────────────────────────
from indicators import calculate_vwap                                   # noqa: E402


def _vwap_candles(closes, vol=10.0):
    out = []
    for i, c in enumerate(closes):
        out.append({"timestamp": T0 + i * STEP, "open": c, "high": c + 0.2,
                    "low": c - 0.2, "close": c, "volume": vol})
    return out


def test_vwap_genuine_bullish_cross():
    # price sits below its VWAP, then the last candle closes decisively above
    closes = [100.0] * 20 + [99.0] * 5 + [103.0]
    out = calculate_vwap(_vwap_candles(closes), period=20)
    assert out["vwap_cross"] == "bullish"
    assert out["previous_vwap"] is not None


def test_vwap_genuine_bearish_cross():
    closes = [100.0] * 20 + [101.0] * 5 + [97.0]
    out = calculate_vwap(_vwap_candles(closes), period=20)
    assert out["vwap_cross"] == "bearish"


def test_vwap_drift_across_price_is_not_a_cross():
    # Price stays ABOVE its contemporaneous VWAP on both bars (no real cross),
    # but a heavy high-priced final bar JUMPS the current VWAP above the
    # previous close. The old code (prev_close vs CURRENT vwap) reported this
    # as a bullish cross: prev_close 100 ≤ vwap_now (~100.8) and close 101 >
    # vwap_now. Against contemporaneous VWAPs there is no cross.
    cs = _vwap_candles([99.0] * 18, vol=10.0)
    n = len(cs)
    cs.append({"timestamp": T0 + n * STEP, "open": 99.5, "high": 100.2,
               "low": 99.4, "close": 100.0, "volume": 10.0})   # prev bar
    cs.append({"timestamp": T0 + (n + 1) * STEP, "open": 100.5, "high": 106.0,
               "low": 100.4, "close": 101.0, "volume": 200.0})  # vwap-jumping bar
    out = calculate_vwap(cs, period=20)
    # sanity: the setup really does straddle — prev close above its own vwap,
    # while the current vwap sits between prev_close and close
    assert out["previous_vwap"] < 100.0 < out["vwap"] < 101.0, out
    assert out["vwap_cross"] is None, \
        f"VWAP jumping across a price that never crossed its own VWAP: {out}"


def test_vwap_mirror_symmetry():
    up = calculate_vwap(_vwap_candles([100.0] * 20 + [99.0] * 5 + [103.0]), period=20)
    dn = calculate_vwap(_vwap_candles([100.0] * 20 + [101.0] * 5 + [97.0]), period=20)
    assert up["vwap_cross"] == "bullish" and dn["vwap_cross"] == "bearish"


# ── 5. doji SHORT bias removed ─────────────────────────────────────────────────
from indicators import candle_direction, CANDLE_DOJI_TOL                # noqa: E402


def _dir_candle(o, c):
    return {"timestamp": T0, "open": o, "high": max(o, c) + 1, "low": min(o, c) - 1,
            "close": c, "volume": 10.0}


def test_candle_direction_helper():
    assert candle_direction(_dir_candle(100.0, 101.0)) == 1
    assert candle_direction(_dir_candle(100.0, 99.0)) == -1
    assert candle_direction(_dir_candle(100.0, 100.0)) == 0, "exact doji is neutral"
    # floating-point noise inside the tolerance is neutral in BOTH directions
    assert candle_direction(_dir_candle(100.0, 100.0 + 100.0 * CANDLE_DOJI_TOL * 0.5)) == 0
    assert candle_direction(_dir_candle(100.0, 100.0 - 100.0 * CANDLE_DOJI_TOL * 0.5)) == 0
    # legacy-compat: a degenerate candle can't crash
    assert candle_direction({"open": 0, "close": 5}) == 0


def test_backtest_candle_dirs_use_doji_neutral():
    candles = mk_candles(30, up=False)
    candles[-1]["close"] = candles[-1]["open"]           # newest closed = doji
    a = build_price_analysis(candles, "2H", "TESTX")
    assert a["candle_dirs"][-1] == 0, "a doji must not be classified bearish"


def _consistency_signal(dirs):
    a = {
        "symbol": "ETH", "timeframe": "1D", "candles": mk_candles(60, up=True),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": dirs,
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
        "macd": {"histogram": 0.0, "cross": "none"},
    }
    return generate_signal(a)["score"]


def test_candle_consistency_symmetric_and_doji_neutral():
    base = _consistency_signal([1, -1, 1, -1])           # mixed → 0 pts
    assert _consistency_signal([0, 0, 0, 0]) == base, "four dojis score zero"
    # (final scores pass through the engine's uniform lone-group damping, so we
    # assert exact bull/bear SYMMETRY and ordering rather than raw magnitudes)
    up3 = _consistency_signal([1, 1, 1, 0])              # 3 bull + doji → lean bull
    dn3 = _consistency_signal([-1, -1, -1, 0])           # exact bearish mirror
    assert up3 - base == base - dn3 > 0, (up3, dn3, base)
    up4 = _consistency_signal([1, 1, 1, 1])
    dn4 = _consistency_signal([-1, -1, -1, -1])
    assert up4 - base == base - dn4 > up3 - base, (up4, dn4, base)
    # dojis can never increase the bearish count: 2 bear + 2 doji is NOT
    # "4 bearish" — it stays neutral
    assert _consistency_signal([-1, -1, 0, 0]) == base


def test_quick_tf_dir_fallback_dojis_not_bearish(monkeypatch):
    pytest.importorskip("flask")
    import app

    # few candles → EMA branch skipped → candle-majority fallback.
    # 3 dojis + 1 bull among the recent window: old code counted dojis as
    # bearish (bear=3 ≥ threshold → SHORT); now they are neutral → NEUTRAL.
    candles = []
    p = 100.0
    for i in range(9):
        candles.append({"timestamp": T0 + i * STEP, "open": p, "high": p + 1,
                        "low": p - 1, "close": p, "volume": 10.0})   # dojis
    candles.append({"timestamp": T0 + 9 * STEP, "open": 100.0, "high": 102,
                    "low": 99, "close": 101.0, "volume": 10.0})      # 1 bull
    candles.append({"timestamp": T0 + 10 * STEP, "open": 101.0, "high": 102,
                    "low": 100, "close": 101.0, "volume": 10.0})     # live (dropped)

    class _Stub:
        def get_spot_klines(self, bs, interval, limit):
            return [dict(c) for c in candles]
        def aggregate_candles(self, cs, n):
            return cs
    monkeypatch.setattr(app, "client", _Stub())
    assert app._quick_tf_dir("BTC", "1D") == "NEUTRAL", \
        "doji-heavy window must not read as SHORT"


def test_choch_exception_confirmation_candle_not_a_pivot():
    # Uptrend swings (HH/HL), then the NEWEST closed candle closes below the
    # last swing low → bearish CHoCH confirmed BY the newest candle. Changing
    # that candle's wick must not move the broken level: it is intentionally
    # excluded from pivot construction (documented exception).
    cs = []
    # two clear swing lows (valleys with 3 lower-low neighbours each side):
    # V1 low 98.4 @ i3, V2 low 101.0 @ i9 — a HIGHER low (uptrend structure).
    pattern = [104, 103, 101, 99, 100.5, 102, 104, 103.5, 101.8, 101.6,
               103, 105, 106, 105.5, 105.2]
    for i, px in enumerate(pattern):
        cs.append({"timestamp": T0 + i * STEP, "open": px + 0.1, "high": px + 0.6,
                   "low": px - 0.6, "close": px, "volume": 10.0})
    # newest closed candle closes below the last higher swing low (101.0)
    cs.append({"timestamp": T0 + len(pattern) * STEP, "open": 105,
               "high": 105.2, "low": 99.8, "close": 100.0, "volume": 10.0})
    out = detect_choch(cs, window=3)
    assert out["signal"] == "bearish", out
    level = out["level"]
    # an extreme wick on the confirmation candle must NOT change the pivot set
    cs2 = [dict(c) for c in cs]
    cs2[-1]["low"] = 50.0
    out2 = detect_choch(cs2, window=3)
    assert out2["signal"] == "bearish" and out2["level"] == level, \
        "confirmation candle must never contribute its own pivot"
