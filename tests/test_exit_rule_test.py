import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import exit_rule_test as ex                                          # noqa: E402
import reversal_1d_test as rv                                        # noqa: E402

D = rv.DAY_MS


def _c(k, lo, hi, c):
    return {"timestamp": k * D, "open": c, "high": hi, "low": lo, "close": c}


def test_opposite_signal_exits_at_the_close():
    after = [_c(1, 99, 101, 100.5), _c(2, 98.5, 100.5, 99)] + [_c(3 + k, 98.6, 99.5, 99) for k in range(20)]
    sigs = [None, {"direction": "SHORT", "strength": 64}] + [None] * 20
    t = ex.manage("LONG", 100.0, 2.0, after, sigs, "opp62")
    assert t["result"] == "early" and t["days"] == 2 and -0.4 < t["r"] < -0.3
    assert ex.manage("LONG", 100.0, 2.0, after, [None, {"direction": "SHORT", "strength": 55}] + [None] * 20,
                     "opp62")["result"] == "time"
    assert ex.manage("LONG", 100.0, 2.0, after, [None, {"direction": "SHORT", "strength": 55}] + [None] * 20,
                     "opp51")["result"] == "early"


def test_break_even_from_the_next_day():
    # day 1 reaches +0.5R (1.5 of 3.0 risk) then day 2 falls back through the entry
    after = [_c(1, 99.5, 101.6, 101), _c(2, 97.5, 101, 98)] + [_c(3 + k, 97.2, 99, 98) for k in range(20)]
    t = ex.manage("LONG", 100.0, 2.0, after, [None] * 22, "be_half")
    assert t["result"] == "breakeven" and t["r"] < 0 and t["r"] > -0.1        # entry less costs
    assert ex.manage("LONG", 100.0, 2.0, after, [None] * 22, "base")["result"] == "time"


def test_era_rule():
    def ev(base_r, v_r):
        return {"ts": 0, "base": {"result": "x", "r": base_r}, "opp62": {"result": "x", "r": v_r},
                "opp51": {"result": "x", "r": base_r}, "be_half": {"result": "x", "r": base_r}}
    evs = [ev(1.0, 1.0)] * 50 + [ev(-1.0, -0.5)] * 50                   # same wins, smaller losses
    e = ex.era(evs)
    assert e["passes"]["opp62"] and not e["passes"]["opp51"]
