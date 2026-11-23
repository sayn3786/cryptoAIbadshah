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
    assert "renderDivWatch(a.rsi_live_watch || a.rsi_forming_watch, a.timeframe)" in js


def test_uses_the_swing_with_the_biggest_rsi_gap_not_only_the_lowest():
    """BTC 2W: June's 57.8k low has RSI ~38 (no gap), Feb's 60k has RSI 35 -
    the watch must be drawn from Feb, as Ted does."""
    import indicators
    closed = _series()
    real = indicators.calculate_rsi_series
    closes = [float(c["close"]) for c in closed]

    def fake(xs, period=14):
        r = real(xs, period)
        if len(xs) == len(closes):                  # the closed series: pin the two swing RSIs
            r = list(r)
            r[29], r[-3] = 35.2, 38.2
        else:
            r = list(r[:-1]) + [38.3]               # live RSI
        return r
    import live_divergence as ld2
    lows = [29, len(closed) - 3]
    closed[29]["low"], closed[-3]["low"] = 60000, 57809
    for k in range(len(closed) - 5, len(closed)):
        if k != len(closed) - 3:
            closed[k]["low"] = max(closed[k]["low"], 58500)
    orig = ld2._swings
    try:
        ld2._swings = lambda c, w=2: (lows, [])
        indicators.calculate_rsi_series = fake
        w = ld2.watch(closed, {"timestamp": 0, "open": 60000, "high": 61000, "low": 55100, "close": 59000})
    finally:
        indicators.calculate_rsi_series = real
        ld2._swings = orig
    assert w["prev_price"] == 60000 and w["prev_rsi"] == 35.2 and w["live_rsi"] == 38.3


def test_forming_on_closed_candles_from_the_biggest_gap_swing(monkeypatch):
    """BTC 2W after the Nov 9-23 candle closed: its low 55.1k (RSI 39.0) vs
    Feb 60k (RSI 35.3) - not vs June 57.8k (RSI 38.2, gap under 1)."""
    import indicators
    closed = _series()
    n = len(closed)
    feb, jun, last = 29, n - 6, n - 1
    closed[feb]["low"], closed[jun]["low"], closed[last]["low"] = 60000, 57809, 55134
    for k in range(jun + 1, last):
        closed[k]["low"] = max(closed[k]["low"], 59000)
    real = indicators.calculate_rsi_series

    def fake(xs, period=14):
        r = list(real(xs, period))
        r[feb], r[jun], r[last] = 35.3, 38.2, 39.0
        return r
    monkeypatch.setattr(indicators, "calculate_rsi_series", fake)
    monkeypatch.setattr(ld, "_swings", lambda c, w=2: ([feb, jun], []))
    w = ld.forming(closed, pw=2)
    assert w["kind"] == "forming" and w["type"] == "bullish"
    assert w["prev_price"] == 60000 and w["prev_rsi"] == 35.3 and w["live_rsi"] == 39.0
    assert w["closes_to_confirm"] == 2


def test_forming_shown_when_no_live_watch():
    root = os.path.join(os.path.dirname(__file__), "..")
    js = open(os.path.join(root, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert "renderDivWatch(a.rsi_live_watch || a.rsi_forming_watch, a.timeframe)" in js
    src = open(os.path.join(root, "backend", "app.py"), encoding="utf-8").read()
    assert '"rsi_forming_watch": _rsi_forming_watch(spot, timeframe)' in src


def test_subtitle_matches_the_kind():
    root = os.path.join(os.path.dirname(__file__), "..")
    js = open(os.path.join(root, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert "'unconfirmed swing · closed candles · not scored'" in js
    html = open(os.path.join(root, "dashboard", "index.html"), encoding="utf-8").read()
    assert 'id="divWatchSub"' in html
