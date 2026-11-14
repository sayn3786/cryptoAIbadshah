"""The pre-registered range-box test: deviations (the IF) and held breaks
(the ELSE) against random directions on the same entries."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import range_vs_random as rv                                           # noqa: E402
from test_ranges import trend_then_range                               # noqa: E402

D = 86_400_000


def test_a_deviation_below_is_a_long_to_the_top_edge_stop_under_the_dip():
    bars = trend_then_range([200, 196, 195, 204])
    s = [x for x in rv.signals_at(bars, D) if x["play"] == "deviation"]
    assert len(s) == 1
    s = s[0]
    assert s["direction"] == "LONG" and s["entry"] == 204 and s["target"] == 208
    assert s["sl"] < 194        # under the lowest low of the dip (194) by 0.1 ATR


def test_a_deviation_above_is_a_short_to_the_bottom_edge():
    s = [x for x in rv.signals_at(trend_then_range([210, 213, 205]), D) if x["play"] == "deviation"]
    assert s and s[0]["direction"] == "SHORT" and s[0]["target"] == 202 and s[0]["sl"] > 214


def test_a_break_held_for_seven_closes_trades_with_it_one_box_height():
    closes = [210, 211, 212, 211, 212, 213, 213.5]        # 7 closes above 208, under 214
    s = [x for x in rv.signals_at(trend_then_range(closes), D) if x["play"] == "break held"]
    assert len(s) == 1 and s[0]["direction"] == "LONG"
    assert s[0]["target"] == 208 + 6 and s[0]["sl"] < 208
    # the 6th close outside is not yet a held break; the 8th no longer a new one
    assert not [x for x in rv.signals_at(trend_then_range(closes[:6]), D) if x["play"] == "break held"]
    assert not [x for x in rv.signals_at(trend_then_range(closes + [217]), D)
                if x["play"] == "break held"]


def test_a_break_already_past_its_target_is_skipped():
    closes = [210, 211, 212, 211, 212, 213, 230]          # past 214 = top + height
    assert not [x for x in rv.signals_at(trend_then_range(closes), D) if x["play"] == "break held"]


def test_reversed_mirrors_stop_and_target():
    r = rv._reversed({"direction": "LONG", "entry": 100.0, "sl": 95.0, "target": 110.0})
    assert (r["direction"], r["sl"], r["target"]) == ("SHORT", 105.0, 90.0)


def test_works_needs_every_check():
    good = {"trades": 40, "pnl_usd": 10, "pf_usd": 1.2, "beats_random": True}
    assert rv.works(good, {"beats_random": False})
    assert not rv.works(good, {"beats_random": True})
    assert not rv.works({**good, "trades": 29}, {})
    assert not rv.works({**good, "pf_usd": 1.1}, {})


def test_the_fixed_rule_and_the_workflow():
    assert (rv.PERIODS, rv.MIN_PF, rv.MIN_TRADES, rv.HOLD_CANDLES, rv.WINDOW) == \
        ((0, 400), 1.15, 30, 30, 150)
    import candle_analysis
    assert rv.WINDOW == candle_analysis.STRUCTURE_CHART_BARS
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "range-vs-random]" in wf and "python -m range_vs_random --telegram" in wf


def test_render():
    row = {"label": "deviation", "signals": 0, "trades": 0}
    res = {"window": {"start": "2025-01-01", "end": "2026-01-01", "days": 365.0}, "coins": ["BTC"],
           "charts": [{"tf": "4H", "hold_h": 120, "plays": [
               {"play": "deviation", "rows": [row, {**row, "label": "deviation, REVERSED"}],
                "works": False, "longs": 0, "avg_rr": None}]}]}
    text = rv.render_telegram(res)
    assert "IF a deviation is confirmed" in text and "does not work ❌" in text
    assert "BOTH end_days_ago 0 and 400" in text
