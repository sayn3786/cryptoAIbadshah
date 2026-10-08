"""
Front-loaded recency (candidate, off by default): a fresh divergence / RSI
top-bottom counts most on the close it is confirmed and less after.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis                                                 # noqa: E402
import signals                                                         # noqa: E402
import recency_compare as rc                                           # noqa: E402
from test_cadence_compare import market, stub, walk                    # noqa: E402,F401


@pytest.fixture
def front(monkeypatch):
    monkeypatch.setattr(signals, "RECENCY_FRONT_LOADED", True)


def test_off_by_default():
    if os.getenv("SIGNAL_RECENCY", "").lower() != "front":
        assert signals.RECENCY_FRONT_LOADED is False
        assert signals.divergence_recency(15) == 1.0          # no effect when off


@pytest.mark.parametrize("age, w", [(3, 1.0), (4, 1.0), (5, 0.9), (8, 0.6), (10, 0.4),
                                    (14, 0.4), (None, 1.0)])
def test_divergence_recency(front, age, w):
    assert signals.divergence_recency(age) == pytest.approx(w)


def test_reversal_brake_is_front_loaded(front):
    assert signals._reversal_freshness(1) == 1.0
    assert signals._reversal_freshness(4) == pytest.approx((12 - 4) / (12 - 1))
    assert signals._reversal_freshness(12) == 0.0


def test_reversal_brake_today(monkeypatch):
    monkeypatch.setattr(signals, "RECENCY_FRONT_LOADED", False)
    assert signals._reversal_freshness(4) == 1.0


def _div_points(age):
    a = candle_analysis.build_candle_analysis(walk(300, seed=3), "2H", "ETH")
    a["rsi_divergence"] = {"type": "bullish", "strength": 6, "age_candles": age,
                           "freshness": 1.0, "description": "test"}
    return signals.generate_signal(a)["score_breakdown"].get("rsi_divergence", 0)


def test_engine_scores_an_older_divergence_less_when_front_loaded(monkeypatch):
    monkeypatch.setattr(signals, "RECENCY_FRONT_LOADED", False)
    assert _div_points(4) == _div_points(10) == 18           # today: full in the window
    monkeypatch.setattr(signals, "RECENCY_FRONT_LOADED", True)
    assert _div_points(4) == 18 and _div_points(10) == 7      # 18 x 0.4


def test_compare_runs_both_and_restores_the_flag(stub):
    before = signals.RECENCY_FRONT_LOADED
    res = rc.compare(market(), core=["BTC", "ETH", "SOL"], days=5)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in rc.VARIANTS]
    assert signals.RECENCY_FRONT_LOADED == before
    assert rc.render_telegram(res).startswith("⏳")
