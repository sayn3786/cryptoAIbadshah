"""
Regression tests for the engulfing closed-candle contract.

detect_engulfing() must check the NEWEST closed candle (candles[-1]) against
its prior candle (candles[-2]). An earlier version sliced candles[:-1]
internally; since production (app.build_analysis), the engulf-alert scanner,
and the backtest all pass already-closed candles, that skipped the newest
completed candle and reported engulfings one bar late.

Synthetic OHLC only; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import detect_engulfing                                 # noqa: E402
from backtest import build_price_analysis                              # noqa: E402

STEP = 3_600_000
T0 = 1_000_000


def _c(i, o, cl, half=0.2):
    return {"timestamp": T0 + i * STEP, "open": o, "high": max(o, cl) + half,
            "low": min(o, cl) - half, "close": cl, "volume": 100.0}


def _flat(n, price=100.0):
    return [_c(i, price + (0.1 if i % 2 == 0 else -0.1),
               price + (-0.1 if i % 2 == 0 else 0.1)) for i in range(n)]


def test_engulfing_on_newest_closed_candle_is_detected():
    # prev candle bearish 101→100, newest CLOSED candle bullish 99.8→101.5
    # (opens below prev close, closes above prev open, bigger body) → bullish
    # engulfing ON THE NEWEST closed candle.
    candles = _flat(10)
    n = len(candles)
    candles.append(_c(n,     101.0, 100.0))     # prev: bearish
    candles.append(_c(n + 1,  99.8, 101.5))     # newest closed: engulfs it
    found = detect_engulfing(candles)
    assert any(p["direction"] == "bullish" and p["candles_ago"] == 1
               for p in found), \
        "the newest CLOSED candle's engulfing must be detected (no internal [:-1])"
    # and it references the correct candle
    hit = [p for p in found if p["direction"] == "bullish"][0]
    assert hit["timestamp"] == candles[-1]["timestamp"]


def test_engulfing_bearish_on_newest_closed_candle():
    candles = _flat(10)
    n = len(candles)
    candles.append(_c(n,     100.0, 101.0))     # prev: bullish
    candles.append(_c(n + 1, 101.2,  99.5))     # newest closed: bearish engulf
    found = detect_engulfing(candles)
    assert any(p["direction"] == "bearish" and p["candles_ago"] == 1
               for p in found)


def test_engulfing_not_detected_one_bar_late():
    # The engulfing pair sits at [-3]/[-2]; the newest closed candle [-1] is a
    # small doji-ish bar. candles_ago must be 2 (one bar back), NOT 1 — proving
    # the function indexes from the true newest closed candle.
    candles = _flat(10)
    n = len(candles)
    candles.append(_c(n,     101.0, 100.0))     # prev: bearish
    candles.append(_c(n + 1,  99.8, 101.5))     # engulfing candle
    candles.append(_c(n + 2, 101.4, 101.5))     # newest closed: tiny bull bar
    found = detect_engulfing(candles)
    assert not any(p["candles_ago"] == 1 for p in found), \
        "no engulfing on the actual newest closed candle here"


def test_backtest_path_sees_newest_closed_engulfing():
    # build_price_analysis passes its closed slice straight through — the
    # newest closed candle's engulfing must appear in the analysis dict.
    candles = _flat(40)
    n = len(candles)
    candles.append(_c(n,     101.0, 100.0))
    candles.append(_c(n + 1,  99.8, 101.5))
    a = build_price_analysis(candles, "1W", "TESTX")
    assert any(p["direction"] == "bullish" and p["candles_ago"] == 1
               for p in a["engulfing"])
