"""Transaction-boundary unit tests; no external DB, alerts or trades."""
import copy
import json
from contextlib import contextmanager
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import publication_batch as batch
import app

SLOT = datetime(2026, 10, 18, 4, tzinfo=timezone.utc)


@pytest.fixture
def journal(monkeypatch):
    state = {"row": None, "signals": []}
    class Result:
        def __init__(self, row): self.row = row
        def mappings(self): return self
        def first(self): return self.row
    class Session:
        def execute(self, sql, params):
            sql = str(sql)
            if sql.startswith("INSERT"):
                if state["row"] is None:
                    state["row"] = {"payload": json.loads(params["payload"]),
                                    "strategy_version": params["version"], "completed_at": None}
            elif sql.startswith("UPDATE"):
                state["row"]["completed_at"] = SLOT
            return Result(state["row"])
    @contextmanager
    def scope():
        before = copy.deepcopy(state)
        try:
            yield Session()
        except Exception:
            state.clear()
            state.update(before)
            raise
    monkeypatch.setattr(batch.db, "session_scope", scope)
    monkeypatch.setattr(batch.deploy_context, "environment", lambda: "research")
    return state


def freeze():
    batch.freeze(SLOT, [{"symbol": "BTC", "entry": "100.123456789", "generated_at_utc": SLOT},
                        {"symbol": "ETH", "entry": "20.123456789", "generated_at_utc": SLOT}],
                 {"BTC": {"signal_candle_closed_at": 1}, "ETH": {}}, [])


def test_failed_batch_rolls_back_and_retry_uses_original_intent(journal, monkeypatch):
    freeze()
    frozen = copy.deepcopy(journal["row"]["payload"])
    calls = []
    failing = True
    def persist(rec, analysis, session):
        assert session is not None
        assert isinstance(rec["generated_at_utc"], datetime)
        calls.append(copy.deepcopy(rec))
        if rec["symbol"] == "ETH" and failing:
            return {"ok": False, "actionable": True}
        journal["signals"].append(rec["symbol"])
        return {"ok": True, "signal_id": rec["symbol"], "created": True}
    monkeypatch.setattr(batch.signal_publish, "persist_recommendation", persist)
    with pytest.raises(RuntimeError, match="PUBLICATION_BATCH_WRITE_FAILED"):
        batch.resume(SLOT)
    assert journal["signals"] == []
    assert journal["row"]["completed_at"] is None
    batch.freeze(SLOT, [{"symbol": "SOL", "entry": 999}], {}, [])
    assert journal["row"]["payload"] == frozen
    failing = False
    result = batch.resume(SLOT)
    assert result["persisted"] == 2
    assert calls[0] == calls[2] and calls[1] == calls[3]
    assert journal["signals"] == ["BTC", "ETH"]
    batch.resume(SLOT)
    assert len(calls) == 4  # completed replay never writes signals again


def test_strategy_change_does_not_reinterpret_frozen_batch(journal, monkeypatch):
    freeze()
    monkeypatch.setattr(batch.signal_publish, "STRATEGY_VERSION", "different")
    with pytest.raises(RuntimeError, match="VERSION_MISMATCH"):
        batch.resume(SLOT)
    assert journal["signals"] == []


def test_empty_batch_is_a_durable_decision(journal):
    batch.freeze(SLOT, [], {}, [{"symbol": "BTC"}])
    assert batch.resume(SLOT)["all_actionable"]
    assert batch.load(SLOT)["completed_at"] is not None


def test_publish_recovers_without_compute_or_kv_marker(monkeypatch):
    monkeypatch.setattr(app, "_cron_authorized", lambda: True)
    monkeypatch.setattr(batch.db, "db_enabled", lambda: True)
    monkeypatch.setattr(batch, "resume", lambda slot: {
        "payload": {"watchlist": []}, "persisted": 2, "duplicates": 0})
    monkeypatch.setattr(app, "_mark_slot_decided", lambda *a: None)
    monkeypatch.setattr(app, "_compute_recommendations", lambda: pytest.fail("must not recompute"))
    monkeypatch.setattr(app, "_hl_auto_execute_catch_up", lambda: {"mock": True})
    with app.app.test_request_context():
        result = app.api_cron_publish().get_json()
    assert result["ok"] and result["recovered"] and result["computed"] == 0


def test_recovery_failure_cannot_execute_hl(monkeypatch):
    monkeypatch.setattr(app, "_cron_authorized", lambda: True)
    monkeypatch.setattr(batch.db, "db_enabled", lambda: True)
    def fail(slot): raise RuntimeError("injected failure")
    monkeypatch.setattr(batch, "resume", fail)
    monkeypatch.setattr(app, "_hl_auto_execute_catch_up", lambda: pytest.fail("no trading"))
    with app.app.test_request_context():
        response, status = app.api_cron_publish()
    assert status == 503 and response.get_json()["error_code"] == "PUBLICATION_RECOVERY_FAILED"


def test_completed_batch_survives_missing_kv_marker(monkeypatch):
    monkeypatch.setattr(batch.db, "db_enabled", lambda: True)
    monkeypatch.setattr(batch, "load", lambda slot: {"completed_at": SLOT})
    assert app._slot_decided(SLOT)
    monkeypatch.setattr(batch, "load", lambda slot: {"completed_at": None})
    assert not app._slot_decided(SLOT)
