"""Stoch RSI crosses on 1D: trade the cross or the opposite?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import stoch_cross_test as sc                                          # noqa: E402

D = sc.DAY_MS


def test_groups():
    bull = {"stoch": {"signal": "bull_cross_oversold"}, "sig": {"direction": "SHORT", "strength": 91}}
    assert sc.groups(bull) == [(sc.BULL, "LONG"), (sc.BULL_VS_SHORT, "LONG")]
    weak = {"stoch": {"signal": "bull_cross_oversold"}, "sig": {"direction": "SHORT", "strength": 50}}
    assert sc.groups(weak) == [(sc.BULL, "LONG")]
    bear = {"stoch": {"signal": "bear_cross_overbought"}, "sig": {"direction": "LONG", "strength": 70}}
    assert sc.groups(bear) == [(sc.BEAR, "SHORT"), (sc.BEAR_VS_LONG, "SHORT")]
    assert sc.groups({"stoch": {"signal": "oversold"}, "sig": {}}) == []


def test_scan_cooldown_and_both_sides():
    daily = [{"timestamp": k * D, "open": 100, "close": 100 + (k % 3), "high": 104, "low": 96,
              "volume": 1} for k in range(400)]

    def read(window, sym, fg):
        i = int(window[-1]["timestamp"]) // D
        return {"stoch": {"signal": "bull_cross_oversold" if i in (300, 310, 330) else None},
                "sig": {}}
    evs = sc.scan(daily, "X", {}, read_fn=read)
    assert [e["ts"] // D for e in evs] == [300, 330] and evs[0]["direction"] == "LONG"
    assert evs[0]["opp"]["result"] in ("target", "stop", "open")


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", stoch-cross" in wf and "python -m stoch_cross_test --telegram" in wf
