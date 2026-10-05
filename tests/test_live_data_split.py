"""
The live-vs-backtest split in the strength diagnostic: how many points the
score sections a price-only backtest can't see (order flow, derivatives,
sentiment, macro, on-chain) moved each candidate.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture
def app_mod():
    pytest.importorskip("flask")
    import app
    return app


BD = {"macd": 20, "rsi_slope": 9, "funding": -30, "fear_greed": -8, "news": 4,
      "order_book": -2, "cvd": -14}


def test_split_is_relative_to_the_trade(app_mod):
    long_ = app_mod._live_data_split(BD, "LONG")
    assert long_["live_pts"] == -36 and long_["live_strength"] == pytest.approx(-16.4)
    assert long_["top"] == [("funding", -30), ("fear_greed", -8), ("news", 4)]
    assert long_["cvd_pts"] == -14
    short = app_mod._live_data_split(BD, "SHORT")
    assert short["live_pts"] == 36 and short["cvd_pts"] == 14
    assert app_mod._live_data_split({}, "LONG") == {"live_pts": 0, "live_strength": 0,
                                                    "top": [], "cvd_pts": 0}


def test_live_sections_are_engine_sections(app_mod):
    import candle_analysis
    from signals import generate_signal
    from test_cadence_compare import walk
    sig = generate_signal(candle_analysis.build_candle_analysis(walk(300, seed=2), "2H", "ETH"))
    assert not set(sig["score_breakdown"]) & app_mod.LIVE_DATA_SECTIONS   # price-only: silent
    from test_score_breakdown import KNOWN
    assert app_mod.LIVE_DATA_SECTIONS <= KNOWN


def test_diag_line_carries_the_split(app_mod):
    h1, h2 = {"strength": 70.0}, {"strength": 64.0, "sig": {"score_breakdown": BD}}
    screen = {"direction": "LONG", "btc_adj": 0, "strength_before_calibration": 64.0,
              "strength": 64.0, "ok": True}
    d = app_mod._strength_diag("BLUR", h1, h2, screen)
    assert ("live-data -36pts ≈-16 (funding -30, fear_greed -8, news +4) · cvd -14 · 64"
            in d["line"])
    assert d["live"]["live_strength"] == pytest.approx(-16.4)
    # an older signal without a breakdown: no split, no crash
    d = app_mod._strength_diag("BLUR", h1, {"strength": 64.0, "sig": {}}, screen)
    assert "live-data" not in d["line"] and d["live"] is None


def test_summary(app_mod):
    rows = [{"live": app_mod._live_data_split(BD, "LONG")},
            {"live": app_mod._live_data_split({"funding": 30}, "LONG")},
            {"live": app_mod._live_data_split({"macd": 5}, "LONG")}, None, {"live": None}]
    s = app_mod._live_data_summary(rows)
    assert s["candidates"] == 3 and s["cost_5_or_more"] == 1 and s["helped_5_or_more"] == 1
    assert s["avg_strength_effect"] == pytest.approx((-16.4 + 13.6 + 0) / 3, abs=0.1)
    assert s["top_sections_pts"][0] == "fear_greed -8"
    assert app_mod._live_data_summary([]) == {"candidates": 0}


def test_publish_and_endpoint_carry_the_summary():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py")).read()
    assert '"live_data_effect": _live_data_summary(strength_diag)' in src
    assert '"live_data_effect": result.get("live_data_effect")' in src
    assert '"live_data_effect_now": result.get("live_data_effect")' in src
    js = open(os.path.join(os.path.dirname(__file__), "..", "cloudflare",
                           "hl-manage-worker", "worker.js")).read()
    assert '"live_data_effect"' in js
