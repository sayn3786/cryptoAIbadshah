"""The trending-coins study: the live 15m book with ADX >= 25 and what might
make it pay (score floor, ADX 30, 1H required, 2R target)."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_trend_compare as tc                                       # noqa: E402
from test_sweep_fvg_compare import b                                   # noqa: E402

M = tc.M15
H = tc.HOUR_MS


def test_two_r_target_keeps_a_farther_swing():
    c = {"direction": "LONG", "entry": 100.0, "sl": 99.0, "tp_targets": [101.5]}
    assert tc.with_target(c, 2.0)["tp_targets"] == [102.0]
    far = {**c, "tp_targets": [103.0]}
    assert tc.with_target(far, 2.0)["tp_targets"] == [103.0]
    short = {"direction": "SHORT", "entry": 100.0, "sl": 101.0, "tp_targets": [98.5]}
    assert tc.with_target(short, 2.0)["tp_targets"] == [98.0]
    assert tc.with_target(c, None) is c


class _Reg:
    def __init__(self, adx):
        self.a = adx

    def btc_chop(self, at):
        return None

    def coin_adx(self, sym, at):
        return self.a.get(sym)


def _sim(rec, candles, **kw):
    return {"taken": True, "net_pct": 1.0, "closed_at": rec["slot_ms"] + 2 * M, "outcome": "tp1",
            "fill": rec["entry"], "stop": rec["sl"], "r": 1.0, "slot_ms": rec["slot_ms"],
            "tp": rec["tp_targets"][0]}


def _scalp(sym, strength, h1):
    return {"symbol": sym, "slot_ms": 10 * H, "direction": "LONG", "strength": strength,
            "h1_dir": h1, "entry": 100.0, "sl": 99.0, "tp_targets": [101.5]}


def test_book_applies_each_variant():
    scalps = [_scalp("SOL", 78, "LONG"), _scalp("ETH", 86, "SHORT"), _scalp("ADA", 82, None)]
    m15 = {"SOL": [], "ETH": [], "ADA": []}
    reg = _Reg({"SOL": 27, "ETH": 35, "ADA": 22})
    run = lambda **kw: tc.book(scalps, m15, reg, simulate=_sim, **kw)          # noqa: E731
    base = run(min_adx=25, floor=75, need_h1=False, target_r=None)
    assert len(base["trades"]) == 2                                              # SOL, ETH
    assert base["skipped"] == 1                                                  # ADA: ADX 22
    assert len(run(min_adx=30, floor=75, need_h1=False, target_r=None)["trades"]) == 1   # ETH
    assert len(run(min_adx=25, floor=85, need_h1=False, target_r=None)["trades"]) == 1   # ETH 86
    h1 = run(min_adx=25, floor=75, need_h1=True, target_r=None)["trades"]
    assert len(h1) == 1                                                          # SOL only
    two = run(min_adx=25, floor=75, need_h1=True, target_r=2.0)["trades"]
    assert two[0]["tp"] == 102.0


def test_compare_and_render():
    random.seed(9)
    bars, p = [], 100.0
    for i in range(tc.sc.WINDOW + 4 * tc.sc.H1_WINDOW + 300):
        o = p
        p *= 1 + random.gauss(0.0004, 0.004)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
    end = int(bars[-1]["timestamp"]) + M
    read = lambda w, tf, s: {"direction": "LONG", "strength": 80, "entry": w[-1]["close"],  # noqa
                             "sl": w[-1]["close"] * 0.99, "tp_targets": [w[-1]["close"] * 1.01]}
    res = tc.compare({"ETH": bars}, start_ms=end - 2 * 24 * H, end_ms=end,
                     read_fn=read, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in tc.VARIANTS]
    assert tc.render_telegram(res).startswith("📈")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-trend) MODULE=scalp_trend_compare" in wf
