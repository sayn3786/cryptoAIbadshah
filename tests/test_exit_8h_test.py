import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import exit_8h_test as e8                                            # noqa: E402

D, H8 = e8.DAY_MS, e8.H8_MS


def _b(t, lo, hi, c):
    return {"timestamp": t, "open": c, "high": hi, "low": lo, "close": c}


def test_walk_variants():
    t0 = 100 * D
    bars = [_b(t0 + k * H8, 99, 101, 100 - 0.1 * k) for k in range(e8.HOLD_BARS)]
    opp = {"direction": "SHORT", "strength": 70}
    h8 = lambda k: opp if k == 1 else None                      # noqa: E731  8H flips on the 2nd bar
    dsig = lambda end: opp if end == t0 + D else None           # noqa: E731  1D flips at the first close
    a = e8.walk("LONG", 100.0, 2.0, bars, "8h", dsig, h8)
    b = e8.walk("LONG", 100.0, 2.0, bars, "daily", dsig, h8)
    c = e8.walk("LONG", 100.0, 2.0, bars, "none", dsig, h8)
    assert a["result"] == "early" and b["result"] == "early" and c["result"] == "time"
    assert a["r"] > b["r"]                                       # out sooner, less given back


def test_half_rule():
    def ev(d_r, h_r):
        return {"ts": 0, "none": {"result": "x", "r": d_r}, "daily": {"result": "x", "r": d_r},
                "8h": {"result": "x", "r": h_r}}
    assert e8.half([ev(1.0, 1.0)] * 60 + [ev(-1.0, -0.8)] * 60)["8h_better"]
    assert not e8.half([ev(1.0, 1.0)] * 40 + [ev(-1.0, -0.8)] * 40)["8h_better"]      # under 100 trades
    assert not e8.half([ev(1.0, 0.5)] * 50 + [ev(-1.0, -0.8)] * 50)["8h_better"]


def test_scan_runs_with_fake_signals():
    days = [_b(k * D, 99, 101, 100) for k in range(60)]
    h8 = [_b(k * H8, 99, 101, 100) for k in range(60 * 3)]
    res = e8.scan(days, h8, "X", {}, d_fn=lambda w, s, f: {"direction": "LONG", "strength": 70},
                  h_fn=lambda w, s, f: {"direction": "SHORT", "strength": 70}, d_lookback=10, h_lookback=10)
    assert res and all(r["8h"]["result"] == "early" for r in res)
