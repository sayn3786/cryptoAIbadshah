"""1W: -10 strength when last week's 62+ signal saw its TP1 reached in the
week that just closed and the new signal points the same way
(tp_reached_test, passed on 1W, Nov 22 2026)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import signals                                                       # noqa: E402


def _weeks(n, last_low):
    return [{"timestamp": k * 7 * 86_400_000, "open": 100, "high": 101,
             "low": last_low if k == n - 1 else 99, "close": 100, "volume": 1} for k in range(n)]


def test_prev_tp1_reached():
    sig = {"direction": "SHORT", "strength": 70, "tp_targets": [95, 90]}
    hit = appmod._prev_tp1_reached(_weeks(80, 94), "BTC", signal_fn=lambda w: sig)
    assert hit == {"direction": "SHORT", "tp1": 95}
    assert appmod._prev_tp1_reached(_weeks(80, 96), "BTC", signal_fn=lambda w: sig) is None
    assert appmod._prev_tp1_reached(_weeks(80, 94), "BTC",
                                    signal_fn=lambda w: {**sig, "strength": 55}) is None
    assert appmod._prev_tp1_reached(_weeks(40, 94), "BTC", signal_fn=lambda w: sig) is None


_CACHE = {}


def _base():
    # a real weekly analysis of a steady downtrend, so the engine gives a direction
    if "a" not in _CACHE:
        import candle_analysis
        px, cs = 100.0, []
        for k in range(150):
            o = px
            px *= 0.985 if k % 5 else 1.01
            cs.append({"timestamp": k * 7 * 86_400_000, "open": o, "high": max(o, px) * 1.01,
                       "low": min(o, px) * 0.99, "close": px, "volume": 1000 + k})
        _CACHE["a"] = candle_analysis.build_candle_analysis(cs, "1W", "BTC")
    return dict(_CACHE["a"])


def test_penalty_applies_only_same_direction():
    plain = signals.generate_signal(_base())
    d = plain["direction"]
    assert d in ("LONG", "SHORT") and plain["strength"] >= 10
    opp = "LONG" if d == "SHORT" else "SHORT"
    same = signals.generate_signal({**_base(), "prev_tp1_reached": {"direction": d, "tp1": 60000}})
    other = signals.generate_signal({**_base(), "prev_tp1_reached": {"direction": opp, "tp1": 1}})
    assert same["strength"] == max(0, plain["strength"] - 10)
    assert other["strength"] == plain["strength"]
    side = "bullish_reasons" if d == "SHORT" else "bearish_reasons"
    assert any("already reached its TP1" in r for r in same.get(side, []))


def test_wired_for_calendar_weekly_only():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    assert 'if timeframe == "1W" and not business:' in src
