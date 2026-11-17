"""A with-trend high-chance pattern hits its target: reverse now?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import target_hit_test as th                                           # noqa: E402
import target_trend_test as tt                                         # noqa: E402

D = th.DAY_MS


def test_the_hit_carries_its_trend():
    c = [{"timestamp": k * D, "open": 300.0 - k * 0.5, "close": 300.0 - k * 0.5,
          "high": 302.0 - k * 0.5, "low": 298.0 - k * 0.5, "volume": 1} for k in range(400)]
    c[305] = {**c[305], "low": 120.0, "close": 140.0}                 # a falling market reaches 140

    def detect(window, tf):
        last = window[-1]
        if int(last["timestamp"]) == 300 * D:
            return [{"_kind": "reversal", "type": "double_top", "direction": "bearish", "confirmed": True,
                     "target": 140.0, "peak_level": 160.0, "break_ts": 300 * D}]
        return []
    evs = th.scan(c, "1D", 240, "X", detect_fn=detect)
    assert len(evs) == 1 and evs[0]["trend"] == "down" and evs[0]["with_trend"] is True
    assert evs[0]["high_chance"]


def _ev(with_trend, win_opp, trend="down", tf="1D", hc=True):
    w, l = {"result": "target", "r": 0.95}, {"result": "stop", "r": -1.05}
    return {"high_chance": hc, "with_trend": with_trend, "trend": trend, "tf": tf, "ts": 0,
            "same": l if win_opp else w, "opp": w if win_opp else l}


def test_only_with_trend_high_chance_hits_decide():
    evs = [_ev(True, k % 3 != 0) for k in range(60)] + [_ev(False, False) for _ in range(60)] \
        + [_ev(True, False, hc=False) for _ in range(60)]
    g = tt.groups(evs)
    assert len(g[tt.DECIDES]) == 60
    assert th.judge(g[tt.DECIDES])["verdict"] == "REVERSAL"


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", target-trend" in wf and "python -m target_trend_test --telegram" in wf
