"""Telegram reads the recorded decision, never cached/recomputed prices."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app as appmod
import kv
import telegram


@pytest.fixture
def stored(monkeypatch):
    rec = appmod._rec_from_row({
        "id": "3f2b1c00-0000-4000-8000-000000000001",
        "symbol": "BTC", "direction": "LONG", "timeframe": "2H",
        "entry_price": "100.123456789123", "stop_loss": "95.000000000001",
        "targets": [{"target_number": 1, "target_price": "110.123456789123"}],
    })
    slot = {"published": True, "reason": None, "recommendations": [rec],
            "slot_start": "2026-10-01T08:00:00+08:00",
            "slot_end": "2026-10-01T12:00:00+08:00"}
    monkeypatch.setattr(appmod, "_published_slot", lambda: slot)
    def forbidden(*a, **k):
        raise AssertionError("must not read cached or recomputed recommendations")
    monkeypatch.setattr(appmod, "_compute_recommendations", forbidden)
    monkeypatch.setattr(appmod, "_rec_cache_load", forbidden)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test")
    monkeypatch.setattr(kv, "claim", lambda *a, **k: True)
    monkeypatch.setattr(kv, "get_value", lambda *a, **k: None)
    monkeypatch.setattr(kv, "set_value", lambda *a, **k: True)
    monkeypatch.setattr(appmod, "_recent_signal_results", lambda *a: ([], {}))
    sent = []
    monkeypatch.setattr(appmod, "_send_telegram_recs", lambda data: sent.append(data) or True)
    return slot, sent


@pytest.mark.parametrize("manual", [True, False])
def test_both_paths_preserve_original_ids_and_prices(stored, manual):
    slot, sent = stored
    with appmod.app.test_request_context():
        if manual:
            assert appmod.api_telegram_send().get_json()["ok"]
        else:
            assert appmod._send_recs_with_context()
    assert len(sent) == 1
    rec = sent[0]["recommendations"][0]
    assert rec == slot["recommendations"][0]
    assert rec["signal_id"] == "3f2b1c00-0000-4000-8000-000000000001"
    assert rec["entry"] == "100.123456789123"
    assert rec["sl"] == "95.000000000001"
    assert rec["tp_targets"] == ["110.123456789123"]
    assert sent[0]["source"] == "database"
    message = telegram.build_rec_message(sent[0])
    assert "Entry: $100.1235" in message
    assert "TP1: $110.1235" in message


@pytest.mark.parametrize("failure", ["empty", "missing_id", "db_error"])
@pytest.mark.parametrize("manual", [True, False])
def test_unavailable_slot_never_sends(stored, monkeypatch, failure, manual):
    slot, sent = stored
    if failure == "empty":
        slot.update(published=False, recommendations=[])
    elif failure == "missing_id":
        slot["recommendations"][0]["signal_id"] = None
    else:
        def fail():
            raise RuntimeError("database unavailable")
        monkeypatch.setattr(appmod, "_published_slot", fail)
    with appmod.app.test_request_context():
        if manual:
            response, status = appmod.api_telegram_send()
            assert status == 503
            assert response.get_json()["error_code"] == "PERSISTED_SLOT_UNAVAILABLE"
        else:
            assert appmod._send_recs_with_context() is False
    assert sent == []


@pytest.mark.parametrize("compute_fails", [False, True])
def test_scheduled_route_ignores_recomputed_prices(stored, monkeypatch, compute_fails):
    slot, sent = stored
    monkeypatch.setattr(appmod, "_cron_authorized", lambda: True)
    monkeypatch.setattr(appmod, "_rec_cache_save", lambda *a: None)
    def compute():
        if compute_fails:
            raise RuntimeError("computation failed after earlier publication")
        return {"recommendations": [{"symbol": "BTC", "direction": "SHORT",
                                      "entry": 999, "sl": 1000}],
                "actionable": True}
    monkeypatch.setattr(appmod, "_compute_recommendations", compute)
    monkeypatch.setattr(appmod, "_dispatch_once", lambda kind, key, fn:
                        ("sent" if fn() else "failed") if kind == "tg:recs" else "skipped")
    with appmod.app.test_request_context():
        response = appmod.api_cron_daily().get_json()
    assert response["results"]["telegram"] == "sent"
    assert sent[0]["recommendations"] == slot["recommendations"]
