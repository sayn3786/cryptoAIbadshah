"""
VWAP whipsaw on the live HL book: on a reading whose close keeps crossing
the VWAP, a new cross (or VWAP altogether) scores nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reweight_compare as rw                                          # noqa: E402
import vwap_whipsaw_compare as vw                                      # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402


def bars(closes):
    return [{"timestamp": i, "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
            for i, c in enumerate(closes)]


def cand(**kw):
    c = {"symbol": "ETH", "slot_ms": 0, "direction": "LONG", "strength": 72.0,
         "strength_before_calibration": 72.0, "h1_strength": 70.0, "h2_strength": 72.0,
         "chased": False, "avg_tf_strength": 71.0,
         "score_breakdown": {"macd": 144, "vwap": 14}, "h1_score_breakdown": {"macd": 154, "vwap": 6}}
    c.update(kw)
    return c


def test_crosses_counts_a_saw_and_not_a_trend():
    saw = [100.0] * 50 + [101.0, 99.0] * 6
    x = vw.crosses(bars(saw))
    assert x["count"] >= 8 and x["now"]
    trend = [100.0 + i for i in range(62)]
    assert vw.crosses(bars(trend)) == {"count": 0, "now": False}
    assert vw.crosses(bars([1.0] * 5))["count"] == 0


def test_change_rules():
    saw_now, saw_old = {"count": 4, "now": True}, {"count": 4, "now": False}
    assert vw.change(14, saw_now, "cross", 3) == -14
    assert vw.change(6, saw_old, "cross", 3) == 0           # no fresh cross: keeps above/below
    assert vw.change(6, saw_old, "all", 3) == -6
    assert vw.change(6, {"count": 3, "now": True}, "all", 4) == 0
    assert vw.change(14, saw_now, None, 3) == 0


def test_strength():
    calm = {"count": 0, "now": False}
    f = {"h2": {"count": 5, "now": True}, "h1": calm, "low_vol": False}
    # -14 on the 2H score -> -6.36 strength
    assert vw.strength(cand(), f, "cross", 3) == pytest.approx(65.64, abs=0.05)
    assert vw.strength(cand(), {"h2": calm, "h1": calm, "low_vol": True}, "all", 3) == \
        72.0 - rw.LOW_VOL_DOCK


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    res = vw.compare(market(), core=["BTC", "ETH", "SOL"], days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in vw.VARIANTS]
    assert vw.render_telegram(res).startswith("〰️")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "vwap-whipsaw) MODULE=vwap_whipsaw_compare" in wf
