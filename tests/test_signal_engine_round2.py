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
