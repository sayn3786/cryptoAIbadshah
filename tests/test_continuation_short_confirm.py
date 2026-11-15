"""Confirmation on fresh history: 1D SHORT 62+ into 'oversold' signs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import continuation_short_confirm as cs                                # noqa: E402

D = cs.DAY_MS


def _ev(ts, win, grp):
    r = {"result": "target" if win else "stop", "r": 0.95 if win else -1.05}
    o = {"result": "stop" if win else "target", "r": -1.05 if win else 0.95}
    return {"family": grp, "ts": ts, "with": r, "opp": o, "symbol": "X"}


def test_only_history_before_the_seen_window_counts():
    end = 5000 * D
    evs = [_ev(1000 * D, True, "g"), _ev(end - 10 * D, True, "g")]
    assert [e["ts"] for e in cs.fresh(evs, end - cs.SEEN_DAYS * D)] == [1000 * D]


def test_confirmed_needs_the_whole_period_and_both_halves():
    g = cs.GROUP["Stretched below EMA50"]
    good = [_ev(k * D, k % 3 != 0, g) for k in range(120)]             # 67% everywhere
    assert cs.judge(good, g)["confirmed"]
    lop = [_ev(k * D, (k % 3 != 0) if k >= 60 else (k % 3 == 0), g) for k in range(120)]
    j = cs.judge(lop, g)                                                # first half 33%
    assert not j["halves_ok"] and not j["confirmed"]
    assert not cs.judge(good[:20], g)["confirmed"]                     # too few


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", continuation-short" in wf and "python -m continuation_short_confirm --telegram" in wf
