"""
Freshness and validity of Telegram alerts.

1. Never built on synthetic/demo candles (the client's last-resort fallback).
2. Never built on a stale series: the latest closed candle must have closed
   within ALERT_MAX_STALENESS_INTERVALS intervals of now (by the clock, not
   just "the last candle in the series").
3. Divergence / RSI-reversal lines say how old they are and the pivot date.
4. A confirmed divergence whose turn already happened ("played out") is not
   announced.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app                                                            # noqa: E402
import telegram_digest as td                                          # noqa: E402

DAY = 86_400_000


def _series(last_close_ms, n=100, price=100.0):
    """n daily candles whose LAST closes at last_close_ms, plus one forming."""
    first_open = last_close_ms - n * DAY
    out = [{"timestamp": first_open + i * DAY, "open": price, "high": price * 1.01,
            "low": price * 0.99, "close": price, "volume": 1} for i in range(n)]
    out.append(dict(out[-1], timestamp=last_close_ms))          # the still-forming bar
    return out


def _now_ms():
    return int(time.time() * 1000)


@pytest.fixture
def fetch(monkeypatch):
    state = {}
    monkeypatch.setattr(app, "_exchange_pair", lambda sym: sym + "USDT")
    monkeypatch.setattr(app.client, "get_spot_klines_sourced",
                        lambda bs, interval, limit: (state["candles"], state["source"]))
    return state


def test_demo_candles_are_refused(fetch):
    fetch.update(candles=_series(_now_ms() - _now_ms() % DAY), source="demo")
    closed, why = app._fetch_alert_candles("BTC", "1D")
    assert closed == [] and why == "demo"


def test_a_series_that_stopped_updating_is_refused(fetch):
    today = _now_ms() - _now_ms() % DAY
    fetch.update(candles=_series(today - 3 * DAY), source="binance")   # last close 3 days ago
    closed, why = app._fetch_alert_candles("BTC", "1D")
    assert closed == [] and why == "stale"


def test_current_real_data_passes(fetch):
    today = _now_ms() - _now_ms() % DAY
    fetch.update(candles=_series(today), source="okx")
    closed, why = app._fetch_alert_candles("BTC", "1D")
    assert why is None and len(closed) == 100                     # forming bar dropped


def test_the_scan_skips_unusable_coins(monkeypatch):
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC", "ETH"))
    monkeypatch.setattr(app, "PATTERN_ALERT_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_alert_candles",
                        lambda sym, tf: ([], "demo") if sym == "BTC" else ([{"timestamp": 1}], None))
    monkeypatch.setattr(app, "_confirmed_patterns_for", lambda closed, tf: [
        {"kind": "divergence", "label": "Bullish RSI Divergence", "direction": "bullish",
         "break_ts": 1, "status": "active"}])
    monkeypatch.setattr(app, "_indicator_flips_for", lambda closed, tf: [])
    monkeypatch.setattr(app, "_kv_claim", lambda key: True)
    assert {a["symbol"] for a in app._scan_confirmed_patterns()} == {"ETH"}


def _closes_to_candles(closes):
    return [{"timestamp": i * DAY, "open": c, "high": c, "low": c, "close": c, "volume": 1}
            for i, c in enumerate(closes)]


def test_confirmed_divergence_played_out_when_price_and_rsi_have_turned():
    # Bullish divergence pivot at index 60 (close 80); price then rallies well
    # past +3% and RSI climbs back above 50 → played out.
    closes = [100 - i * 0.33 for i in range(61)] + [80 + i * 1.5 for i in range(1, 15)]
    candles = _closes_to_candles(closes)
    pivot_ts = candles[60]["timestamp"]
    assert app._confirmed_divergence_status(candles, pivot_ts, bullish=True) == "played_out"
    # The same pivot, but price still near the low → still active.
    flat = _closes_to_candles(closes[:61] + [80.5] * 5)
    assert app._confirmed_divergence_status(flat, pivot_ts, bullish=True) == "active"


def test_bearish_divergence_played_out_mirrors():
    closes = [100 + i * 0.33 for i in range(61)] + [120 - i * 1.5 for i in range(1, 15)]
    candles = _closes_to_candles(closes)
    assert app._confirmed_divergence_status(candles, candles[60]["timestamp"],
                                            bullish=False) == "played_out"


def test_the_scan_drops_a_played_out_divergence(monkeypatch):
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "PATTERN_ALERT_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_alert_candles", lambda sym, tf: ([{"timestamp": 1}], None))
    monkeypatch.setattr(app, "_indicator_flips_for", lambda closed, tf: [])
    monkeypatch.setattr(app, "_confirmed_patterns_for", lambda closed, tf: [
        {"kind": "divergence", "label": "Bullish RSI Divergence", "direction": "bullish",
         "break_ts": 1, "status": "played_out"},
        {"kind": "divergence", "label": "Bearish RSI Divergence", "direction": "bearish",
         "break_ts": 2, "status": "active"}])
    monkeypatch.setattr(app, "_kv_claim", lambda key: True)
    assert [a["label"] for a in app._scan_confirmed_patterns()] == ["Bearish RSI Divergence"]


def test_divergence_and_reversal_lines_show_when_they_happened():
    div = {"symbol": "BTC", "timeframe": "1D", "kind": "divergence", "direction": "bullish",
           "label": "Bullish RSI Divergence", "rsi_gap": 6.2, "age_candles": 3,
           "break_ts": 1790208000000}                        # 24 Sep 00:00 UTC open
    assert td.describe(div).endswith("· confirmed on the latest candle (Sep 28 close)")
    swing = {"symbol": "ETH", "timeframe": "4H", "kind": "rsi_swing", "direction": "bullish",
             "label": "RSI Oversold Bottom", "rsi": 28, "age_candles": 0,
             "break_ts": 1790409600000}
    assert "· confirmed on the latest candle (" in td.describe(swing)
    div1 = {**div, "age_candles": 4}
    assert "· confirmed 1 candle ago (Sep 28 close)" in td.describe(div1)
    assert "pivot" not in td.describe(div1)
