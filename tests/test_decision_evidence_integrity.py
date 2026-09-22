"""Evidence collection must stay bounded, isolated, and honest about coverage."""
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import app
import db
import decision_audit
import signal_store


def test_rejected_candidate_keeps_reason_and_normalized_funding():
    record = decision_audit.candidate_record("ETH", {"strength": 30}, {
        "strength": 60, "sig": {"direction": "LONG"},
        "analysis": {"funding_rate": {"current": 0.001, "interval_hours": 4},
                     "api_key": "must-not-store", "candles": [{"close": 1}]}},
        {"ok": False, "reason": "TF_DISAGREE"})
    assert record["screen_ok"] is False
    assert record["reason"] == "TF_DISAGREE"
    assert record["snapshot"]["market_context"]["funding_rate"] == 0.002
    assert "must-not-store" not in json.dumps(record)


@pytest.mark.parametrize("table_exists", [False, True])
def test_audit_bulk_insert_is_first_observed_and_environment_scoped(monkeypatch, table_exists):
    calls = []
    class Session:
        def execute(self, sql, params=None):
            calls.append((str(sql), params))
            return SimpleNamespace(scalar=lambda: table_exists)
    @contextmanager
    def session_scope():
        yield Session()
    monkeypatch.setattr(db, "db_configured", lambda: True)
    monkeypatch.setattr(db, "session_scope", session_scope)
    records = [{"symbol": "ETH", "api_key": "secret"}, {"symbol": "SOL"}]
    result = decision_audit.persist(records, {"ETH"}, datetime(2026, 9, 22, 7, 15, tzinfo=timezone.utc))
    if not table_exists:
        assert result == {"ok": False, "reason": "MIGRATION_012_REQUIRED"}
        assert len(calls) == 1
        return
    assert result["ok"]
    assert len(calls) == 2
    sql, params = calls[1]
    assert "ON CONFLICT (environment, strategy_version, slot_at, symbol) DO NOTHING" in sql
    assert len(params) == 2 and params[0]["slot"].hour == 4
    assert params[0]["env"] and params[0]["version"]
    assert "secret" not in params[0]["payload"]
    assert json.loads(params[0]["payload"])["selected"] is True
    assert json.loads(params[1]["payload"])["selected"] is False
    assert "selected" not in records[0]


def test_audit_missing_db_does_not_open_connection(monkeypatch):
    monkeypatch.setattr(db, "db_configured", lambda: False)
    assert decision_audit.persist([], set(), datetime.now(timezone.utc))["reason"] == "DB_NOT_CONFIGURED"


def test_analytical_sql_filters_before_paging(monkeypatch):
    calls = []
    monkeypatch.setattr(signal_store, "_environment_clause", lambda s, e: (" AND environment = :env", {"env": "preview"}))
    class Session:
        def execute(self, sql, params):
            calls.append((str(sql), params))
            return SimpleNamespace(all=lambda: [])
    assert signal_store.list_closed_with_snapshots(session=Session(), limit=500,
        offset=500, min_strength=60, max_strength=70, include_archived=True) == []
    sql, params = calls[0]
    assert sql.index("confidence_score >= :min_strength") < sql.index("LIMIT")
    assert sql.index("confidence_score < :max_strength") < sql.index("LIMIT")
    assert "id DESC" in sql and "OFFSET :offset" in sql
    assert "archived_at IS NULL" not in sql
    assert params["limit"] == 500 and params["offset"] == 500
    assert params["env"] == "preview"


@pytest.mark.parametrize("route,module", [("market-snapshot", "market_metrics_store"), ("etf-snapshot", "etf_store")])
@pytest.mark.parametrize("ok,status", [(True, 200), (False, 503)])
def test_snapshot_failure_is_not_a_successful_http_response(monkeypatch, route, module, ok, status):
    import importlib
    monkeypatch.setattr(app, "_cron_authorized", lambda: True)
    monkeypatch.setattr(importlib.import_module(module), "snapshot_daily", lambda: {"ok": ok})
    with app.app.test_client() as client:
        response = client.post("/api/cron/" + route)
    assert response.status_code == status
    assert response.get_json()["ok"] is ok


def test_sample_window_does_not_claim_lifetime_or_known_total():
    with app.app.test_request_context("/?limit=2&offset=4"):
        sample = app._report_sample_window([{"closed_at": "2026-09-22"}, {"closed_at": "2026-09-21"}])
    assert sample["possibly_truncated"] is True and sample["next_offset"] == 6
    assert sample["total_matching_rows"] is None
    assert sample["oldest_closed_at"] == "2026-09-21"

