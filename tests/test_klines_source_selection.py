"""
Spot-kline source selection: get_spot_klines must keep the RICHEST source, not
lock onto the first one that barely clears the floor. Regression for TAO 1D
showing "not tradeable — 26 candles" because it pinned to OKX's thin window
instead of Bybit/KuCoin's full history. Sources are mocked; no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from binance import BinanceClient                                        # noqa: E402


def _candles(n, start_ts=1_000_000, step=86_400_000, price=100.0):
    return [{"timestamp": start_ts + i * step, "open": price, "high": price + 1,
             "low": price - 1, "close": price, "volume": 10.0} for i in range(n)]


def test_daily_prefers_rich_source_over_thin_first(monkeypatch):
    c = BinanceClient()
    # Binance has nothing (e.g. token not listed there); OKX has a THIN 26-bar
    # window; Bybit carries the full history.
    monkeypatch.setattr(c, "_binance_klines", lambda *a, **k: None)
    monkeypatch.setattr(c, "_okx_candles",    lambda *a, **k: _candles(26))
    monkeypatch.setattr(c, "_bybit_candles",  lambda *a, **k: _candles(240))
    monkeypatch.setattr(c, "_kucoin_candles", lambda *a, **k: None)
    monkeypatch.setattr(c, "_mexc_candles",   lambda *a, **k: None)
    monkeypatch.setattr(c, "_htx_candles",    lambda *a, **k: None)
    monkeypatch.setattr(c, "_lbank_candles",  lambda *a, **k: None)

    out = c.get_spot_klines("TAO", "1d", 240)
    assert len(out) == 240, "must return Bybit's full history, not OKX's 26"
    assert c.data_source == "bybit"


def test_daily_stops_early_on_first_rich_source(monkeypatch):
    c = BinanceClient()
    # Binance already rich → don't waste calls on later sources.
    monkeypatch.setattr(c, "_binance_klines", lambda *a, **k: _candles(240))
    def _boom(*a, **k):
        raise AssertionError("should not be called once a rich source is found")
    monkeypatch.setattr(c, "_okx_candles", _boom)
    out = c.get_spot_klines("BTC", "1d", 240)
    assert len(out) == 240 and c.data_source == "binance"


def test_genuinely_thin_everywhere_returns_best_real(monkeypatch):
    c = BinanceClient()
    # All real sources thin (young token) → return the deepest real result (still
    # clears the 26 floor) rather than demo; the data-quality gate handles the
    # "not tradeable" verdict downstream.
    for name in ("_binance_klines", "_bybit_candles", "_kucoin_candles",
                 "_mexc_candles", "_htx_candles", "_lbank_candles",
                 "_cg_daily_as_candles"):
        monkeypatch.setattr(c, name, lambda *a, **k: None)
    monkeypatch.setattr(c, "_okx_candles", lambda *a, **k: _candles(28))
    out = c.get_spot_klines("NEWX", "1d", 240)
    assert len(out) == 28 and c.data_source == "okx"
