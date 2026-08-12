"""
A confirmed RSI divergence reaches the bell and Telegram.

Both destinations already run through _confirmed_patterns_for — the Telegram
scan, the alert preview and the in-app bell all call it — so divergence is
added there once and arrives in both. That function is on NO scoring path, which
is what makes this safe to land during the v44 freeze.

The gate is deliberately strict. A divergence alerts only when the detector's
own lifecycle says `confirmed` (never a provisional second pivot, never an
expired one) AND the second pivot is inside the same freshness window every
other alert here uses. An alert is about something that just happened.

The other half of the job is copy. A divergence is momentum disagreeing with
price — it breaks no level and has no measured target — so it must not inherit
the breakout wording, which would assert a certainty the signal does not have.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                  # noqa: E402
import telegram as tgmod                                              # noqa: E402
from telegram import (build_pattern_alert_message,                    # noqa: E402
                      build_divergence_alert_message, send_pattern_alerts)


BASE_TS = 1_767_268_800_000
STEP = 86_400_000          # 1D


def _candles(n=60):
    return [{"timestamp": BASE_TS + i * STEP, "open": 100, "high": 101,
             "low": 99, "close": 100} for i in range(n)]


def _divergence(candles, *, status="confirmed", forming=False, bars_back=1,
                dtype="bullish", strength=8.2, age=1, with_ts=True):
    ts = candles[-bars_back]["timestamp"] if with_ts else None
    return {
        "type": dtype, "status": status, "forming": forming,
        "strength": strength, "age_candles": age, "freshness": 1.0,
        "points": {"kind": "low",
                   "prev": {"timestamp": BASE_TS, "price": 99, "rsi": 30},
                   "curr": {"timestamp": ts, "price": 98, "rsi": 35}},
    }


def _scan(monkeypatch, div, candles=None):
    """Run the alert scan with the detector stubbed to a known verdict."""
    candles = candles or _candles()
    monkeypatch.setattr(appmod, "calculate_rsi_series", lambda closes: [50.0] * len(closes))
    monkeypatch.setattr(appmod, "detect_rsi_divergence", lambda *a, **k: div)
    pats = appmod._confirmed_patterns_for(candles, "1D")
    return [p for p in pats if p.get("kind") == "divergence"]


# ── When it fires ───────────────────────────────────────────────────────────

def test_a_confirmed_fresh_divergence_becomes_an_alert(monkeypatch):
    candles = _candles()
    got = _scan(monkeypatch, _divergence(candles), candles)
    assert len(got) == 1
    a = got[0]
    assert a["label"] == "Bullish RSI Divergence"
    assert a["direction"] == "bullish"
    assert a["rsi_gap"] == 8.2
    assert a["break_ts"] == candles[-1]["timestamp"]


def test_a_hidden_divergence_is_labelled_as_hidden(monkeypatch):
    candles = _candles()
    got = _scan(monkeypatch, _divergence(candles, dtype="hidden_bearish"), candles)
    assert got[0]["label"] == "Hidden Bearish RSI Divergence"
    assert got[0]["direction"] == "bearish"


def test_it_claims_no_level_direction_or_target(monkeypatch):
    """
    Nothing broke. Every renderer keys off these, and a stray value would put
    a price and an arrow on a signal that has neither.
    """
    candles = _candles()
    a = _scan(monkeypatch, _divergence(candles), candles)[0]
    assert a["level"] is None
    assert a["target"] is None
    assert a["break_dir"] is None


# ── When it must not ────────────────────────────────────────────────────────

def test_a_forming_divergence_does_not_alert(monkeypatch):
    """
    The second pivot is still provisional. Alerting would fire and then need
    retracting when the pivot moves.
    """
    candles = _candles()
    assert _scan(monkeypatch, _divergence(candles, status="forming",
                                          forming=True), candles) == []


def test_an_expired_divergence_does_not_alert(monkeypatch):
    candles = _candles()
    assert _scan(monkeypatch, _divergence(candles, status="expired",
                                          age=9), candles) == []


def test_a_confirmed_divergence_a_few_closes_back_still_alerts(monkeypatch):
    """
    A confirmed divergence's second pivot is already pivot_window (3) closes old
    the instant it confirms, so it commonly sits 3-5 bars back. The wider
    divergence window must still catch it — this is the whole point of the
    5-close observation window (it would have been missed by the 3-bar breakout
    window).
    """
    candles = _candles()
    for bars_back in (4, appmod.DIVERGENCE_ALERT_FRESH_BARS):   # within the window
        got = _scan(monkeypatch, _divergence(candles, bars_back=bars_back), candles)
        assert len(got) == 1, f"a divergence {bars_back} closes back should alert"


def test_a_stale_but_still_confirmed_divergence_does_not_alert(monkeypatch):
    """
    The detector's window is wider than the alert window. An alert is about
    something that just happened, so past the observation window it stays quiet.
    """
    candles = _candles()
    stale = _divergence(candles, bars_back=appmod.DIVERGENCE_ALERT_FRESH_BARS + 2)
    assert _scan(monkeypatch, stale, candles) == []


def test_no_divergence_at_all_alerts_nothing(monkeypatch):
    candles = _candles()
    empty = {"type": None, "strength": None, "description": None}
    assert _scan(monkeypatch, empty, candles) == []


def test_a_divergence_without_a_timestamp_is_skipped(monkeypatch):
    """
    No timestamp means no stable id, and dedup is keyed on it — the alert would
    re-fire on every scan. Silence beats spam.
    """
    candles = _candles()
    assert _scan(monkeypatch, _divergence(candles, with_ts=False), candles) == []


# ── Dedup ───────────────────────────────────────────────────────────────────

def test_the_alert_id_is_stable_and_distinct(monkeypatch):
    candles = _candles()
    a = _scan(monkeypatch, _divergence(candles), candles)[0]
    a = {"symbol": "TAO", "timeframe": "1D", **a}

    first = appmod._pattern_alert_id("TAO", "1D", a)
    assert first == appmod._pattern_alert_id("TAO", "1D", a), "id must be stable"
    assert "divergence" in first

    # A flag on the same symbol/timeframe/bar is a different alert.
    flag = {"kind": "flag", "type": "bullish", "break_ts": a["break_ts"]}
    assert appmod._pattern_alert_id("TAO", "1D", flag) != first


# ── The copy — divergences use their OWN dedicated message ────────────────────

def _tg(**over):
    a = {"kind": "divergence", "symbol": "TAO", "timeframe": "1D",
         "label": "Bullish RSI Divergence", "direction": "bullish",
         "rsi_gap": 8.2, "age_candles": 1,
         "break_dir": None, "level": None, "target": None}
    a.update(over)
    return build_divergence_alert_message([a])


def test_the_divergence_message_has_its_own_header():
    msg = _tg()
    assert "RSI Divergence" in msg
    # It is NOT the breakout/failure 'Pattern' batch header.
    assert "Pattern Confirmed" not in msg and "Pattern Update" not in msg


def test_telegram_does_not_describe_a_divergence_as_a_breakout():
    msg = _tg()
    assert "Bullish RSI Divergence" in msg
    assert "8.2 RSI pts" in msg
    assert "Broke" not in msg, "a divergence breaks nothing"
    assert "🎯" not in msg, "and has no measured target"


def test_telegram_says_how_old_the_divergence_is():
    assert "1 candle ago" in _tg(age_candles=1)
    assert "3 candles ago" in _tg(age_candles=3)
    assert "on the last close" in _tg(age_candles=0)


def test_telegram_survives_a_missing_rsi_gap():
    msg = _tg(rsi_gap=None)
    assert "Bullish RSI Divergence" in msg
    assert "None" not in msg


def test_a_breakout_alert_is_unaffected():
    """The dedicated divergence message must not change how a breakout reads."""
    msg = build_pattern_alert_message([{
        "kind": "flag", "symbol": "BTC", "timeframe": "1D", "label": "Bullish Flag",
        "direction": "bullish", "break_dir": "up", "level": 65000, "target": 68000}])
    assert "Broke ↑" in msg
    assert "🎯" in msg


# ── Separation: a mixed batch goes out as TWO messages ───────────────────────

def _capture_sends(monkeypatch):
    """Stub Telegram env + HTTP; capture every message body sent."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    sent = []
    monkeypatch.setattr(tgmod, "_post_message",
                        lambda token, chat_id, text: sent.append(text) or True)
    return sent


def test_a_mixed_batch_sends_divergences_as_a_separate_message(monkeypatch):
    sent = _capture_sends(monkeypatch)
    ok = send_pattern_alerts([
        {"kind": "flag", "symbol": "BTC", "timeframe": "1D", "label": "Bullish Flag",
         "direction": "bullish", "break_dir": "up", "level": 65000, "target": 68000},
        {"kind": "divergence", "symbol": "TAO", "timeframe": "1D",
         "label": "Bullish RSI Divergence", "direction": "bullish", "rsi_gap": 8.2,
         "age_candles": 2, "break_dir": None, "level": None, "target": None},
    ])
    assert ok is True
    assert len(sent) == 2, "breakouts and divergences go out as two messages"
    pattern_msg = next(m for m in sent if "Bullish Flag" in m)
    div_msg     = next(m for m in sent if "RSI Divergence" in m)
    # Neither message bleeds into the other.
    assert "RSI Divergence" not in pattern_msg
    assert "Bullish Flag" not in div_msg


def test_a_divergence_only_batch_sends_one_message(monkeypatch):
    sent = _capture_sends(monkeypatch)
    send_pattern_alerts([
        {"kind": "divergence", "symbol": "TAO", "timeframe": "1D",
         "label": "Bearish RSI Divergence", "direction": "bearish", "rsi_gap": 5.0,
         "age_candles": 0, "break_dir": None, "level": None, "target": None}])
    assert len(sent) == 1 and "RSI Divergence" in sent[0]


def test_a_breakout_only_batch_sends_no_divergence_message(monkeypatch):
    sent = _capture_sends(monkeypatch)
    send_pattern_alerts([
        {"kind": "flag", "symbol": "BTC", "timeframe": "1D", "label": "Bullish Flag",
         "direction": "bullish", "break_dir": "up", "level": 65000, "target": 68000}])
    assert len(sent) == 1 and "RSI Divergence" not in sent[0]
