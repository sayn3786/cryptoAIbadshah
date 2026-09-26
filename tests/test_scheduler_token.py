"""
SCHEDULER_TOKEN — the narrow credential for the Cloudflare scheduler Worker.

It must open POST to exactly the scheduled-job paths (publish, monitor, daily
Telegram run, pattern alerts, data snapshots, ML research collect/label),
including through the AUTH_REQUIRED login gate, and NOTHING else: not order placement, not the position manager, not other
cron endpoints that share the same CRON_SECRET check, not GET. Fail-closed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app                                                            # noqa: E402

TOKEN = "s" * 40
H = {"x-scheduler-token": TOKEN}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("SCHEDULER_TOKEN", TOKEN)
    monkeypatch.setenv("CRON_SECRET", "cron-" + "c" * 30)
    monkeypatch.setenv("HL_ADMIN_TOKEN", "admin-" + "a" * 30)
    monkeypatch.setenv("HL_MANAGE_TOKEN", "m" * 40)
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    return app.app.test_client()


@pytest.mark.parametrize("path,patch", [
    ("/api/cron/publish", "_compute_recommendations"),
    ("/api/cron/daily", "_compute_recommendations"),
])
def test_token_passes_auth_on_publish_and_daily(client, monkeypatch, path, patch):
    # The heavy compute raises a sentinel: reaching it proves auth passed.
    class Reached(Exception):
        pass
    def boom(*a, **k):
        raise Reached()
    monkeypatch.setattr(app, patch, boom)
    monkeypatch.setattr(app, "_slot_already_published", lambda *a, **k: False, raising=False)
    # daily catches the compute error and continues to Twitter: keep it offline.
    monkeypatch.setattr(app, "_dispatch_once", lambda *a, **k: {"skipped": "test"})
    try:
        r = client.post(path, headers=H)
        assert r.status_code not in (401, 403), r.get_data(as_text=True)
    except Reached:
        pass


def test_token_passes_auth_on_monitor(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "db_configured", lambda: False)
    r = client.post("/api/signals/monitor", headers=H)
    assert r.status_code not in (401, 403), r.get_data(as_text=True)


def test_token_passes_auth_on_pattern_alerts(client, monkeypatch):
    r = client.post("/api/patterns/alert?dry=1&symbols=NONE", headers=H)
    assert r.status_code not in (401, 403), r.get_data(as_text=True)


def test_without_token_the_same_paths_are_refused(client):
    for path in ("/api/cron/publish", "/api/signals/monitor", "/api/cron/daily",
                 "/api/patterns/alert"):
        assert client.post(path).status_code == 401, path


@pytest.mark.parametrize("method,path", [
    ("post", "/api/hl/auto-execute"),
    ("post", "/api/hl/execute"),
    ("post", "/api/hl/manage"),                  # has its own token
    ("get", "/api/hl/account"),
    ("get", "/api/hl/positions"),
    ("get", "/api/cron/etf-snapshot"),           # GET: the token is POST-only
    ("post", "/api/telegram/send"),
    ("get", "/api/recommendations"),
    ("get", "/api/auth/users"),
])
def test_token_opens_nothing_else(client, method, path):
    r = getattr(client, method)(path, headers=H)
    assert r.status_code in (401, 403), path


def test_get_is_not_opened(client):
    # publish/daily accept GET for Vercel's own cron, but the token is POST-only.
    assert client.get("/api/cron/publish", headers=H).status_code == 401
    assert client.get("/api/cron/daily", headers=H).status_code == 401


def test_wrong_token_is_rejected(client):
    r = client.post("/api/cron/publish", headers={"x-scheduler-token": "s" * 39 + "x"})
    assert r.status_code == 401


@pytest.mark.parametrize("value", ["", "too-short"])
def test_unset_or_weak_token_fails_closed(client, monkeypatch, value):
    monkeypatch.setenv("SCHEDULER_TOKEN", value)
    r = client.post("/api/cron/publish", headers={"x-scheduler-token": value or "x"})
    assert r.status_code == 401


def test_manager_token_does_not_open_scheduler_paths(client):
    r = client.post("/api/cron/publish", headers={"x-hl-manage-token": "m" * 40})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/api/cron/etf-snapshot", "/api/cron/market-snapshot",
                                  "/api/cron/tao-snapshot"])
def test_token_passes_auth_on_snapshots(client, monkeypatch, path):
    # Reaching the handler (whatever it then does) proves auth passed; make the
    # snapshot work itself fail fast and offline.
    import db
    monkeypatch.setattr(db, "db_configured", lambda: False)
    r = client.post(path, headers=H)
    assert r.status_code not in (401, 403), r.get_data(as_text=True)


@pytest.mark.parametrize("kind", ["collect", "label"])
def test_token_passes_auth_on_ml_research(client, monkeypatch, kind):
    monkeypatch.delenv("ML_RESEARCH_ENABLED", raising=False)     # feature off → 503, not 401
    r = client.post(f"/api/research/ml/{kind}", headers=H, json={})
    assert r.status_code == 503 and r.get_json()["error_code"] != "FORBIDDEN"
