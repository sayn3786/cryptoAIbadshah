"""Does a retest confirm a range break?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import range_retest_test as rt                                         # noqa: E402
from test_ranges import bar, trend_then_range                          # noqa: E402

D = rt.DAY_MS


def test_plain_break_on_the_breaking_close():
    got = rt.events_at(trend_then_range([196]), D, set())
    assert got == [(rt.PLAIN, "SHORT")]


def test_retest_held_needs_a_touch_of_the_edge_and_a_close_outside():
    seen = set()
    held = trend_then_range([196, 194])
    held[-1] = bar(51, 196, 197, h=201.5, lo=194)          # wick back near 202, closes below
    assert rt.events_at(held, D, seen) == [(rt.RETEST, "SHORT")]
    assert rt.events_at(held, D, seen) == []                # only the first retest of a break
    no_touch = trend_then_range([196, 194])
    no_touch[-1] = bar(51, 196, 193, h=197, lo=192)        # never came back to the edge
    assert rt.events_at(no_touch, D, set()) == []


def test_period_rule():
    def ev(fam, win):
        w = {"result": "target", "r": 0.95}
        l = {"result": "stop", "r": -1.05}
        return {"family": fam, "with": w if win else l, "opp": l if win else w}
    good = [ev(rt.RETEST, k % 3 != 0) for k in range(120)] + [ev(rt.PLAIN, k % 2 == 0) for k in range(120)]
    assert rt.period(good)["passes"]
    close = [ev(rt.RETEST, k % 3 != 0) for k in range(120)] + [ev(rt.PLAIN, k % 3 != 0) for k in range(120)]
    assert not rt.period(close)["passes"]                   # no better than a plain break


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", range-retest" in wf and "python -m range_retest_test --telegram" in wf
