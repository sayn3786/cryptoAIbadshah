"""
Hold longer when a daily setup agrees: only the exits of HL trades going a
fresh setup's way change; a control runs the same runner on every trade.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import rr_compare as rr                                                # noqa: E402
import setup_exit_compare as se                                        # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402

D = se.DAY_MS
LONG = {"symbol": "ETH", "direction": "LONG", "slot_ms": 10 * D, "tp_targets": [110, 120, 130]}


def test_exit_plans():
    assert se.exit_plan(None)(LONG) is None
    assert se.exit_plan("tp3")(LONG) == {"tp_targets": [110, 130]}
    assert se.exit_plan("tp3_30")(LONG) == {"tp_targets": [110, 130], "tp1_frac": 0.3}
    run = se.exit_plan("run3")(LONG)
    assert run["max_hold_hours"] == 72 and run["tp_targets"][1] > 1000
    short = {**LONG, "direction": "SHORT", "tp_targets": [90, 80, 70]}
    assert se.exit_plan("run7")(short)["tp_targets"][1] < 1
    assert se.exit_plan("tp3")({**LONG, "tp_targets": [110, 120]}) is None


def test_only_trades_with_a_setup_their_way_change():
    setups = {"ETH": [(9 * D, 1)]}
    pick = se.chooser(setups, "tp3", True)
    assert pick(LONG) == {"tp_targets": [110, 130]}
    assert pick({**LONG, "direction": "SHORT"}) is None                  # against it
    assert pick({**LONG, "slot_ms": 13 * D}) is None                      # setup too old
    assert se.chooser(setups, "tp3", False)({**LONG, "symbol": "SOL"}) is not None


def test_split_by_setup():
    setups = {"ETH": [(9 * D, 1)]}
    trades = [{**LONG, "outcome": "tp2", "pnl_usd": 1.0},
              {**LONG, "direction": "SHORT", "outcome": "stop", "pnl_usd": -0.8},
              {**LONG, "symbol": "SOL", "outcome": "tp1", "pnl_usd": 0.4},
              {**LONG, "symbol": "SOL", "outcome": "open_at_end", "pnl_usd": 9.0}]
    sp = se.split(trades, setups)
    assert sp["with"] == {"n": 1, "win": 100.0, "avg": 1.0, "total": 1.0}
    assert sp["against"]["n"] == 1 and sp["none"]["n"] == 1


def test_book_takes_a_per_trade_exit(monkeypatch):
    seen = []

    def fake(r, candles, **kw):
        seen.append((r["tp_targets"], kw.get("tp1_frac"), kw.get("max_hold_hours")))
        return {"taken": False, "reason": "STALE_ENTRY"}
    monkeypatch.setattr(rr.ee, "simulate_hl", fake)
    rec = {**LONG, "strength": 75, "entry": 100, "sl": 95, "rank": 1}
    rr.book([rec], {}, atr_add=0, exit_for=lambda r: {"tp_targets": [110, 130], "tp1_frac": 0.3})
    rr.book([rec], {}, atr_add=0)
    assert seen[0] == ([110, 130], 0.3, None) and seen[1] == ([110, 120, 130], 0.5, None)


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = se.compare(market(), {"ETH": [(0, 1)]}, core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in se.VARIANTS]
    assert se.render_telegram(res).startswith("🏃")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "setup-exits) MODULE=setup_exit_compare" in wf
