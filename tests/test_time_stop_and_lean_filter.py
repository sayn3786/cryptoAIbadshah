import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import lean_filter_1d_test as lf                                      # noqa: E402
import time_stop_test as ts                                           # noqa: E402

DAY = 86_400_000


def _b(lo, hi, close):
    return {"low": lo, "high": hi, "close": close}


def test_time_stop_closes_a_trade_that_went_nowhere():
    # long 100, ATR 10 -> risk 15; +0.5R = 107.5 never touched in 3 days
    after = [_b(98, 104, 101), _b(97, 105, 99), _b(96, 106, 98)] + [_b(99, 101, 100)] * 30
    r = ts.manage("LONG", 100.0, 10.0, after, [], 3)
    assert r["result"] == "timestop" and r["days"] == 3
    assert ts.manage("LONG", 100.0, 10.0, after, [], None)["result"] == "time"


def test_time_stop_spares_a_trade_that_moved():
    after = [_b(99, 108, 103), _b(97, 105, 99), _b(96, 106, 98)] + [_b(99, 101, 100)] * 30
    assert ts.manage("LONG", 100.0, 10.0, after, [], 3)["result"] != "timestop"


def test_opposite_signal_exit_comes_first():
    after = [_b(98, 104, 101), _b(97, 105, 99)] + [_b(99, 101, 100)] * 30
    sigs = [{"direction": "SHORT", "strength": 70}]
    assert ts.manage("LONG", 100.0, 10.0, after, sigs, 2)["result"] == "early"


def test_lean_filter_books():
    ev = lambda d, ln: [b for b in lf.BOOKS if lf.slf.keep(b, d, ln)]         # noqa: E731
    assert ev("LONG", {"premium": "LONG", "stable": None}) == ["baseline", "no-against", "agree-only"]
    assert ev("LONG", {"premium": None, "stable": None}) == ["baseline", "no-against"]
    assert ev("LONG", {"premium": "SHORT", "stable": "LONG"}) == ["baseline"]


def test_lean_is_the_signal_days_own():
    day = 1_795_392_000_000
    assert lf.lean_on({"premium": {day: "LONG"}}, day + 5000) == {"premium": "LONG", "stable": None}
