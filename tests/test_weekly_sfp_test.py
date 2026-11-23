import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import weekly_sfp_test as t                                           # noqa: E402

DAY = 86_400_000
MON = 1514764800000          # 2018-01-01, a Monday


def _daily(n_weeks, sweep_week=None):
    rows = []
    for d in range(n_weeks * 7):
        w = d // 7
        lo, hi, cl = 99.0, 101.0, 100.0
        if w == sweep_week and d % 7 == 3:
            lo = 90.0                     # wick under every earlier low, the week closes back at 100
        rows.append({"timestamp": MON + d * DAY, "open": 100.0, "high": hi, "low": lo, "close": cl, "volume": 1.0})
    return rows


def test_bullish_sfp_found_on_its_week():
    got = t.scan(_daily(60, sweep_week=50), "X")
    sfp = got["sfp"]
    assert len(sfp) == 1 and sfp[0]["direction"] == "LONG" and sfp[0]["level"] == 99.0
    assert sfp[0]["ts"] == MON + 51 * 7 * DAY            # booked at the weekly close (next Monday)


def test_no_sfp_without_a_sweep():
    assert t.scan(_daily(60), "X")["sfp"] == []


def test_walk_stop_first():
    r = t.walk("LONG", 100.0, 10.0, [{"high": 120.0, "low": 80.0}])
    assert r["result"] == "stop"
