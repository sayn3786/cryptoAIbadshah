import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import combo_exit_test as t                                           # noqa: E402


def _b(lo, hi, close):
    return {"low": lo, "high": hi, "close": close}


AFTER = [_b(99, 101, 100), _b(97, 100, 98), _b(96, 99, 97)] + [_b(99, 101, 100)] * 30


def test_short_closes_when_bullish_alerts_fire():
    alerts = lambda k: {"bullish": 3 if k == 1 else 0, "bearish": 0}      # noqa: E731
    r = t.manage("SHORT", 100.0, 10.0, AFTER, [], alerts, 3)
    assert r["result"] == "alerts" and r["days"] == 2
    assert t.manage("SHORT", 100.0, 10.0, AFTER, [], alerts, None)["result"] == "time"


def test_bearish_alerts_do_not_close_a_short():
    alerts = lambda k: {"bullish": 0, "bearish": 3}                       # noqa: E731
    assert t.manage("SHORT", 100.0, 10.0, AFTER, [], alerts, 2)["result"] == "time"


def test_saved_is_paired_on_closed_trades():
    evs = [{"base": {"r": -1.0, "result": "stop"}, "all3": {"r": -0.2, "result": "alerts"}},
           {"base": {"r": 1.0, "result": "target"}, "all3": {"r": 0.4, "result": "alerts"}},
           {"base": {"r": 1.0, "result": "target"}, "all3": {"r": 1.0, "result": "target"}}]
    s = t.saved(evs, "all3")
    assert s["n"] == 2 and s["mean"] == 0.1
