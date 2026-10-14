"""
A slot whose only 69+ signals are HL extras (no top-3 pick reached the
channel floor, so no channel row) must still be traded: the publish request
runs auto-exec whenever it recorded the slot, and later requests catch up via
the slot-decided marker.
"""
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app                                                            # noqa: E402

H = {"x-cron-secret": "cron-" + "c" * 30}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "cron-" + "c" * 30)
    monkeypatch.setattr(app, "_slot_already_published", lambda *a, **k: False)
    return app.app.test_client()


def test_publish_runs_auto_exec_when_only_extras_were_recorded(client, monkeypatch):
    monkeypatch.setattr(app, "_slot_decided", lambda *a, **k: False)
    monkeypatch.setattr(app, "_compute_recommendations", lambda: {
        "slot": "8:00 AM", "recommendations": [], "actionable": True, "slot_current": True,
        "persistence": {"persisted": 0, "hl_extra": {"symbols": ["ALGO"]}}})
    calls = []
    monkeypatch.setattr(app, "_hl_auto_execute_run",
                        lambda: calls.append(1) or {"ok": True, "ran": True, "executed": 1})
    body = client.post("/api/cron/publish", headers=H).get_json()
    assert calls == [1] and body["hl_auto_execute"]["executed"] == 1


def test_no_run_when_the_compute_skipped_persistence(client, monkeypatch):
    monkeypatch.setattr(app, "_slot_decided", lambda *a, **k: False)
    monkeypatch.setattr(app, "_compute_recommendations", lambda: {
        "slot": "8:00 AM", "recommendations": [], "actionable": False, "slot_current": True,
        "persistence": {"persisted": 0, "skipped_reason": "DRY_RUN"}})
    monkeypatch.setattr(app, "_hl_auto_execute_run", lambda: pytest.fail("must not run"))
    assert client.post("/api/cron/publish", headers=H).status_code == 200


def test_a_decided_slot_catches_up_without_recomputing(client, monkeypatch):
    monkeypatch.setattr(app, "_slot_decided", lambda *a, **k: True)
    monkeypatch.setattr(app, "_compute_recommendations",
                        lambda: pytest.fail("a decided slot must not recompute"))
    monkeypatch.setattr(app, "_slot_start", lambda t: t - timedelta(minutes=10))
    calls = []
    monkeypatch.setattr(app, "_hl_auto_execute_run",
                        lambda: calls.append(1) or {"ok": True, "ran": True, "executed": 0})
    body = client.post("/api/cron/publish", headers=H).get_json()
    assert body["skipped_reason"] == "SLOT_ALREADY_PUBLISHED" and calls == [1]


def test_not_attempted_names_the_runs():
    code, text = app._hl_history_verdict(
        listed=True, placed=False, runs=[], stale_alert=False,
        slot_runs=[{"ran": True, "reason": "NO_CONFIRMED_SIGNALS", "attempted": 0}])
    assert code == "not_attempted" and "NO_CONFIRMED_SIGNALS (attempted 0)" in text
