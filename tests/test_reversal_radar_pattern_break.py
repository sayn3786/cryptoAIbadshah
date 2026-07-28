"""
Reversal Radar: a FRESH, CONFIRMED counter-trend pattern break counts as a
topping/bottoming signal — but only when volume backed it. A break on weak
volume is far more likely to fail, so it must NOT fire.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import _reversal_radar, _counter_trend_break                # noqa: E402
from test_flag_pattern_correctness import _make_candles                  # noqa: E402


def _uptrend():
    cs = _make_candles(60, up=True)
    return {"candles": cs,
            "ema_trend": {"above": [50, 200], "below": [], "ema50": cs[-1]["close"] * 0.98},
            "supertrend": {"direction": "bullish"}}


def _downtrend():
    cs = _make_candles(60, up=False)
    return {"candles": cs,
            "ema_trend": {"above": [], "below": [50, 200], "ema50": cs[-1]["close"] * 1.02},
            "supertrend": {"direction": "bearish"}}


def _rev(a, direction, level, ratio, label="Double Top", fresh=True):
    x = dict(a)
    ts = a["candles"][-2]["timestamp"] if fresh else a["candles"][0]["timestamp"]
    x["reversal_patterns"] = [{"label": label, "direction": direction, "confirmed": True,
                               "break_ts": ts,
                               "breakout_volume": {"level": level, "ratio": ratio}}]
    return x


def _fired(rr):
    return [s for s in rr["signals"] if "pattern break" in s["label"].lower()]


def test_strong_volume_break_fires_topping_signal():
    rr = _reversal_radar(_rev(_uptrend(), "bearish", "strong", 2.4))
    hit = _fired(rr)
    assert hit and "strong volume" in hit[0]["note"]
    assert rr["mode"] == "top"


def test_normal_volume_break_fires():
    assert _fired(_reversal_radar(_rev(_uptrend(), "bearish", "normal", 1.1)))


def test_weak_volume_break_does_not_fire():
    assert not _fired(_reversal_radar(_rev(_uptrend(), "bearish", "weak", 0.6)))


def test_stale_break_does_not_fire():
    assert not _fired(_reversal_radar(_rev(_uptrend(), "bearish", "strong", 2.4, fresh=False)))


def test_same_direction_break_is_not_a_reversal_signal():
    # A BULLISH break during an uptrend is continuation, not a topping signal.
    assert not _fired(_reversal_radar(_rev(_uptrend(), "bullish", "strong", 2.4)))


def test_bottoming_mirror():
    rr = _reversal_radar(_rev(_downtrend(), "bullish", "strong", 3.0, label="Double Bottom"))
    hit = _fired(rr)
    assert rr["mode"] == "bottom"
    assert hit and "turning up" in hit[0]["note"]


def test_reversal_pattern_outranks_a_flag():
    a = _rev(_uptrend(), "bearish", "normal", 1.0, label="Head & Shoulders")
    a["flags"] = [{"direction": "bearish", "confirmed": True, "is_active": True,
                   "breakout_ts": a["candles"][-2]["timestamp"], "flag_slope": "ascending",
                   "breakout_volume": {"level": "strong", "ratio": 3.0}}]
    best = _counter_trend_break(a, "bearish")
    assert best["kind"] == "reversal" and best["label"] == "Head & Shoulders"


def test_no_patterns_returns_none():
    assert _counter_trend_break(_uptrend(), "bearish") is None
