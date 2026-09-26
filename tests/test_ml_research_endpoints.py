import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import app
import auth
import db
import ml_dataset as dataset
import ml_research_jobs as jobs


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "enforcement_enabled", lambda: False)
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    monkeypatch.setenv("ML_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    monkeypatch.setattr(db, "db_configured", lambda: True)
    app.app.config["TESTING"] = True
    return app.app.test_client()


HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.mark.parametrize("kind", ["collect", "label"])
def test_requires_secret_even_with_feature_enabled(client, monkeypatch, kind):
    path = "/api/research/ml/" + kind
    assert client.post(path, json={}).status_code == 401
    monkeypatch.delenv("CRON_SECRET")
    assert client.post(path, json={}, headers=HEADERS).status_code == 401


def test_feature_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("ML_RESEARCH_ENABLED")
    assert client.post("/api/research/ml/collect", json={}, headers=HEADERS).status_code == 503


@pytest.mark.parametrize("kind", ["collect", "label"])
def test_post_only(client, kind):
    # The application's static catch-all handles otherwise-unmatched GETs.
    assert client.get("/api/research/ml/" + kind, headers=HEADERS).status_code in (404, 405)


@pytest.mark.parametrize("body", [{"write": "true"}, {"symbols": ["BTC", "ETH", "SOL"]},
    {"symbols": ["BTC", "BTC"]}, {"symbols": []}, {"symbols": [None]},
    {"source": "demo"}, {"environment": "production"}, {"limit": 11}, {"limit": True}])
def test_strict_request_limits(client, body):
    assert client.post("/api/research/ml/collect", json=body, headers=HEADERS).status_code == 400


def test_default_dry_run_and_production_namespace(client, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "collect", lambda *a: calls.append(a) or {"ok": True})
    assert client.post("/api/research/ml/collect", json={}, headers=HEADERS).status_code == 200
    assert calls == [(["BTC", "ETH"], "okx", "research", False)]


def test_preview_cannot_label_production_research(client, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    calls = []
    monkeypatch.setattr(jobs, "label", lambda *a: calls.append(a) or {"ok": True})
    assert client.post("/api/research/ml/label", json={"write": True}, headers=HEADERS).status_code == 200
    assert calls[0][2] == "research_preview"


def test_errors_are_sanitized(client, monkeypatch):
    def fail(*a):
        raise RuntimeError("postgresql://secret-password")
    monkeypatch.setattr(jobs, "collect", fail)
    response = client.post("/api/research/ml/collect", json={}, headers=HEADERS)
    assert response.status_code == 503
    assert "secret-password" not in response.get_data(as_text=True)


def test_cloud_fetch_is_one_request_without_fallback(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {"code": "0", "data": [["3600000", "100", "101", "99", "100", "10"]]})
    monkeypatch.setattr(jobs.requests, "get", get)
    assert jobs.fetch_candles("BTC", "okx")[0]["close"] == 100
    assert len(calls) == 1 and calls[0][1]["timeout"] == (3, 6)
    assert calls[0][1]["allow_redirects"] is False


def test_cloud_collection_dry_run_never_connects_db(monkeypatch):
    def fetch(symbol, source):
        hour = int(datetime.now(timezone.utc).timestamp() // 3600)
        return [{"timestamp": h * dataset.HOUR_MS, "open": 100, "high": 101,
                 "low": 99, "close": 100, "volume": 10} for h in range(hour - 64, hour)]
    monkeypatch.setattr(jobs, "fetch_candles", fetch)
    monkeypatch.setattr(db, "session_scope", lambda: pytest.fail("dry run attempted DB write"))
    result = jobs.collect(["BTC", "ETH"], "okx", "research")
    assert result["ok"] and result["counts"]["ready"] == 2


def test_pending_filters_precede_limit():
    calls = []
    class Session:
        def execute(self, sql, params):
            calls.append((str(sql), params))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    dataset.pending_labels(Session(), "research", datetime.now(timezone.utc), 10,
                           symbols=["BTC", "ETH"], source="okx")
    sql, params = calls[0]
    assert sql.index("f.source = :source") < sql.index("LIMIT")
    assert sql.index("f.symbol IN") < sql.index("LIMIT")
    assert params["source"] == "okx" and params["symbol_0"] == "BTC"


def test_label_batch_fetches_each_symbol_once_and_writes_only_on_opt_in(monkeypatch):
    records = [{"id": str(i), "symbol": "BTC", "entry_at_ms": dataset.milliseconds(datetime.now(timezone.utc)) - 5 * dataset.HOUR_MS}
               for i in range(3)]
    @contextmanager
    def session_scope():
        yield object()
    monkeypatch.setattr(db, "session_scope", session_scope)
    monkeypatch.setattr(dataset, "pending_labels", lambda *a, **kw: records)
    fetched, saved = [], []
    monkeypatch.setattr(jobs, "fetch_candles", lambda *a: fetched.append(a) or [])
    monkeypatch.setattr(dataset, "label_snapshot", lambda r, c, s, at: {"snapshot_id": r["id"]})
    monkeypatch.setattr(dataset, "save_label", lambda r, s: saved.append(r) or 1)
    assert jobs.label(["BTC"], "binance", "research")["attempted"] == 3
    assert len(fetched) == 1 and not saved
    result = jobs.label(["BTC"], "binance", "research", write=True)
    assert result["counts"]["inserted"] == 3 and len(saved) == 3


def test_label_fetch_failure_is_queued_not_a_fake_outcome(monkeypatch):
    @contextmanager
    def session_scope():
        yield object()
    monkeypatch.setattr(db, "session_scope", session_scope)
    row = {"id": "test", "symbol": "BTC", "entry_at_ms": dataset.milliseconds(datetime.now(timezone.utc)) - 5 * dataset.HOUR_MS}
    monkeypatch.setattr(dataset, "pending_labels", lambda *a, **kw: [row])
    def unavailable(*a):
        raise RuntimeError("provider secret")
    monkeypatch.setattr(jobs, "fetch_candles", unavailable)
    failed = []
    monkeypatch.setattr(dataset, "record_label_failure", lambda *a, **kw: failed.append(a[2]))
    monkeypatch.setattr(dataset, "save_label", lambda *a: pytest.fail("fake outcome written"))
    result = jobs.label(["BTC"], "okx", "research", write=True)
    assert not result["ok"] and failed == ["FETCH_FAILED"]
