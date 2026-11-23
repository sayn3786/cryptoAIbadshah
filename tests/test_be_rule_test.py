import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import be_rule_test as t                                              # noqa: E402


def _b(lo, hi, close=None):
    return {"low": lo, "high": hi, "close": close if close is not None else (lo + hi) / 2}


def _path():
    # fills at 100, TP1 110 at hour 2, back to 100 at hour 4, then TP2 120 / TP3 130
    return [_b(99, 101)] * 1 + [_b(100, 105), _b(104, 111), _b(103, 106), _b(99.5, 104)] + \
           [_b(105, 121), _b(118, 131)] + [_b(120, 125)] * 200


def test_a_stops_at_entry_b_rides_on():
    a = t.walk("LONG", 100.0, 90.0, [110, 120, 130], _path(), "A")
    b = t.walk("LONG", 100.0, 90.0, [110, 120, 130], _path(), "B")
    assert a["result"] == "stop" and abs(a["r"] - (10 / 3 / 10 - t.COST * 100 / 10)) < 1e-9
    assert b["result"] == "target" and abs(b["r"] - (2.0 - t.COST * 100 / 10)) < 1e-9


def test_c_and_d():
    c = t.walk("LONG", 100.0, 90.0, [110, 120, 130], _path(), "C")
    assert c["result"] == "target"                      # entry stop only after TP2
    d = t.walk("LONG", 100.0, 90.0, [110, 120, 130], _path(), "D")
    assert d["result"] == "target"                      # halfway (95) was not touched


def test_not_filled_and_stats():
    assert t.walk("LONG", 100.0, 90.0, [110], [_b(101, 105)] * 200, "A")["result"] == "not_filled"
    evs = [{"ts": 0, "A": {"r": 1.0, "hours": 5, "result": "target"}},
           {"ts": 86_400_000, "A": {"r": -1.0, "hours": 5, "result": "stop"}}]
    s = t.stats(evs, "A")
    assert s["n"] == 2 and s["max_dd"] == 1.0 and s["stops"] == 1
