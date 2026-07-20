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
