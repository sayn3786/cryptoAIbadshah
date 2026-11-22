import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import amdx_test as ax                                               # noqa: E402

M = ax.M15


def _b(t, o, h, l, c):
    return {"timestamp": t, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def test_x_trade_paths():
    bars = [_b(0, 100, 100, 100, 100), _b(M, 100, 100.5, 98.9, 99)]
    assert ax.x_trade(bars, 0, 1, "SHORT", 100, 1.0)["result"] == "target"
    assert ax.x_trade(bars, 0, 1, "LONG", 100, 1.0)["result"] == "stop"
    flat = [_b(0, 100, 100, 100, 100), _b(M, 100, 100.2, 99.8, 100.2)]
    r = ax.x_trade(flat, 0, 1, "LONG", 100, 1.0)
    assert r["result"] == "open" and 0.06 < r["r"] < 0.08          # +0.20R less 0.13R of costs


def test_scan_needs_a_real_distribution_and_the_windows(monkeypatch):
    day = 20_000 * ax.DAY_MS
    bars = [_b(day + k * M, 100, 100.1, 99.9, 100) for k in range(96)]
    i16 = 16 * 4 - 1
    bars[i16] = _b(day + i16 * M, 100, 100, 98, 98.5)               # distribution down by 16:00
    for k in range(i16 + 1, 96):
        bars[k] = _b(day + k * M, 98.5, 99.4, 98.4, 99.3)            # NY afternoon bounces
    setup = {"slot_ms": day + 8 * 3_600_000, "direction": "SHORT", "entry": 100.0,
             "asia_hi": 100.5, "asia_lo": 99.5}
    monkeypatch.setattr(ax.al, "setups", lambda sym, b, start_ms=0: [setup])
    evs = ax.scan("ETH", bars)
    assert len(evs) == 1 and evs[0]["reversal"]["result"] == "target"
    assert evs[0]["continuation"]["result"] == "stop"
    monkeypatch.setattr(ax.al, "setups", lambda sym, b, start_ms=0: [{**setup, "entry": 98.8}])
    assert ax.scan("ETH", bars) == []                               # moved < half the Asia range


def test_summary_rule():
    def ev(ts, rev_win):
        w = {"result": "target", "r": 0.98}
        lo = {"result": "stop", "r": -1.02}
        return {"ts": ts, "reversal": w if rev_win else lo, "continuation": lo if rev_win else w}
    evs = [ev(1, k % 10 < 6) for k in range(200)] + [ev(10, k % 10 < 6) for k in range(200)]
    assert ax.summarize(evs, 5)["answer"] == "X REVERSES"
    evs2 = [ev(1, k % 10 < 6) for k in range(200)] + [ev(10, k % 2 == 0) for k in range(200)]
    assert ax.summarize(evs2, 5)["answer"] == "no edge"
