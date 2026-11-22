"""Live RSI divergence WATCH on the forming candle (display only)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import live_divergence as ld                                         # noqa: E402

W = 14 * 86_400_000


def _c(i, lo, hi, c):
    return {"timestamp": i * W, "open": c, "high": hi, "low": lo, "close": c}


def _series():
    """A sharp drop to a 60k swing low (RSI deep), a bounce, then a slow drift
    back down: price lower now, RSI higher - Ted's 2W picture."""
    warm = [90 + (1 if k % 2 else -1) for k in range(20)]                  # RSI needs 14+ candles first
    px = warm + [90, 88, 86, 84, 82, 80, 76, 70, 64, 60, 62, 66, 70, 74, 76, 75, 73, 71, 69, 67,
                 66, 65, 64, 63]
    return [_c(i, p * 1000 - 1500, p * 1000 + 800, p * 1000) for i, p in enumerate(px)]


def test_bullish_watch_on_the_forming_candle():
    closed = _series()
    live = {"timestamp": len(closed) * W, "open": 63000, "high": 63500, "low": 56000, "close": 59000}
    w = ld.watch(closed, live)
    assert w and w["type"] == "bullish"
    assert w["live_price"] == 56000 and w["prev_price"] == 58500
    assert w["live_rsi"] > w["prev_rsi"] and "UNCONFIRMED" in w["note"]


def test_no_watch_without_a_lower_low_or_without_a_live_candle():
    closed = _series()
    assert ld.watch(closed, {"timestamp": 0, "open": 63000, "high": 64000, "low": 61000, "close": 63500}) is None
    assert ld.watch(closed, None) is None


def test_wired_display_only():
    root = os.path.join(os.path.dirname(__file__), "..")
    src = open(os.path.join(root, "backend", "app.py"), encoding="utf-8").read()
    assert '"rsi_live_watch": _rsi_live_watch(spot, live_candle)' in src
    sig = open(os.path.join(root, "backend", "signals.py"), encoding="utf-8").read()
    assert "rsi_live_watch" not in sig                                 # never scored
    js = open(os.path.join(root, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert "renderDivWatch(a.rsi_live_watch, a.timeframe)" in js
