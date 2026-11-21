import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_1d_test as rv                                        # noqa: E402
import tp_reached_test as tt                                         # noqa: E402

D = rv.DAY_MS


def test_reached():
    s = {"direction": "SHORT", "strength": 70, "tp_targets": [95, 90]}
    assert tt.reached(s, {"high": 101, "low": 94}) == {"tp1": True, "last": False}
    assert tt.reached(s, {"high": 101, "low": 89}) == {"tp1": True, "last": True}
    assert tt.reached({**s, "strength": 50}, {"high": 101, "low": 80}) == {"tp1": False, "last": False}
    assert tt.reached({"direction": "LONG", "strength": 70, "tp_targets": [105]},
                      {"high": 106, "low": 99})["tp1"]


def _daily(n):
    return [{"timestamp": i * D, "open": 100, "high": 101, "low": 99 if i % 2 else 94,
             "close": 100} for i in range(n)]


def test_scan_groups():
    daily = _daily(rv.LOOKBACK + 60)
    sig = {"direction": "SHORT", "strength": 70, "tp_targets": [95, 90]}
    evs = tt.scan(daily, "X", {}, signal_fn=lambda w, s, f: sig)
    groups = {e["group"] for e in evs}
    assert "after TP1" in groups and "other" in groups and "after last TP" not in groups


def test_era_checks():
    def ev(g, win, n):
        return [{"group": g, "x": {"result": "target" if k < win else "stop",
                                   "r": 0.9 if k < win else -1.1}} for k in range(n)]
    e = tt.era(ev("after TP1", 15, 40) + ev("other", 22, 40))
    assert e["passes"]
    e2 = tt.era(ev("after TP1", 21, 40) + ev("other", 22, 40))
    assert not e2["passes"]
