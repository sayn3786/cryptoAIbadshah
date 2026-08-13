"""
RSI swing markers reach Telegram as their OWN dedicated message.

An oversold bottom (price swing low + oversold RSI) or overbought top (swing high
+ overbought RSI) is a momentum turning point — no level broken, no target — so,
exactly like an RSI divergence, it alerts on the wide 5-close observation window
and goes out as its own message, separate from the breakout 'Pattern' batch.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                  # noqa: E402
import telegram as tgmod                                              # noqa: E402
from telegram import (build_rsi_swing_alert_message,                  # noqa: E402
                      send_pattern_alerts)


BASE_TS = 1_767_268_800_000
STEP = 86_400_000          # 1D


def _candles(n=60):
    return [{"timestamp": BASE_TS + i * STEP, "open": 100, "high": 101,
             "low": 99, "close": 100} for i in range(n)]


def _scan(monkeypatch, marker, candles=None):
    candles = candles or _candles()
    monkeypatch.setattr(appmod, "calculate_rsi_series", lambda closes: [50.0] * len(closes))
    monkeypatch.setattr(appmod.candle_analysis, "rsi_swing_markers",
                        lambda *a, **k: ([marker] if marker else []))
    # keep the other detectors quiet
    monkeypatch.setattr(appmod, "detect_rsi_divergence", lambda *a, **k: {"type": None})
    pats = appmod._confirmed_patterns_for(candles, "1D")
    return [p for p in pats if p.get("kind") == "rsi_swing"]


def _mk(candles, *, kind="oversold_bottom", bars_back=2, rsi=32.0):
    return {"timestamp": candles[-bars_back]["timestamp"], "kind": kind,
            "rsi": rsi, "price": 99}


# ── When it fires ────────────────────────────────────────────────────────────

def test_a_fresh_oversold_bottom_becomes_a_bullish_alert(monkeypatch):
    candles = _candles()
    got = _scan(monkeypatch, _mk(candles), candles)
    assert len(got) == 1
    a = got[0]
    assert a["label"] == "RSI Oversold Bottom" and a["direction"] == "bullish"
    assert a["rsi"] == 32.0 and a["level"] is None and a["target"] is None


def test_an_overbought_top_is_bearish(monkeypatch):
    candles = _candles()
    got = _scan(monkeypatch, _mk(candles, kind="overbought_top", rsi=68.0), candles)
    assert got[0]["label"] == "RSI Overbought Top" and got[0]["direction"] == "bearish"


def test_a_marker_a_few_closes_back_still_alerts(monkeypatch):
    candles = _candles()
    got = _scan(monkeypatch, _mk(candles, bars_back=appmod.RSI_SWING_ALERT_FRESH_BARS), candles)
    assert len(got) == 1


# ── When it must not ─────────────────────────────────────────────────────────

def test_a_stale_marker_does_not_alert(monkeypatch):
    candles = _candles()
    stale = _mk(candles, bars_back=appmod.RSI_SWING_ALERT_FRESH_BARS + 3)
    assert _scan(monkeypatch, stale, candles) == []


def test_no_marker_alerts_nothing(monkeypatch):
    assert _scan(monkeypatch, None) == []


# ── Dedup ────────────────────────────────────────────────────────────────────

def test_the_alert_id_is_stable_and_distinct(monkeypatch):
    candles = _candles()
    a = {"symbol": "ETH", "timeframe": "1D", **_scan(monkeypatch, _mk(candles), candles)[0]}
    first = appmod._pattern_alert_id("ETH", "1D", a)
    assert first == appmod._pattern_alert_id("ETH", "1D", a)
    assert "rsi_swing" in first


# ── Copy + separation ────────────────────────────────────────────────────────

def test_the_message_has_its_own_header():
    msg = build_rsi_swing_alert_message([{
        "kind": "rsi_swing", "type": "oversold_bottom", "symbol": "ETH",
        "timeframe": "1D", "label": "RSI Oversold Bottom", "direction": "bullish",
        "rsi": 32}])
    assert "RSI Reversal" in msg and "RSI Oversold Bottom" in msg
    assert "swing low" in msg
    assert "Broke" not in msg and "🎯" not in msg


def _capture_sends(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    sent = []
    monkeypatch.setattr(tgmod, "_post_message",
                        lambda token, chat_id, text: sent.append(text) or True)
    return sent


def test_a_mixed_batch_sends_three_separate_messages(monkeypatch):
    sent = _capture_sends(monkeypatch)
    ok = send_pattern_alerts([
        {"kind": "flag", "symbol": "BTC", "timeframe": "1D", "label": "Bullish Flag",
         "direction": "bullish", "break_dir": "up", "level": 65000, "target": 68000},
        {"kind": "divergence", "symbol": "TAO", "timeframe": "1D",
         "label": "Bullish RSI Divergence", "direction": "bullish", "rsi_gap": 8.2,
         "age_candles": 2, "break_dir": None, "level": None, "target": None},
        {"kind": "rsi_swing", "type": "oversold_bottom", "symbol": "ETH",
         "timeframe": "1D", "label": "RSI Oversold Bottom", "direction": "bullish",
         "rsi": 32, "level": None, "target": None, "break_dir": None},
    ])
    assert ok is True and len(sent) == 3
    assert any("Bullish Flag" in m and "RSI" not in m for m in sent)
    assert any("RSI Divergence" in m for m in sent)
    assert any("RSI Reversal" in m for m in sent)
