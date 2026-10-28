"""SMA 100 / 200 on the chart and in the EMA card (display only)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis as ca                                           # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_sma_series():
    assert ca.sma_series([1, 2, 3, 4], 5) == [None] * 4
    assert ca.sma_series([1, 2, 3, 4, 5], 2) == [None, 1.5, 2.5, 3.5, 4.5]


def test_sma_now_reads_the_distance():
    closes = [100.0] * 199 + [102.0]
    got = ca._sma_now(closes)
    assert round(got["sma200"], 2) == 100.01 and round(got["sma100"], 2) == 100.02
    assert got["sma200_dist_pct"] > 1.9
    assert ca._sma_now([1.0] * 50) == {}


def test_the_chart_draws_and_toggles_them():
    js = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js")).read()
    html = open(os.path.join(ROOT, "dashboard", "index.html")).read()
    assert "emaLines?.sma200" in js and "emaLines?.sma100" in js
    assert "_layerVisible('sma200')" in js
    assert 'data-layer="sma200"' in html and 'data-layer="sma100"' in html
