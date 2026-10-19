"""
RSI divergence by the book: points only near 30/70 and/or after a break of
structure, judged per timeframe, on the live HL book.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import divergence_gate_compare as dg                                   # noqa: E402
import portfolio_backtest as pbt                                       # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402


def div(**kw):
    d = {"type": "bullish", "status": "confirmed", "prev_rsi": 28.0, "curr_rsi": 36.0,
         "curr_ts": 100, "bos_dir": "bullish", "bos_ts": 140, "bos_held": True}
    d.update(kw)
    return d


def cand(h2=None, h1=None, **kw):
    c = {"symbol": "ETH", "slot_ms": 0, "direction": "LONG", "strength": 72.0,
         "strength_before_calibration": 72.0, "h1_strength": 70.0, "h2_strength": 72.0,
         "chased": False, "avg_tf_strength": 71.0,
         "score_breakdown": {"macd": 140, "rsi_divergence": 18},
         "h1_score_breakdown": {"macd": 140, "rsi_divergence": 18},
         "h2_div": div() if h2 is None else h2, "h1_div": div() if h1 is None else h1}
    c.update(kw)
    return c


def test_zone_rule():
    assert dg.in_zone(div())                                         # 28 <= 35
    assert not dg.in_zone(div(prev_rsi=41.0, curr_rsi=45.0))
    assert dg.in_zone(div(type="bearish", prev_rsi=71.0, curr_rsi=60.0))
    assert not dg.in_zone(div(type="bearish", prev_rsi=60.0, curr_rsi=55.0))
    assert dg.in_zone(div(type="hidden_bullish", prev_rsi=50.0, curr_rsi=45.0))
    assert not dg.in_zone(div(prev_rsi=None, curr_rsi=None))


def test_break_of_structure_rule():
    assert dg.structure_broke(div())
    assert not dg.structure_broke(div(bos_ts=90))                    # before the pivot
    assert not dg.structure_broke(div(bos_dir="bearish"))            # the other way
    assert not dg.structure_broke(div(bos_held=False))               # given back
    assert not dg.structure_broke(div(status="forming"))
    assert dg.structure_broke(div(type="hidden_bearish", bos_dir="bearish"))


def test_no_divergence_always_passes():
    assert dg.passes({}, True, True, True)
    assert not dg.passes(div(), False, False, drop=True)


def test_failing_divergence_loses_its_points():
    assert dg.gated(cand(), True, True) == 72.0                       # passes both
    # 18 points off the 2H score -> -8.18 strength
    got = dg.gated(cand(h2=div(prev_rsi=45.0, curr_rsi=48.0)), True, False)
    assert got == pytest.approx(63.82, abs=0.05)
    assert dg.gated(cand(), False, False, drop=True) == pytest.approx(63.82, abs=0.05)


def test_one_timeframe_only():
    # only the 1H divergence fails: 2H strength unchanged
    got = dg.gated(cand(h1=div(bos_ts=50)), False, True)
    assert got == pytest.approx(72.0)


def test_div_context_reads_the_analysis():
    a = {"rsi_divergence": {"type": "bearish", "status": "confirmed",
                            "points": {"prev": {"rsi": 72, "timestamp": 1},
                                       "curr": {"rsi": 66, "timestamp": 5}}},
         "bos_streak": {"direction": "bearish", "last_ts": 7, "held": True}}
    d = pbt._div_context(a)
    assert (d["prev_rsi"], d["curr_rsi"], d["curr_ts"], d["bos_ts"]) == (72, 66, 5, 7)
    assert pbt._div_context({})["type"] is None


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = dg.compare(market(), core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in dg.VARIANTS]
    assert dg.render_telegram(res).startswith("〽️")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "divergence) MODULE=divergence_gate_compare" in wf
