"""
Auto-exec must not be lost when the publishing request dies after persisting.

It used to run ONLY in the request that persisted the slot. A publish killed by
the 60s platform limit after persisting (seen on 26 Sep: 504, then the retry
answered SLOT_ALREADY_PUBLISHED) never traded, and neither did any later call.
Now an already-published slot still runs auto-exec during its first hour; the
exact-once claims / position reconcile / stale-entry guard make repeats safe.
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
    monkeypatch.setattr(app, "_slot_already_published", lambda *a, **k: True)
    return app.app.test_client()


def _slot_age(monkeypatch, minutes):
    monkeypatch.setattr(app, "_slot_start", lambda t: t - timedelta(minutes=minutes))


def test_already_published_slot_still_runs_auto_exec_early_in_the_slot(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_hl_auto_execute_run",
                        lambda: calls.append(1) or {"ok": True, "ran": True, "executed": 1})
    _slot_age(monkeypatch, 12)                         # the :12 retry
    body = client.post("/api/cron/publish", headers=H).get_json()
    assert body["skipped_reason"] == "SLOT_ALREADY_PUBLISHED"
    assert calls == [1] and body["hl_auto_execute"]["executed"] == 1


def test_no_catch_up_after_the_first_hour(client, monkeypatch):
    monkeypatch.setattr(app, "_hl_auto_execute_run",
                        lambda: pytest.fail("must not trade this late in the slot"))
    _slot_age(monkeypatch, app.AUTO_EXEC_CATCH_UP_MIN + 1)
    body = client.post("/api/cron/publish", headers=H).get_json()
    assert body["hl_auto_execute"]["reason"] == "PAST_CATCH_UP_WINDOW"


def test_an_hl_failure_never_fails_the_publish_response(client, monkeypatch):
    def boom():
        raise RuntimeError("hyperliquid down")
    monkeypatch.setattr(app, "_hl_auto_execute_run", boom)
    _slot_age(monkeypatch, 2)
    r = client.post("/api/cron/publish", headers=H)
    assert r.status_code == 200
    assert r.get_json()["hl_auto_execute"]["error_code"] == "HL_AUTO_EXECUTE_FAILED"


def test_the_slot_check_still_comes_before_the_compute():
    import inspect
    src = inspect.getsource(app.api_cron_publish)
    assert src.index("_slot_already_published()") < src.index("_compute_recommendations()")
