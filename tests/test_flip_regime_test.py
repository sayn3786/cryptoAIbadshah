import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import flip_regime_test as f                                          # noqa: E402

DAY = 86_400_000
T = 1763942400000


def test_tag_with_and_against():
    evs = [{"ts": T + 5, "dir": "LONG"}, {"ts": T, "dir": "SHORT"}, {"ts": T + DAY, "dir": "LONG"}]
    got = f.tag(evs, {T: "bull"})
    assert [g["with"] for g in got] == [True, False]          # the day without a regime is dropped


def _e(win, with_, ts=T):
    return {"ts": ts, "dir": "LONG", "with": with_,
            "trade": {"result": "target" if win else "stop", "r": 1.0 if win else -1.0}}


def test_passes_needs_both_eras():
    old = f.rtt.ERA_SPLIT - DAY
    good = [_e(True, True)] * 40 + [_e(False, True)] * 20 + [_e(True, False)] * 10 + [_e(False, False)] * 10
    both = good + [{**x, "ts": old} for x in good]
    assert f.summarize(both)["passes"]
    assert not f.summarize(good)["passes"]                      # the older era has no trades
