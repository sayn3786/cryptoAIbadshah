"""15m scalps with the engine's direction and structure exits, sweep/FVG as a
bonus or an alternative trigger, and risk-based sizing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_struct_compare as ss                                      # noqa: E402
import sweep_fvg as sf                                                 # noqa: E402
from test_sweep_fvg_compare import b, long_setup                        # noqa: E402

M = ss.M15


def test_structure_exit_uses_confirmed_swings_and_rr():
    bars = long_setup()
    piv = sf.pivots(bars)
    assert ss.structure_exit(bars, 19, "LONG", piv) is None           # close 104: R:R 1.3
    bars2 = bars + [b(20, 104, 104.5, 102.8, 103)]
    ex = ss.structure_exit(bars2, 20, "LONG", sf.pivots(bars2))
    # the latest confirmed swing low is the sweep's wick (99.5, confirmed at 19)
    assert ex and ex["target"] == 110 and abs(ex["sl"] - 99.5 * (1 - sf.STOP_BUFFER)) < 1e-9
    assert ss.structure_exit(bars2, 19, "LONG", sf.pivots(bars2), min_rr=5) is None


def _sim(rec, candles, **kw):
    return {"taken": True, "net_pct": 1.0, "closed_at": rec["slot_ms"] + M, "outcome": "tp1",
            "rr_at_fill": 2, "fill": rec["entry"], "stop": rec["sl"], "r": 0.5,
            "slot_ms": rec["slot_ms"]}


def _cand(sym, slot, d, s, h1="LONG"):
    return {"symbol": sym, "slot_ms": slot, "direction": d, "strength": s, "h1_dir": h1,
            "entry": 104.0, "sl": 103.0, "tp_targets": [105.0, 106.0]}


def test_variants_pick_the_right_trades():
    bars = long_setup() + [b(20, 104, 104.5, 102.8, 103)] + [b(21 + k, 104, 104.5, 103.8, 104)
                                                            for k in range(5)]
    m15 = {"ETH": bars}
    piv = {"ETH": sf.pivots(bars)}
    slot = 21 * M                                       # close of bar 20
    setups = {"ETH": [{"direction": "LONG", "ready": 18, "slot_ms": 19 * M, "limit": 103.0,
                       "sl": 99.4, "target": 110.0, "rr": 1.9, "h1_dir": "LONG"}]}
    weak = [_cand("ETH", slot, "LONG", 68)]
    assert ss.trades_for("A", False, weak, m15, piv, setups, simulate=_sim) == []
    got = ss.trades_for("B", False, weak, m15, piv, setups, simulate=_sim)   # 68 + 10
    assert len(got) == 1 and got[0]["pnl_usd"] == 0.25
    strong = [_cand("ETH", slot, "LONG", 80, h1="SHORT")]
    assert len(ss.trades_for("A", False, strong, m15, piv, setups, simulate=_sim)) == 1
    assert ss.trades_for("A", True, strong, m15, piv, setups, simulate=_sim) == []
    ref = ss.trades_for("ladder", False, strong, m15, piv, setups, simulate=_sim)
    assert ref and ref[0]["fill"] == 104.0


def test_risk_sizing_columns():
    t = {"fill": 100.0, "stop": 99.6, "net_pct": 0.8}
    got = ss._sizes(t, "BTC")
    assert got["notional_risk"] == 250.0 and got["pnl_risk"] == 2.0 and got["pnl_usd"] == 0.2


def test_compare_and_render():
    import random
    random.seed(9)
    bars, p = [], 100.0
    for i in range(ss.sc.WINDOW + 4 * ss.sc.H1_WINDOW + 300):
        o = p
        p *= 1 + random.gauss(0, 0.004)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
    end = int(bars[-1]["timestamp"]) + M
    read = lambda w, tf, s: {"direction": "LONG", "strength": 80, "entry": w[-1]["close"],  # noqa
                             "sl": w[-1]["close"] * 0.99, "tp_targets": [w[-1]["close"] * 1.01]}
    res = ss.compare({"ETH": bars}, start_ms=end - 2 * 24 * 3_600_000, end_ms=end,
                     read_fn=read, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in ss.VARIANTS]
    assert ss.render_telegram(res).startswith("🧱")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-struct) MODULE=scalp_struct_compare" in wf
