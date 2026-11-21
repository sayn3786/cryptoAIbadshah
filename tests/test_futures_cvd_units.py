"""Futures CVD units (Nov 2026): 'Futures 5147x spot' came from OKX swap
candle volume in CONTRACTS read as coins, then x price again."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import binance                                                       # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_okx_swap_candles_use_coin_volume(monkeypatch):
    c = binance.BinanceClient() if hasattr(binance, "BinanceClient") else None
    if c is None:
        cls = next(v for v in vars(binance).values()
                   if isinstance(v, type) and hasattr(v, "_okx_futures_candles"))
        c = cls.__new__(cls)
    # [ts, o, h, l, c, vol (contracts), volCcy (BTC), volCcyQuote, confirm]
    row = ["1700000000000", "60000", "61000", "59000", "60500", "12345", "123.45", "7400000", "1"]
    monkeypatch.setattr(c, "_get", lambda url, params=None: {"data": [row]}, raising=False)
    out = c._okx_futures_candles("BTCUSDT", "1h", 10)
    assert out and out[0]["volume"] == 123.45


def test_real_futures_taker_cvd_is_preferred_over_the_estimate():
    src = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
    i = src.index("fut_cvd = fetch_aggregated_futures_cvd(bs, interval, limit, price_map=_cvd_price_map)")
    j = src.index('fut_cvd = calculate_cvd(futures, "futures")')
    assert i < j
