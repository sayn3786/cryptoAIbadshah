"""
The 9/21 EMA rules on the live HL book: flat 2H lines docked or skipped, and
the short cross scored on EMA 9 instead of 7, on top of the low-vol dock.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import ema_flat_compare as ef                                          # noqa: E402
import reweight_compare as rw                                          # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402


def bars(closes):
    return [{"timestamp": i, "open": c, "high": c, "low": c, "close": c, "volume": 1}
            for i, c in enumerate(closes)]


def cand(**kw):
    c = {"symbol": "ETH", "slot_ms": 0, "direction": "LONG", "strength": 72.0,
         "strength_before_calibration": 72.0, "h1_strength": 70.0, "h2_strength": 72.0,
         "chased": False, "avg_tf_strength": 71.0,
         "score_breakdown": {"macd": 158}, "h1_score_breakdown": {"macd": 154}}
    c.update(kw)
    return c


def test_ema_matches_the_engine_seed():
    e = ef.ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and e[3] == pytest.approx(3.0)


def test_flat_and_trending_lines():
    assert ef.is_flat(bars([100.0] * 40)) is True
    assert ef.is_flat(bars([100.0 + i for i in range(40)])) is False      # trending
    assert ef.is_flat(bars([100.0] * 10)) is None                          # too short


def test_cross_points():
    up = [100.0] * 30 + [101.0 + i for i in range(10)]
    assert ef.cross_points(up, 7) == ef.HOLD_PTS
    down = [100.0 + i for i in range(30)] + [60.0]
    assert ef.cross_points(down, 7) == -ef.FRESH_PTS
    assert ef.cross_points([1.0] * 5, 7) == 0.0


def test_cross_change_when_7_and_9_agree_is_zero():
    up = [100.0 + i for i in range(40)]
    assert ef.cross_change(bars(up)) == 0.0


def test_strength_variants():
    f = {"flat": True, "low_vol": True, "add2": 0.0, "add1": 0.0}
    assert ef.strength(cand(), f, 0.0, False) == 72.0 - rw.LOW_VOL_DOCK
    assert ef.strength(cand(), f, 10.0, False) == 72.0 - rw.LOW_VOL_DOCK - 10
    assert ef.strength(cand(), {**f, "flat": False}, 10.0, False) == 67.0
    # a 9/21 fresh cross where 7/21 only held: +8 score -> +3.64 strength
    g = {"flat": False, "low_vol": False, "add2": 8.0, "add1": 0.0}
    assert ef.strength(cand(), g, 0.0, True) == pytest.approx(75.64, abs=0.05)


def test_reweighted_takes_a_signed_score_change():
    assert rw.reweighted(cand(), {}, add=-22.0) == pytest.approx(62.0, abs=0.05)
    assert rw.reweighted(cand(direction="SHORT", score_breakdown={"macd": -158}),
                         {}, add=-22.0) == pytest.approx(82.0, abs=0.05)


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = ef.compare(market(), core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in ef.VARIANTS]
    assert ef.render_telegram(res).startswith("📏")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "ema-flat) MODULE=ema_flat_compare" in wf
