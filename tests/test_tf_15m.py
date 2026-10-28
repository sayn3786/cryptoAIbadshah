"""15m on the dashboard chart: the backend knows the timeframe and every
candle source maps the 15m interval (an unknown interval used to fall back to
WEEKLY candles on some exchanges)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_backend_timeframe_tables():
    pytest.importorskip("flask")
    import app
    assert app.TF_INTERVAL["15M"] == "15m" and app.TF_SECONDS["15M"] == 900
    assert app.TF_LIMIT["15M"] == 320 and "15M" in app._TF_PRICE_GAP   # SMA 200 across the chart


def test_every_exchange_maps_15m():
    src = open(os.path.join(ROOT, "backend", "binance.py"), encoding="utf-8").read()
    for name in ("_GATE_IV", "_OKX_BAR_SPOT", "_BYBIT_IV", "_OKX_BAR", "_MEXC_IV", "_HTX_IV",
                 "_LBANK_IV", "_KUCOIN_IV"):
        i = src.index(name + " = {")
        assert '"15m"' in src[i:i + 200], name


def test_dashboard_has_the_15m_tab():
    html = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert 'data-tf="15M"' in html
    assert "'15M': { candle: '15m candle'" in js and "['15M', '1H', '2H'].includes(tf)" in js


def test_analysis_cache_refreshes_every_15m_on_15m():
    pytest.importorskip("flask")
    import app
    assert app._analysis_cache_key("BTC", "15M")[-2:] in ("00", "15", "30", "45")
