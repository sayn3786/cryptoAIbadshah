"""
Regression tests for the spot-absorption divergence.

Price holding FLAT while spot and futures CVD pull hard in opposite directions
is absorption (one side soaking up the other's aggression). The old
detect_cvd_divergence bailed to a generic 'neutral / Price ranging' whenever
price moved < ±0.5%, so it never even looked at the CVD split — silent on the
most telling version of the signal. These tests cover the new
spot_absorption_bullish / _bearish detection and scoring.

Synthetic USD CVD; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import detect_cvd_divergence                            # noqa: E402
from signals import generate_signal                                     # noqa: E402

HOUR_MS = 3_600_000
T0 = 1_000_000


def _cvd_usd(deltas):
    s, c = [], 0.0
    for i, d in enumerate(deltas):
        c += d
        s.append({"timestamp": T0 + i * HOUR_MS, "cvd": round(c, 2), "delta": d})
    trend = "bullish" if sum(deltas) > 0 else "bearish" if sum(deltas) < 0 else "neutral"
    return {"current": round(c, 2), "trend": trend, "series": s, "unit": "usd"}


def _market(spot_deltas, fut_deltas, price_mode="flat", n=12, price=100.0):
    candles = []
    p = price
    for i in range(n):
        if price_mode == "flat":
            cl = price + (0.05 if i % 2 == 0 else -0.05)     # <0.5% over any 5
            o = price
        elif price_mode == "up":
            cl = p + 0.6
            o = p
        else:                                                # down
            cl = p - 0.6
            o = p
        candles.append({"timestamp": T0 + i * HOUR_MS, "open": o,
                        "high": max(o, cl) + 0.1, "low": min(o, cl) - 0.1,
                        "close": cl, "volume": 10.0})
        p = cl
    return _cvd_usd(spot_deltas), _cvd_usd(fut_deltas), candles


# ── detection ───────────────────────────────────────────────────────────────────
def test_spot_absorption_bullish_when_price_flat():
    # spot BUYS (+), futures SELL (−), price holds flat → absorption bullish
    spot, fut, cs = _market([40.0] * 12, [-60.0] * 12, price_mode="flat")
    d = detect_cvd_divergence(spot, fut, cs)
    assert d["type"] == "spot_absorption_bullish", d
    assert d["signal"] == "bullish"
    assert d["squeeze_risk"] == "short_squeeze_building"


def test_spot_absorption_bearish_when_price_flat():
    spot, fut, cs = _market([-40.0] * 12, [60.0] * 12, price_mode="flat")
    d = detect_cvd_divergence(spot, fut, cs)
    assert d["type"] == "spot_absorption_bearish", d
    assert d["signal"] == "bearish"
    assert d["squeeze_risk"] == "long_squeeze_building"


def test_flat_but_same_direction_is_not_absorption():
    # both bullish + price flat → no opposite divergence → plain neutral
    spot, fut, cs = _market([40.0] * 12, [40.0] * 12, price_mode="flat")
    d = detect_cvd_divergence(spot, fut, cs)
    assert d["type"] == "neutral" and d["label"] == "Price ranging"


def test_absorption_requires_ok_flow_quality():
    # unknown units → dominance_data_quality != ok → fall back to neutral,
    # never a false absorption read on a thin/unknown feed
    spot, fut, cs = _market([40.0] * 12, [-60.0] * 12, price_mode="flat")
    spot_unknown = dict(spot); spot_unknown["unit"] = "mystery"
    d = detect_cvd_divergence(spot_unknown, fut, cs)
    assert d["type"] == "neutral", d


def test_trending_divergence_unchanged_regression():
    # price UP + spot bullish + futures bearish → still spot_led_up (not absorption)
    spot, fut, cs = _market([40.0] * 12, [-60.0] * 12, price_mode="up")
    assert detect_cvd_divergence(spot, fut, cs)["type"] == "spot_led_up"
    # price DOWN + spot bullish + futures bearish → still futures_led_down
    spot, fut, cs = _market([40.0] * 12, [-60.0] * 12, price_mode="down")
    assert detect_cvd_divergence(spot, fut, cs)["type"] == "futures_led_down"


# ── scoring ──────────────────────────────────────────────────────────────────────
def _analysis(cvd_div=None):
    a = {
        "symbol": "TAO", "timeframe": "1D",
        "candles": [{"timestamp": T0 + i * HOUR_MS, "open": 100, "high": 100.3,
                     "low": 99.7, "close": 100 + (0.1 if i % 2 else -0.1),
                     "volume": 10.0} for i in range(60)],
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.0, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
        "macd": {"histogram": 0.0, "cross": "none"},
    }
    if cvd_div is not None:
        a["cvd_divergence"] = cvd_div
    return a


def test_absorption_scores_modestly_and_symmetric():
    base = generate_signal(_analysis())["score"]
    bull = generate_signal(_analysis(
        {"type": "spot_absorption_bullish", "signal": "bullish",
         "spot_ratio": 1.5, "futures_ratio": 1.5,
         "squeeze_risk": "short_squeeze_building"}))
    bear = generate_signal(_analysis(
        {"type": "spot_absorption_bearish", "signal": "bearish",
         "spot_ratio": 1.5, "futures_ratio": 1.5,
         "squeeze_risk": "long_squeeze_building"}))
    assert bull["score"] > base > bear["score"]
    assert (bull["score"] - base) == (base - bear["score"]), "mirror symmetric"
    assert any("Spot absorbing futures selling" in r for r in bull["bullish_reasons"])
    assert any("Spot distributing into futures buying" in r for r in bear["bearish_reasons"])


def test_absorption_scores_below_spot_led():
    # absorption (price unconfirmed) must be weaker than a trending spot-led move
    base = generate_signal(_analysis())["score"]
    absorb = generate_signal(_analysis(
        {"type": "spot_absorption_bullish", "signal": "bullish",
         "spot_ratio": 1.5, "futures_ratio": 1.5}))["score"] - base
    spot_led = generate_signal(_analysis(
        {"type": "spot_led_up", "signal": "bullish",
         "spot_ratio": 1.5, "futures_ratio": 1.5}))["score"] - base
    assert 0 < absorb < spot_led, (absorb, spot_led)
