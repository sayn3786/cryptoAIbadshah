"""
RSI swing markers — 'this marked a bottom/top' circles.

A price swing LOW where RSI was oversold is circled green; a swing HIGH where RSI
was overbought is circled red. Requiring BOTH a real price pivot and a momentum
extreme is what keeps these to genuine turning points, not every 30/70 tag.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from candle_analysis import rsi_swing_markers                          # noqa: E402


def _c(ts, lo, hi):
    return {"timestamp": ts, "low": lo, "high": hi, "close": (lo + hi) / 2}


def test_a_swing_low_with_oversold_rsi_is_a_green_bottom():
    lows = [10, 9, 8, 7, 5, 7, 8, 9, 10]      # i=4 is the swing low
    candles = [_c(i, lows[i], lows[i] + 2) for i in range(9)]
    rsi = [50, 50, 50, 50, 35, 50, 50, 50, 50]
    m = rsi_swing_markers(candles, rsi, window=3)
    assert len(m) == 1
    assert m[0]["kind"] == "oversold_bottom"
    assert m[0]["rsi"] == 35 and m[0]["price"] == 5 and m[0]["timestamp"] == 4


def test_a_swing_high_with_overbought_rsi_is_a_red_top():
    highs = [10, 11, 12, 13, 15, 13, 12, 11, 10]   # i=4 is the swing high
    candles = [_c(i, highs[i] - 2, highs[i]) for i in range(9)]
    rsi = [50, 50, 50, 50, 66, 50, 50, 50, 50]
    m = rsi_swing_markers(candles, rsi, window=3)
    assert len(m) == 1
    assert m[0]["kind"] == "overbought_top"
    assert m[0]["rsi"] == 66 and m[0]["price"] == 15


def test_a_swing_low_without_oversold_rsi_is_not_marked():
    lows = [10, 9, 8, 7, 5, 7, 8, 9, 10]
    candles = [_c(i, lows[i], lows[i] + 2) for i in range(9)]
    rsi = [50] * 9                              # never oversold
    assert rsi_swing_markers(candles, rsi, window=3) == []


def test_since_ts_filters_older_markers():
    lows = [10, 9, 8, 7, 5, 7, 8, 9, 10]
    candles = [_c(i, lows[i], lows[i] + 2) for i in range(9)]
    rsi = [50, 50, 50, 50, 35, 50, 50, 50, 50]
    assert rsi_swing_markers(candles, rsi, window=3, since_ts=5) == []   # bottom at ts=4 dropped
    assert len(rsi_swing_markers(candles, rsi, window=3, since_ts=4)) == 1


def test_none_rsi_values_are_skipped():
    lows = [10, 9, 8, 7, 5, 7, 8, 9, 10]
    candles = [_c(i, lows[i], lows[i] + 2) for i in range(9)]
    rsi = [None] * 9
    assert rsi_swing_markers(candles, rsi, window=3) == []
