import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_timing_test as t                                     # noqa: E402

DAY = 86_400_000
MON = 1763942400000          # 2025-11-24, a Monday


def _bars(prices):
    return [{"timestamp": MON + i * DAY, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p}
            for i, p in enumerate(prices)]


def test_finds_a_major_bottom_on_its_day():
    prices = [100 - i for i in range(40)] + [61 + 3 * i for i in range(40)]
    found = t.turns(_bars(prices))
    bottoms = [x for x in found if x["kind"] == "bottom"]
    assert len(bottoms) == 1 and bottoms[0]["i"] == 39


def test_weekday_and_month_helpers():
    assert t._wd(MON) == 0 and t._wd(MON - DAY) == 6
    assert t.near_month(MON + 7 * DAY)          # Dec 1
    assert not t.near_month(MON - 10 * DAY)     # Nov 14
    assert abs(t.month_chance(MON) - 5 / 30) < 1e-9


def test_part_a_counts_each_day_once():
    turns = [{"ts": MON, "kind": "bottom"}] * 5 + [{"ts": MON + 2 * DAY, "kind": "bottom"}]
    a = t.part_a(turns)
    assert a["recent"]["week"]["n"] == 2 and a["recent"]["week"]["hits"] == 1


def test_indicators_basic():
    closes = [float(x) for x in range(1, 60)]
    assert t.rsi(closes)[-1] == 100.0
    assert t.ema(closes, 20)[18] is None and t.ema(closes, 20)[19] == sum(range(1, 21)) / 20
