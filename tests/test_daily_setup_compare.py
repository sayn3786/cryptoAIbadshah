"""
The combination study's daily setups applied to the 4h HL signals: boost the
signals their way, dock the ones against, for the next day or 3 days.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import combo_study as cs                                               # noqa: E402
import daily_setup_compare as ds                                       # noqa: E402
import reweight_compare as rw                                          # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402

D = ds.DAY_MS


def test_setup_rules():
    assert ds.setup({"c_range": 1, "engine": 1}) == 1
    assert ds.setup({"c_range": 1, "macd": 1}) == 0              # no listed confirmation
    assert ds.setup({"c_range": 1}) == 0
    assert ds.setup({"flags": -1, "liquidity_grab": -1}) == -1
    assert ds.setup({"flags": -1, "volume": -1}) == 0
    assert ds.setup({"flags": 1, "stoch_rsi": 1}) == 0


def test_active_window():
    ss = [(10 * D, 1), (20 * D, -1)]
    assert ds.active(ss, 10 * D, 1) == 1
    assert ds.active(ss, 10 * D + D - 1, 1) == 1
    assert ds.active(ss, 11 * D, 1) == 0
    assert ds.active(ss, 12 * D, 3) == 1
    assert ds.active(ss, 21 * D, 3) == -1
    assert ds.active(ss, 9 * D, 3) == 0                         # before any setup


def test_strength():
    long_ = {"direction": "LONG", "strength": 65.0}
    short = {"direction": "SHORT", "strength": 72.0}
    assert ds.strength(long_, 1, False, 10, -10) == 75.0
    assert ds.strength(short, 1, False, 10, -10) == 62.0
    assert ds.strength(short, -1, True, 10, -10) == 82.0 - rw.LOW_VOL_DOCK
    assert ds.strength(long_, 0, False, 10, -10) == 65.0


def test_coin_setups_reads_closed_daily_candles_only():
    bars = [{"timestamp": i * D, "open": 100, "high": 100.1, "low": 99.9, "close": 100,
             "volume": 1.0} for i in range(cs.WINDOW + 3)]
    seen = []

    def score(win, sym):
        seen.append(win[-1]["timestamp"])
        return {"score_breakdown": {"flags": -20, "macd": -10}, "direction": "SHORT",
                "strength": 40}
    got = ds.coin_setups(bars, score, symbol="X", since_ms=(cs.WINDOW + 1) * D + D)
    assert got == [((cs.WINDOW + 1) * D + D, -1), ((cs.WINDOW + 2) * D + D, -1)]
    assert all(t + D <= c for t, (c, _) in zip(seen, got))


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = ds.compare(market(), {"ETH": [(0, 1)]}, core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in ds.VARIANTS]
    assert ds.render_telegram(res).startswith("🧩")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "daily-setups) MODULE=daily_setup_compare" in wf


def test_only_one_setup_family():
    c = [{"symbol": "ETH", "slot_ms": 10 * D, "direction": "SHORT", "strength": 66.0},
         {"symbol": "SOL", "slot_ms": 10 * D, "direction": "LONG", "strength": 66.0}]
    setups = {"ETH": [(10 * D, -1)], "SOL": [(10 * D, 1)]}
    got = {x["symbol"]: x["strength"] for x in ds.apply(c, setups, {}, 1, 5.0, 0.0, -1)}
    assert got == {"ETH": 71.0, "SOL": 66.0}
    got = {x["symbol"]: x["strength"] for x in ds.apply(c, setups, {}, 1, 5.0, 0.0, 1)}
    assert got == {"ETH": 66.0, "SOL": 71.0}
