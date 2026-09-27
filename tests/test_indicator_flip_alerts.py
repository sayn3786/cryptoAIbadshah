"""
Indicator flips in the Telegram Market Update: MACD cross, SuperTrend, price vs
EMA 50 and Ichimoku TK cross, each with its timeframe and the date/time of the
candle close that confirmed it. Only fresh flips; each announced once.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app                                                            # noqa: E402
import telegram_digest as td                                          # noqa: E402

DAY = 86_400_000
T0 = 1_780_000_000_000 - (1_780_000_000_000 % DAY)


def _candles(closes):
    return [{"timestamp": T0 + i * DAY, "open": c, "high": c * 1.01, "low": c * 0.99,
             "close": c, "volume": 1000} for i, c in enumerate(closes)]


def test_a_real_breakdown_on_the_last_candle_is_detected_with_its_close_time():
    closes = [100 + i * 0.5 for i in range(120)] + [80]      # steady uptrend, then a crash
    candles = _candles(closes)
    flips = {f["type"]: f for f in app._indicator_flips_for(candles, "1D")}
    assert "ema50" in flips
    f = flips["ema50"]
    assert f["direction"] == "bearish" and f["label"] == "Price crossed below EMA 50"
    assert f["bars_ago"] == 0
    assert f["break_ts"] == candles[-1]["timestamp"] + DAY      # the close of the flip candle


def test_a_steady_trend_announces_nothing():
    candles = _candles([100 + i * 0.5 for i in range(121)])
    assert app._indicator_flips_for(candles, "1D") == []


def _stub(monkeypatch, macd=None, ema=None, st=None, ich=None):
    monkeypatch.setattr(app, "calculate_macd", lambda closes: macd or {})
    monkeypatch.setattr(app, "calculate_ema_trend", lambda closes: ema or {})
    monkeypatch.setattr(app, "calculate_supertrend", lambda c: st or {})
    monkeypatch.setattr(app, "calculate_ichimoku", lambda c: ich or {})


def test_each_indicator_labels_and_direction(monkeypatch):
    _stub(monkeypatch,
          macd={"flipped_bars_ago": 0, "previous_direction": "bearish"},
          ema={"flipped_bars_ago": 1, "previous_direction": "bullish"},
          st={"flipped_bars_ago": 0, "flipped_ts": 123, "direction": "bullish"},
          ich={"tk_flipped_bars_ago": 0, "tk_flipped_ts": 456, "tk_previous_direction": "bullish"})
    flips = {f["type"]: f for f in app._indicator_flips_for(_candles([1.0] * 80), "1D")}
    assert flips["macd"]["label"] == "MACD bullish cross (histogram turned positive)"
    assert flips["ema50"]["label"] == "Price crossed below EMA 50"          # 1 bar ago still fresh
    assert flips["supertrend"]["direction"] == "bullish" and flips["supertrend"]["break_ts"] == 123
    assert flips["ichimoku"]["label"] == "Ichimoku bearish TK cross"


def test_stale_flips_are_not_announced(monkeypatch):
    _stub(monkeypatch, macd={"flipped_bars_ago": 2, "previous_direction": "bearish"},
          st={"flipped_bars_ago": None, "flipped_ts": None, "direction": "bullish"})
    assert app._indicator_flips_for(_candles([1.0] * 80), "1D") == []


def test_flips_join_the_telegram_scan_and_dedupe_per_indicator_and_candle(monkeypatch):
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "PATTERN_ALERT_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_closed_spot", lambda sym, tf: _candles([1.0] * 80))
    monkeypatch.setattr(app, "_confirmed_patterns_for", lambda closed, tf: [])
    monkeypatch.setattr(app, "_indicator_flips_for", lambda closed, tf: [
        {"kind": "indicator_flip", "type": "macd", "event": "flip", "direction": "bullish",
         "label": "MACD bullish cross (histogram turned positive)", "break_ts": 999},
        {"kind": "indicator_flip", "type": "supertrend", "event": "flip", "direction": "bullish",
         "label": "SuperTrend flipped bullish", "break_ts": 999}])
    claimed = set()
    monkeypatch.setattr(app, "_kv_claim", lambda key: not (key in claimed or claimed.add(key)))
    first = app._scan_confirmed_patterns()
    assert {a["type"] for a in first} == {"macd", "supertrend"}   # distinct ids per indicator
    assert app._scan_confirmed_patterns() == []                    # announced once


def test_digest_line_shows_timeframe_and_sgt_date():
    a = {"symbol": "BTC", "timeframe": "1D", "kind": "indicator_flip", "direction": "bullish",
         "label": "SuperTrend flipped bullish", "break_ts": 1790467200000}   # 27 Sep 00:00 UTC
    assert td.describe(a) == "1D  🟢 SuperTrend flipped bullish · Sep 27, 8:00 AM SGT"


def test_flips_count_toward_confluence_but_less_than_a_divergence():
    flip = {"symbol": "BTC", "timeframe": "1D", "kind": "indicator_flip",
            "direction": "bullish", "label": "MACD bullish cross", "break_ts": 1}
    div = {"symbol": "BTC", "timeframe": "1D", "kind": "divergence", "direction": "bullish",
           "label": "Bullish RSI Divergence", "rsi_gap": 5.0}
    assert td._weight(flip) < td._weight(div)
    text, loud = td.build_market_digest([flip, div])
    assert "⭐ BTC  (2 bullish reads)" in text and loud is True


def test_the_bell_does_not_get_indicator_flips():
    import inspect
    assert "_indicator_flips_for" not in inspect.getsource(app.api_pattern_alerts)


def test_flips_are_announced_on_1d_and_1w_only(monkeypatch):
    _stub(monkeypatch, macd={"flipped_bars_ago": 0, "previous_direction": "bearish"})
    c = _candles([1.0] * 80)
    assert app._indicator_flips_for(c, "4H") == []
    assert app._indicator_flips_for(c, "1D") and app._indicator_flips_for(c, "1W")
