"""
Private trade + problem alerts (backend/ops_alerts.py and its hooks).

Only ever to the owner's private chat (TELEGRAM_ALERT_CHAT_ID, falling back to
TELEGRAM_REPORT_CHAT_ID), never to the public signals channel. Each alert is
sent once per key; a failure to alert never breaks trading.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import ops_alerts as oa                                               # noqa: E402

OPENED = {"ok": True, "coin": "FET", "side": "buy", "size": 40, "mark_px": 0.61,
          "notional_usd": 24.4, "leverage": 3, "cloid": "0xabc",
          "exit_prices": {"sl": 0.55, "tp": 0.66, "tp2": 0.72}, "tp_split": [20, 20],
          "exits_ok": True,
          "response": {"status": "ok", "response": {"data": {"statuses": [
              {"filled": {"avgPx": "0.6012", "totalSz": "40"}}]}}}}
SIG = {"id": "s1", "candle_ts": 1, "symbol": "FET", "direction": "LONG",
       "confidence_score": 71.4}


@pytest.fixture
def sent(monkeypatch):
    """Capture sends; a real in-memory claim store for dedupe."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@public_channel")
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "123456")
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)
    import kv
    claimed = set()
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=0: not (k in claimed or claimed.add(k)))
    monkeypatch.setattr(kv, "release", lambda k: claimed.discard(k))
    monkeypatch.setattr(kv, "kv_enabled", lambda: True)      # a durable store
    out = []
    monkeypatch.setattr(oa, "_post", lambda text, session=None: out.append(text) or True)
    return out


def test_opened_alert_shows_fill_stop_and_split_targets():
    t = oa.fmt_opened(OPENED, SIG)
    assert "🟢 Opened FET LONG" in t and "Entry ~0.6012" in t
    assert "Stop 0.5500" in t and "TP1 0.6600 (20) · TP2 0.7200 (20)" in t
    assert "Signal strength 71" in t


def test_execution_alerts_open_once_and_flag_unprotected(sent):
    out = {"results": [dict(OPENED, exits_ok=False, exits_error="rejected")]}
    oa.notify_execution([SIG], out)
    oa.notify_execution([SIG], out)                    # a retry: no repeats
    assert len(sent) == 2
    assert sent[0].startswith("🟢 Opened FET") and "unprotected" in sent[1]


def test_rejections_alert_but_routine_guards_do_not(sent):
    oa.notify_execution([SIG, dict(SIG, id="s2")], {"results": [
        {"ok": False, "reason": "SEND_REJECTED", "coin": "FET", "detail": "Insufficient margin"},
        {"ok": False, "reason": "POSITION_EXISTS", "coin": "ETH"}]})
    assert len(sent) == 1 and "not opened: SEND_REJECTED" in sent[0] and "Insufficient" in sent[0]


def test_manager_alerts(sent):
    oa.notify_manager([
        {"coin": "FET", "action": "move_stop", "placed": True, "stop_px": 0.6, "size": 20},
        {"coin": "ETH", "action": "close_remainder", "closed": True, "entry_px": 3000, "size": 0.004},
        {"coin": "SOL", "action": "move_stop", "placed": False, "error": "bad price"}])
    assert "TP1 hit, stop moved to entry 0.6000" in sent[0]
    assert "closed at market" in sent[1]
    assert "failed moving the stop to entry" in sent[2] and "old stop is still in place" in sent[2]


def test_closed_trade_alert_once(sent):
    t = {"coin": "FET", "side": "long", "entry_px": 0.6, "exit_px": 0.69, "exits": 2,
         "pnl_usd": 3.57, "pnl_pct": 14.88, "fees_usd": 0.03, "result": "win", "closed_at": 5}
    oa.notify_closed([t]); oa.notify_closed([t])
    assert len(sent) == 1 and "🏁 WIN · FET LONG closed" in sent[0] and "+$3.57" in sent[0]


def test_goes_only_to_the_private_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@public_channel")
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "123456")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "999")
    calls = []
    class S:
        def post(self, url, json=None, timeout=None):
            calls.append(json)
            class R:
                def raise_for_status(self): pass
            return R()
    assert oa._post("x", session=S()) is True
    assert calls[0]["chat_id"] == "999" and "parse_mode" not in calls[0]
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID")
    assert oa.chat_id() == "123456"                     # falls back to the report chat


def test_no_private_chat_means_no_alert(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@public_channel")
    monkeypatch.delenv("TELEGRAM_REPORT_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)
    assert oa.send("x", "k") == "unconfigured"


def test_a_failed_send_releases_the_claim_and_never_raises(sent, monkeypatch):
    def boom(text, session=None):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(oa, "_post", boom)
    assert oa.send("x", "k1") == "failed"
    monkeypatch.setattr(oa, "_post", lambda text, session=None: sent.append(text) or True)
    assert oa.send("x", "k1") == "sent"                 # retried next run


# ── hooks in the app ─────────────────────────────────────────────────────────

def test_auto_exec_alerts_after_execute_and_survives_alert_errors(monkeypatch):
    pytest.importorskip("flask")
    import app
    import ops_alerts
    seen = []
    monkeypatch.setattr(ops_alerts, "notify_execution", lambda s, o: seen.append(o))
    src = __import__("inspect").getsource(app._hl_auto_execute_run)
    assert "notify_execution" in src and "never fatal" in src
    def boom(*a):
        raise RuntimeError("alert path broken")
    monkeypatch.setattr(ops_alerts, "notify_manager", boom)
    app._alert_manager_pass({"ran": True, "results": [{"coin": "X"}]})   # must not raise



# ── without a durable KV store (claims don't persist across invocations) ─────

@pytest.fixture
def no_store(monkeypatch):
    """Production without Upstash: every claim 'succeeds' (read-only file)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "123456")
    import kv
    monkeypatch.setattr(kv, "kv_enabled", lambda: False)
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=0: True)
    out = []
    monkeypatch.setattr(oa, "_post", lambda text, session=None: out.append(text) or True)
    return out


def test_closed_trade_alerts_once_across_minutely_runs_without_a_store(no_store):
    t = {"coin": "GRAM", "side": "long", "entry_px": 1.593, "exit_px": 1.566,
         "exits": 1, "pnl_usd": -0.42, "pnl_pct": -1.77, "fees_usd": 0.02,
         "result": "loss", "closed_at": 10 * 60_000 + 25_000}       # 00:10:25
    for minute in range(10, 16):                                    # runs at 00:10 … 00:15
        oa.notify_closed([t], now_ms=minute * 60_000 + 3_000)
    assert len(no_store) == 1 and "GRAM LONG closed" in no_store[0]


def test_manager_failure_alerts_at_most_every_30_min_without_a_store(no_store):
    fail = [{"coin": "SOL", "action": "move_stop", "placed": False, "error": "x"}]
    for minute in range(0, 60):
        oa.notify_manager(fail, now_ms=minute * 60_000)
    assert len(no_store) == 2                                       # :00 and :30


def test_one_shot_alerts_still_send_without_a_store(no_store):
    oa.notify_manager([{"coin": "FET", "action": "move_stop", "placed": True,
                        "stop_px": 0.6, "size": 20}], now_ms=7 * 60_000)
    assert len(no_store) == 1 and "stop moved to entry" in no_store[0]



def test_a_stale_entry_skip_is_explained_once(sent):
    res = {"ok": False, "reason": "STALE_ENTRY", "coin": "FET", "drift_pct": 1.5,
           "allowed_pct": 1.0}
    oa.notify_execution([SIG], {"results": [res]})
    oa.notify_execution([SIG], {"results": [res]})      # the :12 / :32 catch-up retries
    assert len(sent) == 1
    assert sent[0].startswith("⏭ FET LONG not opened: price already 1.5% from the signal entry")
    assert "(limit 1.0%" in sent[0]
