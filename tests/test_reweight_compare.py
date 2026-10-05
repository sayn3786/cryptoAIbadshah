"""
Re-weighting the strength score from the per-section breakdown, with the
v53 caps re-applied, on the live HL book.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reweight_compare as rw                                          # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402


def cand(**kw):
    c = {"symbol": "ETH", "slot_ms": 0, "direction": "LONG", "strength": 66.0,
         "strength_before_calibration": 66.0, "h1_strength": 60.0, "h2_strength": 66.0,
         "chased": False, "avg_tf_strength": 63.0,
         "score_breakdown": {"macd": 100, "engulfing": 22, "cvd": 23},
         "h1_score_breakdown": {"macd": 100, "engulfing": 22}}
    c.update(kw)
    return c


def test_score_delta():
    assert rw.score_delta({"engulfing": 22, "cvd": -14}, {"engulfing": 1.5, "cvd": 0.5}) == 18


def test_a_bull_change_raises_a_long_and_lowers_a_short():
    assert rw._toward({"macd": 100}, 22) == pytest.approx(10.0)
    assert rw._toward({"macd": -100}, 22) == pytest.approx(-10.0)


def test_engulfing_x1_5_lifts_a_long_over_the_floor():
    # +11 score -> +5 strength: 66 -> 71
    assert rw.reweighted(cand(), rw.A) == pytest.approx(71.0)


def test_halving_a_section_that_backed_the_trade_lowers_it():
    # CVD +23 halved -> -11.5 score -> -5.23 strength
    assert rw.reweighted(cand(), rw.C) == pytest.approx(60.77, abs=0.05)


def test_the_caps_are_reapplied():
    c = cand(chased=True)
    assert rw.reweighted(c, rw.A) == 68.0            # chase cap holds the lift at 68
    split = cand(h1_strength=40.0, h2_strength=66.0, h1_score_breakdown={"macd": 88})
    assert rw.reweighted(split, rw.A) < 69           # 1H/2H split still docked


def test_low_vol_docks_only_when_asked():
    assert rw.reweighted(cand(), {}) == 66.0
    assert rw.reweighted(cand(), {}, low_vol=True) == 61.0


def test_apply_moves_the_ranking_average_with_the_strength():
    got = rw.apply([cand()], rw.A, {("ETH", 0): "low"}, low_vol=True)[0]
    assert got["strength"] == pytest.approx(66.0) and got["avg_tf_strength"] == pytest.approx(63.0)
    got = rw.apply([cand()], rw.A, {}, low_vol=True)[0]
    assert got["strength"] == pytest.approx(71.0) and got["avg_tf_strength"] == pytest.approx(68.0)


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = rw.compare(market(), core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in rw.VARIANTS]
    assert rw.render_telegram(res).startswith("⚖️")
