"""After a high-chance pattern hits its target: reversal or continuation?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import target_hit_test as th                                           # noqa: E402

D = th.DAY_MS


def test_high_chance_set():
    assert th.high_chance("double top", "1D") and th.high_chance("inverse head & shoulders", "1D")
    assert th.high_chance("bear flag", "4H") and th.high_chance("falling wedge", "1W")
    assert not th.high_chance("bull flag", "4H") and not th.high_chance("double top", "4H")
    assert not th.high_chance("falling wedge", "4H")


def _bars(n=300):
    return [{"timestamp": k * D, "open": 100.0, "close": 100.0, "high": 102.0, "low": 98.0,
             "volume": 1} for k in range(n)]


def test_event_on_the_candle_that_reaches_the_target():
    c = _bars()
    c[205] = {**c[205], "high": 111.0, "close": 110.0}          # reaches 110 five candles later

    def detect(window, tf):
        last = window[-1]
        if int(last["timestamp"]) == 200 * D:
            return [{"_kind": "flag", "direction": "bullish", "confirmed": True, "target": 110.0,
                     "flag_low": 95.0, "flag_high": 100.0, "breakout_ts": 200 * D}]
        return []
    evs = th.scan(c, "1D", 240, "X", detect_fn=detect)
    assert len(evs) == 1 and evs[0]["ts"] == 205 * D and evs[0]["type"] == "bull flag"
    assert not evs[0]["high_chance"] and evs[0]["flag"]
    assert evs[0]["same"]["result"] in ("target", "stop", "open", "pending")


def test_judge_rule():
    def ev(win_same):
        w = {"result": "target", "r": 0.95}
        l = {"result": "stop", "r": -1.05}
        return {"same": w if win_same else l, "opp": l if win_same else w}
    rev = [ev(k % 3 == 0) for k in range(150)]                  # opposite wins 67%
    assert th.judge(rev)["verdict"] == "REVERSAL"
    cont = [ev(k % 3 != 0) for k in range(150)]
    assert th.judge(cont)["verdict"] == "CONTINUATION"
    assert th.judge(rev[:20])["verdict"] == "too few"
    flat = [ev(k % 2 == 0) for k in range(150)]
    assert th.judge(flat)["verdict"] == "no edge"


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", target-hit" in wf and "python -m target_hit_test --telegram" in wf
