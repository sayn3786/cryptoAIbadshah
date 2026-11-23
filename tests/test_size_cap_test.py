import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import size_cap_test as t                                             # noqa: E402

DAY = 86_400_000
T0 = 1763942400000


def _ev(i, d, r, days=5):
    return {"ts": T0 + i * 1000, "direction": d, "width": 1.5,
            "base": {"result": "x", "r": r, "pct": r, "days": days}}


def test_soft_cap_sizes():
    evs = [_ev(i, "SHORT", 1.0) for i in range(6)]
    w = [x["w"] for x in t.sized(evs, "soft", 3)]
    assert w == [1.0, 1.0, 1.0, 0.75, 0.6, 0.5]


def test_hard_cap_skips_and_other_side_unaffected():
    evs = [_ev(i, "SHORT", 1.0) for i in range(5)] + [_ev(9, "LONG", 1.0)]
    rows = t.sized(evs, "hard", 3)
    assert sum(x["direction"] == "SHORT" for x in rows) == 3 and any(x["direction"] == "LONG" for x in rows)


def test_book_weights_r():
    evs = [_ev(i, "SHORT", -1.0) for i in range(4)]
    b = t.book(t.sized(evs, "soft", 3))
    assert b["total_r"] == -3.8 and b["risk_units"] == 3.8 and b["avg_r"] == -1.0
