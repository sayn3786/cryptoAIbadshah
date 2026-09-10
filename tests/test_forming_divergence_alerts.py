"""
A FORMING (provisional) RSI divergence reaches Telegram as its OWN early
heads-up — separate from, and never mistaken for, the confirmed feed.

The confirmed divergence alert is always ~pivot_window closes behind the second
pivot (a swing high/low is only knowable once enough later candles close). This
forming alert exists to surface the divergence EARLY, the instant the detector
spots the provisional pivot, clearly labelled as not-yet-confirmed.

Two invariants matter and are pinned here:
  1. Separation — a forming read has kind "divergence_forming" and routes to its
     own ⏳ message; it never appears in the confirmed "divergence" feed, and a
     confirmed read never appears as forming.
  2. One heads-up per setup — dedup is keyed on the CONFIRMED prior pivot, which
     is stable while the provisional second pivot walks forward over the closes
     before it confirms, so the setup alerts exactly once.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                  # noqa: E402
import telegram as tgmod                                              # noqa: E402
from telegram import (build_forming_divergence_alert_message,         # noqa: E402
                      build_divergence_alert_message, send_pattern_alerts)


BASE_TS = 1_767_268_800_000
STEP = 86_400_000          # 1D
PREV_TS = BASE_TS          # the confirmed prior pivot — the dedup anchor


def _candles(n=60):
    return [{"timestamp": BASE_TS + i * STEP, "open": 100, "high": 101,
             "low": 99, "close": 100} for i in range(n)]


def _forming(candles, *, dtype="bearish", strength=6.4, age=0, played_out=False,
             closes_to_confirm=3, prev_ts=PREV_TS, curr_back=1):
    """A forming (provisional) divergence verdict, as the detector returns one."""
    curr_ts = candles[-curr_back]["timestamp"]
    return {
        "type": dtype, "status": "forming", "forming": True,
        "played_out": played_out, "strength": strength, "age_candles": age,
        "closes_to_confirm": closes_to_confirm,
        "points": {"kind": "high",
                   "prev": {"timestamp": prev_ts, "price": 100, "rsi": 78},
                   "curr": {"timestamp": curr_ts, "price": 101, "rsi": 67}},
    }


def _scan(monkeypatch, div, candles=None, kind="divergence_forming"):
    candles = candles or _candles()
    monkeypatch.setattr(appmod, "calculate_rsi_series", lambda closes: [50.0] * len(closes))
    monkeypatch.setattr(appmod, "detect_rsi_divergence", lambda *a, **k: div)
    pats = appmod._confirmed_patterns_for(candles, "1D")
    return [p for p in pats if p.get("kind") == kind]


# ── When it fires ─────────────────────────────────────────────────────────────

def test_a_forming_divergence_becomes_its_own_early_alert(monkeypatch):
    candles = _candles()
    got = _scan(monkeypatch, _forming(candles), candles)
    assert len(got) == 1
    a = got[0]
    assert a["kind"] == "divergence_forming"
    assert a["event"] == "forming"
    assert a["label"] == "Forming Bearish RSI Divergence"
    assert a["direction"] == "bearish"
    assert a["rsi_gap"] == 6.4
    assert a["closes_to_confirm"] == 3


def test_a_forming_hidden_divergence_is_labelled_hidden(monkeypatch):
    got = _scan(monkeypatch, _forming(_candles(), dtype="hidden_bullish"))
    assert got[0]["label"] == "Forming Hidden Bullish RSI Divergence"
    assert got[0]["direction"] == "bullish"


def test_a_forming_alert_claims_no_level_direction_or_target(monkeypatch):
    a = _scan(monkeypatch, _forming(_candles()))[0]
    assert a["level"] is None and a["target"] is None and a["break_dir"] is None


# ── Dedup: keyed on the STABLE prior pivot, not the walking provisional one ────

def test_dedup_is_keyed_on_the_confirmed_prior_pivot(monkeypatch):
    """The provisional second pivot moves forward each close before it confirms.
    Keying on it would re-fire; keying on the confirmed prior pivot fires once."""
    candles = _candles()
    # Same setup, seen on three consecutive scans: the provisional pivot walks
    # from 3 → 2 → 1 closes back, but the prior pivot (the anchor) is unchanged.
    ids = set()
    for curr_back, ctc in ((3, 3), (2, 2), (1, 1)):
        a = _scan(monkeypatch, _forming(candles, curr_back=curr_back,
                                        closes_to_confirm=ctc), candles)[0]
        a = {"symbol": "HYPE", "timeframe": "1D", **a}
        ids.add(appmod._pattern_alert_id("HYPE", "1D", a))
    assert len(ids) == 1, "one setup must produce one stable dedup id"


def test_forming_and_confirmed_ids_never_collide(monkeypatch):
    """A forming alert and the later confirmed alert are distinct events, so a
    forming heads-up never suppresses the confirmed one (different kind)."""
    forming = {"kind": "divergence_forming", "type": "bearish", "break_ts": PREV_TS}
    confirmed = {"kind": "divergence", "type": "bearish", "break_ts": PREV_TS}
    assert (appmod._pattern_alert_id("HYPE", "1D", forming)
            != appmod._pattern_alert_id("HYPE", "1D", confirmed))


# ── When it must not fire ──────────────────────────────────────────────────────

def test_a_played_out_forming_divergence_is_suppressed(monkeypatch):
    """Its predicted turn already happened — an 'early, watch this' alert would
    be misleading. Nothing goes out."""
    assert _scan(monkeypatch, _forming(_candles(), played_out=True)) == []


def test_a_confirmed_divergence_does_not_emit_a_forming_alert(monkeypatch):
    confirmed = {"type": "bearish", "status": "confirmed", "forming": False,
                 "strength": 6.4, "age_candles": 3,
                 "points": {"prev": {"timestamp": PREV_TS},
                            "curr": {"timestamp": _candles()[-3]["timestamp"]}}}
    assert _scan(monkeypatch, confirmed) == []


def test_a_forming_divergence_does_not_leak_into_the_confirmed_feed(monkeypatch):
    """The whole safety property: forming reads never appear as confirmed."""
    got = _scan(monkeypatch, _forming(_candles()), kind="divergence")
    assert got == []


def test_a_forming_divergence_without_a_prior_pivot_ts_is_skipped(monkeypatch):
    """No prior-pivot timestamp means no stable dedup anchor — skip, don't spam."""
    assert _scan(monkeypatch, _forming(_candles(), prev_ts=None)) == []


def test_a_stale_forming_read_beyond_the_window_does_not_alert(monkeypatch):
    """Defensive: the provisional pivot should be recent, so a provisional pivot
    past the observation window is not sent."""
    candles = _candles()
    stale = _forming(candles, curr_back=appmod.FORMING_DIVERGENCE_ALERT_FRESH_BARS + 2)
    assert _scan(monkeypatch, stale, candles) == []


# ── The copy — forming uses its OWN provisional message ────────────────────────

def _tg(**over):
    a = {"kind": "divergence_forming", "symbol": "HYPE", "timeframe": "1D",
         "label": "Forming Bearish RSI Divergence", "direction": "bearish",
         "rsi_gap": 6.4, "closes_to_confirm": 2,
         "break_dir": None, "level": None, "target": None}
    a.update(over)
    return build_forming_divergence_alert_message([a])


def test_the_forming_message_is_marked_provisional():
    msg = _tg()
    assert "Forming RSI Divergence" in msg
    assert "Provisional" in msg or "provisional" in msg
    assert "unconfirmed" in msg.lower() or "not yet confirmed" in msg.lower()


def test_the_forming_message_shows_a_countdown_not_an_age():
    assert "2 more closes confirm" in _tg(closes_to_confirm=2)
    assert "1 more close confirms" in _tg(closes_to_confirm=1)


def test_the_forming_message_survives_a_missing_countdown():
    msg = _tg(closes_to_confirm=None)
    assert "Forming Bearish RSI Divergence" in msg
    assert "None" not in msg


def test_the_forming_message_is_not_a_breakout_and_survives_missing_gap():
    msg = _tg(rsi_gap=None)
    assert "Broke" not in msg and "🎯" not in msg
    assert "None" not in msg


# ── Separation on the wire — forming goes out as its own message ───────────────

def _capture_sends(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    sent = []
    monkeypatch.setattr(tgmod, "_post_message",
                        lambda token, chat_id, text: sent.append(text) or True)
    return sent


def test_a_forming_only_batch_sends_one_provisional_message(monkeypatch):
    sent = _capture_sends(monkeypatch)
    ok = send_pattern_alerts([
        {"kind": "divergence_forming", "symbol": "HYPE", "timeframe": "1D",
         "label": "Forming Bearish RSI Divergence", "direction": "bearish",
         "rsi_gap": 6.4, "closes_to_confirm": 3,
         "break_dir": None, "level": None, "target": None}])
    assert ok is True
    assert len(sent) == 1
    assert "Forming RSI Divergence" in sent[0]


def test_confirmed_and_forming_go_out_as_two_separate_messages(monkeypatch):
    sent = _capture_sends(monkeypatch)
    send_pattern_alerts([
        {"kind": "divergence", "symbol": "TAO", "timeframe": "1D",
         "label": "Bullish RSI Divergence", "direction": "bullish", "rsi_gap": 8.2,
         "age_candles": 3, "break_dir": None, "level": None, "target": None},
        {"kind": "divergence_forming", "symbol": "HYPE", "timeframe": "1D",
         "label": "Forming Bearish RSI Divergence", "direction": "bearish",
         "rsi_gap": 6.4, "closes_to_confirm": 2,
         "break_dir": None, "level": None, "target": None},
    ])
    assert len(sent) == 2
    confirmed_msg = next(m for m in sent if "Forming" not in m)
    forming_msg = next(m for m in sent if "Forming" in m)
    # Neither message bleeds into the other.
    assert "Forming" not in confirmed_msg
    assert "Provisional" in forming_msg or "provisional" in forming_msg
