import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import risk_shape_test as t                                           # noqa: E402

DAY = 86_400_000
T0 = 1763942400000


def _b(lo, hi, close=None):
    return {"low": lo, "high": hi, "close": close if close is not None else (lo + hi) / 2}


def test_ladder_thirds_then_breakeven():
    # long 100, stop 90, targets 110/120/130: TP1 on day 1, back to entry on day 2
    bars = [_b(99, 101), _b(100, 111), _b(99, 105)] + [_b(100, 101)] * 10
    r = t.trade("LONG", 100, 90, [110, 120, 130], bars)
    assert abs(r["r"] - (1 / 3 * 10 / 10 - t.COST * 100 / 10)) < 1e-9 and r["result"] == "be_or_win"


def test_stop_first_and_not_filled():
    assert t.trade("SHORT", 100, 110, [90], [_b(95, 111)] + [_b(99, 101)] * 9)["result"] == "stop"
    assert t.trade("LONG", 100, 90, [110], [_b(101, 105)] * 10)["result"] == "not_filled"
    assert t.trade("LONG", 100, 105, [110], [_b(99, 101)] * 10)["result"] == "skip"     # stop on the wrong side


def test_expired_at_market():
    r = t.trade("LONG", 100, 90, [150], [_b(99, 101, 100)] + [_b(100, 104, 103)] * 7)
    assert r["result"] == "expired" and abs(r["pct"] - (3 - 100 * t.COST)) < 1e-9


def _ev(ts, d, r, days=3, width=2.0):
    return {"ts": ts, "direction": d, "width": width, "base": {"result": "x", "r": r, "pct": r, "days": days}}


def test_cap_skips_crowded_trades():
    evs = [_ev(T0 + i * 1000, "SHORT", 1.0) for i in range(5)] + [_ev(T0, "LONG", -1.0)]
    kept = t.apply_cap(evs, 3)
    assert sum(e["direction"] == "SHORT" for e in kept) == 3 and len(kept) == 4


def test_curve_drawdown():
    evs = [_ev(T0, "LONG", 2.0, 1), _ev(T0 + DAY, "LONG", -3.0, 1), _ev(T0 + 2 * DAY, "LONG", 1.0, 1)]
    assert t.curve(evs) == {"max_dd": 3.0, "worst_day": -3.0}
