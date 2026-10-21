"""
Fake breakouts on the live HL book: pattern-breakout points by the break's
volume and liquidity grabs only on above-average volume.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import breakout_volume_compare as bv                                   # noqa: E402
import reweight_compare as rw                                          # noqa: E402
import signals                                                         # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402


def cand(**kw):
    c = {"symbol": "ETH", "slot_ms": 0, "direction": "LONG", "strength": 72.0,
         "strength_before_calibration": 72.0, "h1_strength": 70.0, "h2_strength": 72.0,
         "chased": False, "avg_tf_strength": 71.0,
         "score_breakdown": {"macd": 140, "flags": 20}, "h1_score_breakdown": {"macd": 154},
         "h2_bvol": {"weak": 20.0}, "h1_bvol": {}, "h2_grab": {}, "h1_grab": {}}
    c.update(kw)
    return c


def test_breakout_change():
    assert bv.breakout_change({"weak": 20, "normal": 12, "strong": 14}) == pytest.approx(-16.5)
    assert bv.breakout_change({"weak": -10}) == 10            # a weak bearish break undone
    assert bv.breakout_change({}) == 0


def test_grab_change():
    assert bv.grab_change({"points": 15, "volume_ratio": 0.7}) == -15
    assert bv.grab_change({"points": -12, "volume_ratio": 1.0}) == 12
    assert bv.grab_change({"points": 15, "volume_ratio": 1.4}) == 0
    assert bv.grab_change({"points": 15, "volume_ratio": None}) == 0     # no volume data


def test_weak_breakout_lowers_a_long():
    # -20 score on the 2H -> -9.09 strength
    assert bv.strength(cand(), False, True, False) == pytest.approx(62.91, abs=0.05)
    assert bv.strength(cand(), False, False, False) == 72.0
    assert bv.strength(cand(), True, False, False) == 72.0 - rw.LOW_VOL_DOCK


def test_engine_reports_breakout_volume_and_grab_volume():
    acc = {}
    signals._note_breakout_vol(acc, {"level": "weak"}, 20)
    signals._note_breakout_vol(acc, None, -12)
    assert acc == {"weak": 20, "unknown": -12}
    cs = [{"volume": 10.0}] * 20 + [{"volume": 30.0}, {"volume": 10.0}]
    assert signals._candle_volume_ratio(cs, 1) == 3.0
    assert signals._candle_volume_ratio(cs, 0) == pytest.approx(round(10 / 11, 2))
    assert signals._candle_volume_ratio(cs, None) is None


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = bv.compare(market(), core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in bv.VARIANTS]
    assert bv.render_telegram(res).startswith("📢")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "breakout-volume) MODULE=breakout_volume_compare" in wf
