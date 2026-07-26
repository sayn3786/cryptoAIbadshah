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


def test_real_exchange_preferred_over_longer_coingecko(monkeypatch):
    # An exchange with FEWER but real-OHLC candles must beat CoinGecko's longer
    # APPROXIMATED history (thin bodies / erratic wicks). No CoinGecko fallback
    # once an exchange cleared the floor.
    c = BinanceClient()
    monkeypatch.setattr(c, "_binance_klines", lambda *a, **k: None)
    monkeypatch.setattr(c, "_okx_candles",    lambda *a, **k: _candles(150))   # real, < rich(200)
    for name in ("_bybit_candles", "_kucoin_candles", "_mexc_candles",
                 "_htx_candles", "_lbank_candles"):
        monkeypatch.setattr(c, name, lambda *a, **k: None)
    cg_called = {"n": 0}
    def _cg(*a, **k):
        cg_called["n"] += 1
        return _candles(240)          # longer, but approximated
    monkeypatch.setattr(c, "_cg_daily_as_candles", _cg)
    out = c.get_spot_klines("TAO", "4h", 240)
    assert len(out) == 150 and c.data_source == "okx"
    assert cg_called["n"] == 0, "CoinGecko must not be used when a real source cleared the floor"


def test_gate_candles_parse_real_ohlc(monkeypatch):
    c = BinanceClient()
    # Gate row: [timestamp(s), volume(quote), close, high, low, open, ...]
    rows = [[str(1_700_000_000 + i * 3600), "1000", str(100 + i), str(101 + i),
             str(99 + i), str(100 + i - 0.5)] for i in range(50)]
    monkeypatch.setattr(c, "_get", lambda url, params=None: rows)
    out = c._gate_candles("TAOUSDT", "4h", 240)
    assert out and len(out) == 50
    assert out[0]["timestamp"] < out[-1]["timestamp"]      # ascending
    assert all(cd["high"] >= cd["low"] for cd in out)
    assert all(cd["high"] >= cd["open"] and cd["high"] >= cd["close"] for cd in out)


def test_gate_skips_unsupported_interval(monkeypatch):
    c = BinanceClient()
    monkeypatch.setattr(c, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert c._gate_candles("TAOUSDT", "2h", 240) is None    # Gate has no 2h bars


def test_okx_paginates_history_for_depth(monkeypatch):
    c = BinanceClient()
    base = 1_700_000_000_000
    def _okx_row(ts, p):
        return [str(ts), str(p), str(p + 1), str(p - 1), str(p), "10", "10"]
    # recent endpoint returns only 30 bars (short listing window); history-candles
    # pages older bars until we have depth, then returns empty.
    recent = [_okx_row(base - i * 86_400_000, 100 + i) for i in range(30)]      # newest-first
    def fake_get(url, params=None):
        if "history-candles" in url:
            after = int(params["after"])
            older = [_okx_row(after - (i + 1) * 86_400_000, 200 + i) for i in range(100)]
            # stop after we've gone far enough back
            if after < base - 200 * 86_400_000:
                return {"data": []}
            return {"data": older}
        return {"data": recent}
    monkeypatch.setattr(c, "_get", fake_get)
    out = c._okx_candles("TAOUSDT", "1d", 240)
    assert out and len(out) > 30, "history pagination must deepen a short recent window"
    assert all(out[i]["timestamp"] < out[i + 1]["timestamp"] for i in range(len(out) - 1))


def test_coingecko_daily_candles_have_real_range(monkeypatch):
    # CoinGecko fallback must produce candles with a real high/low range (from
    # hourly data), not flat open==close dashes that render invisibly.
    import math
    c = BinanceClient()
    base = 1_700_000_000_000
    prices = [[base + h * 3600_000, 100 + 10 * math.sin(h / 4.0)] for h in range(72)]
    vols   = [[base + h * 3600_000, 5.0] for h in range(72)]
    monkeypatch.setattr(c, "_cg_intraday_data", lambda sym, days: (prices, vols))
    out = c._cg_daily_as_candles("TAO", "1d", 240)
    assert out and len(out) >= 3
    # the full-day candles must have high strictly above low (a real body/wick)
    assert all(cd["high"] > cd["low"] for cd in out[1:]), "candles must not be flat"


def test_coingecko_falls_back_to_daily_when_no_hourly(monkeypatch):
    c = BinanceClient()
    monkeypatch.setattr(c, "_cg_intraday_data", lambda sym, days: (None, None))
    day = [[1_700_000_000_000 + d * 86_400_000, 100 + d] for d in range(40)]
    monkeypatch.setattr(c, "_cg_daily_data", lambda sym, days: (day, [[p[0], 1.0] for p in day]))
    out = c._cg_daily_as_candles("TAO", "1d", 240)
    assert out and len(out) >= 30            # still usable, just flat (last resort)


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
