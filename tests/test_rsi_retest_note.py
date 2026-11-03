"""The dashboard's "back at the oversold-bottom low" note (candle_analysis.rsi_retest)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import candle_analysis as ca                                           # noqa: E402
from test_rsi_retest_study import tao_like                             # noqa: E402


def _read(bars, tf="1D"):
    return ca.build_candle_analysis(bars, tf, "TAO")


def test_the_tao_chart_gets_the_low_at_risk_note():
    bars = tao_like([])                                     # last close 221 vs the 220.12 low
    a = _read(bars)
    rt = a["rsi_retest"]
    assert rt and rt["kind"] == "weaker" and rt["headline"] == "Low at risk"
    assert rt["low"] == 220.12 and rt["rsi"] < rt["bottom_rsi"] and rt["bars_ago"] == 5
    assert rt["stats"] == {"n": 89, "broke_pct": 66.7}
    assert "66.7%" in rt["note"] and "213.516" in rt["note"]
    assert "rsi_retest" in ca.CANDLE_DERIVED_KEYS


def test_no_note_away_from_the_low_or_after_a_break():
    up = tao_like([232])                                    # 5% above the low
    assert _read(up)["rsi_retest"] is None
    broke = tao_like([205, 221])                            # closed 7% under it, came back
    assert _read(broke)["rsi_retest"] is None


def test_other_timeframes_say_the_history_is_daily_only():
    rt = ca.rsi_retest(tao_like([]), ca.calculate_rsi_series([b["close"] for b in tao_like([])]),
                       [{"kind": "oversold_bottom", "timestamp": 40 * 86_400_000,
                         "price": 220.12, "rsi": 34.4}], "4H")
    assert rt and rt["stats"] is None and "daily candles only" in rt["note"]


def test_stronger_and_similar_kinds():
    bars = tao_like([])
    rsi = [None] * (len(bars) - 1) + [40.0]
    mk = [{"kind": "oversold_bottom", "timestamp": bars[40]["timestamp"], "price": 220.12,
           "rsi": 34.4}]
    assert ca.rsi_retest(bars, rsi, mk, "1D")["kind"] == "stronger"
    rsi[-1] = 35.0
    assert ca.rsi_retest(bars, rsi, mk, "1D")["kind"] == "similar"
    assert ca.rsi_retest(bars, rsi, [], "1D") is None
    assert ca.rsi_retest(bars, rsi, [{**mk[0], "kind": "overbought_top"}], "1D") is None


def test_dashboard_renders_the_note():
    root = os.path.join(os.path.dirname(__file__), "..", "dashboard")
    js = open(os.path.join(root, "js", "dashboard.js")).read()
    html = open(os.path.join(root, "index.html")).read()
    assert "function renderRsiRetestNote" in js and "renderRsiRetestNote(a.rsi_retest)" in js
    assert 'id="rsiRetestNote"' in html
