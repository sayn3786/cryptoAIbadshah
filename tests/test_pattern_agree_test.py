"""Should a high-chance pattern push the 1D signal its way?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pattern_agree_test as pa                                        # noqa: E402

D = pa.DAY_MS


def test_only_a_reversal_pattern_confirming_that_day():
    p = {"_kind": "reversal", "type": "double_top", "direction": "bearish", "confirmed": True,
         "break_ts": 5 * D}
    assert pa.confirmed_today([p], 5 * D) == "SHORT"
    assert pa.confirmed_today([p], 6 * D) is None
    assert pa.confirmed_today([{**p, "_kind": "flag"}], 5 * D) is None
    assert pa.confirmed_today([{**p, "type": "falling_wedge"}], 5 * D) is None


def test_groups_and_the_20_day_window():
    daily = [{"timestamp": k * D, "open": 100, "close": 100 + (k % 3), "high": 104, "low": 96,
              "volume": 1} for k in range(400)]

    def detect(window, tf):
        ts = int(window[-1]["timestamp"])
        return [{"_kind": "reversal", "type": "double_top", "direction": "bearish", "confirmed": True,
                 "break_ts": ts}] if ts == 300 * D else []

    def sig(window, sym, fg):
        i = int(window[-1]["timestamp"]) // D
        return {305: {"direction": "SHORT", "strength": 70}, 310: {"direction": "LONG", "strength": 70},
                330: {"direction": "SHORT", "strength": 70}}.get(i, {})
    evs = pa.scan(daily, "X", {}, signal_fn=sig, detect_fn=detect)
    assert [(e["ts"] // D, e["group"]) for e in evs] == [(305, pa.AGREES), (310, pa.OPPOSES), (330, pa.NONE)]


def test_era_rule():
    def ev(g, win):
        return {"group": g, "trade": {"result": "target", "r": 0.95} if win else {"result": "stop", "r": -1.05}}
    good = [ev(pa.AGREES, k % 3 != 0) for k in range(120)] + [ev(pa.OPPOSES, k % 2 == 0) for k in range(60)]
    assert pa.era(good)["passes"]
    few_opp = [ev(pa.AGREES, k % 3 != 0) for k in range(120)] + [ev(pa.OPPOSES, False) for _ in range(10)]
    assert not pa.era(few_opp)["passes"]
    close = [ev(pa.AGREES, k % 3 != 0) for k in range(120)] + [ev(pa.OPPOSES, k % 3 != 0) for k in range(60)]
    assert not pa.era(close)["passes"]


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", pattern-agree" in wf and "python -m pattern_agree_test --telegram" in wf
