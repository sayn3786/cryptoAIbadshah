"""The engulfing card: the last 4 closed candles and why the newest missed.
The score still reads only the newest candle."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import indicators as ind                                               # noqa: E402

D = 86_400_000


def c(i, o, cl):
    return {"timestamp": i * D, "open": o, "close": cl, "high": max(o, cl) + 1,
            "low": min(o, cl) - 1, "volume": 1.0}


def test_the_card_lists_the_last_four_and_the_score_the_newest():
    bars = [c(0, 100, 100.5), c(1, 104, 100), c(2, 99.5, 105),       # engulfing 3 candles ago
            c(3, 105, 106), c(4, 106, 107), c(5, 107, 108)]
    assert ind.detect_engulfing(bars) == []                           # score: newest only
    shown = ind.detect_engulfing(bars, lookback=4, keep_all=True)
    assert [p["candles_ago"] for p in shown] == [4]                   # the D2 candle, 4 back
    assert shown[0]["type"] == "bullish_engulfing"


def test_a_near_miss_says_why():
    # TAO-like: red 224 -> 218, then green 218.2 -> 223.9 (same size, just short)
    bars = [c(0, 230, 224), c(1, 224, 218), c(2, 218.2, 223.9)]
    assert ind.detect_engulfing(bars) == []
    nm = ind.engulfing_near_miss(bars)
    assert nm["direction"] == "bullish"
    assert "opened above the red candle's close" in nm["misses"]
    assert "closed below the red candle's open" in nm["misses"]
    assert any(m.startswith("body 0.95x") for m in nm["misses"])
    ok = [c(0, 230, 224), c(1, 224, 218), c(2, 217.5, 225)]
    assert ind.detect_engulfing(ok)[0]["type"] == "bullish_engulfing"
    assert ind.engulfing_near_miss(ok) is None                        # it IS one
    assert ind.engulfing_near_miss([c(0, 1, 2), c(1, 2, 3)]) is None  # same colour: no case
    bear = ind.engulfing_near_miss([c(0, 100, 104), c(1, 104, 101)])
    assert bear["direction"] == "bearish" and "closed above the green candle's open" in bear["misses"]


def test_the_dashboard_uses_the_display_list():
    js = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "js", "dashboard.js")).read()
    assert "renderEngulfing(a.engulfing_recent || a.engulfing, a.timeframe, a.engulfing_near_miss)" in js
    assert "the score counts only the newest candle" in js
