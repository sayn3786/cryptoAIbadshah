"""
Voiding a CLOSED signal whose outcome came from bad data: an append-only row
in signal_voids (migration 017) that every statistic respects; the signal row
itself never changes.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_store as ss  # noqa: E402

SID = str(uuid.uuid4())


class _Res:
    def __init__(self, first=None, scalar=None):
        self._first, self._scalar = first, scalar

    def first(self):
        return self._first

    def scalar(self):
        return self._scalar


class Session:
    def __init__(self, status="SL_HIT", exists=True, already=False, table=True):
        self.status, self.exists, self.already, self.table = status, exists, already, table
        self.inserted = []

    def execute(self, stmt, params=None):
        q = str(stmt)
        if "to_regclass('signal_voids')" in q:
            return _Res(scalar=self.table)
        if q.strip().startswith("SELECT status"):
            return _Res(first=(self.status,) if self.exists else None)
        if "INSERT INTO signal_voids" in q:
            self.inserted.append(params)
            return _Res(first=None if self.already else (params["id"],))
        raise AssertionError(q)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(ss, "_VOIDS_TABLE", None)


def test_void_a_closed_signal():
    s = Session()
    out = ss.void_signal(SID, reason="stopped out by demo candles", session=s)
    assert out == {"signal_id": SID, "voided": True, "created": True, "status": "SL_HIT",
                   "reason": "stopped out by demo candles"}
    assert s.inserted[0]["reason"] == "stopped out by demo candles"


def test_idempotent():
    out = ss.void_signal(SID, reason="again please", session=Session(already=True))
    assert out["voided"] is True and out["created"] is False


@pytest.mark.parametrize("kw, exc", [
    ({"status": "OPEN"}, ss.InvalidTransition),
    ({"exists": False}, ss.SignalValidationError),
    ({"table": False}, ss.SignalValidationError),
])
def test_refusals(kw, exc):
    with pytest.raises(exc):
        ss.void_signal(SID, reason="bad data", session=Session(**kw))


def test_reason_is_required():
    with pytest.raises(ss.SignalValidationError):
        ss.void_signal(SID, reason=" x ", session=Session())


def test_void_clause(monkeypatch):
    monkeypatch.setattr(ss, "has_voids_table", lambda s: True)
    assert "signal_voids" in ss._void_clause(None)
    assert ss._void_clause(None, include_voided=True) == ""
    monkeypatch.setattr(ss, "has_voids_table", lambda s: False)
    assert ss._void_clause(None) == ""                       # before migration 017


def test_statistics_reads_exclude_voids():
    import inspect
    assert "_void_clause(s, include_voided)" in inspect.getsource(ss.list_signals)
    assert "_void_clause(s, include_voided)" in inspect.getsource(ss.list_closed_with_snapshots)


def test_migration_and_rollback_exist():
    root = os.path.join(os.path.dirname(__file__), "..", "database", "migrations")
    sql = open(os.path.join(root, "017_signal_voids.sql")).read()
    assert "CREATE TABLE IF NOT EXISTS signal_voids" in sql and "append-only" in sql
    assert os.path.exists(os.path.join(root, "rollback", "017_rollback_signal_voids.sql"))


def test_endpoint(monkeypatch):
    pytest.importorskip("flask")
    os.environ.setdefault("DISABLE_REC_SCHEDULER", "1")
    import app
    import db
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    c = app.app.test_client()
    assert c.post(f"/api/signals/{SID}/void", json={"reason": "bad data"}).status_code == 401
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")
    monkeypatch.setattr(db, "db_configured", lambda: True)
    H = {"x-cron-secret": "s3cret-s3cret-s3cret"}

    def ok(sid, reason):
        return {"signal_id": sid, "voided": True, "created": True, "status": "SL_HIT",
                "reason": reason}
    monkeypatch.setattr(ss, "void_signal", ok)
    monkeypatch.setattr(app, "_signal_store", lambda: ss)
    r = c.post(f"/api/signals/{SID}/void", json={"reason": "demo candles"}, headers=H)
    assert r.status_code == 200 and r.get_json()["voided"] is True

    def working(sid, reason):
        raise ss.InvalidTransition("OPEN is still working")
    monkeypatch.setattr(ss, "void_signal", working)
    assert c.post(f"/api/signals/{SID}/void", json={"reason": "x y z"},
                  headers=H).status_code == 409


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "void-signal.yml")).read()
    assert "workflow_dispatch" in wf and "/api/signals/$SIGNAL_ID/void" in wf
    assert "must be a uuid" in wf
