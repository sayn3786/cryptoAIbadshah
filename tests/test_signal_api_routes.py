"""
Signal API routes: validation, auth and secret-safety.

Uses Flask's test client with the store stubbed, so these run without a
database and assert the HTTP contract rather than the SQL.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import db                                                            # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


class _FakeStore:
    """Minimal stand-in with the same surface the routes use."""
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100
    SignalValidationError = __import__("signal_store").SignalValidationError
    InvalidTransition = __import__("signal_store").InvalidTransition

    def __init__(self):
        self.calls = {}

    def list_active_signals(self, **kw):
        self.calls["active"] = kw
        return [{"id": "s1", "symbol": "BTC", "status": "OPEN"}]

    def list_signals(self, **kw):
        self.calls["list"] = kw
        return {"items": [], "limit": kw.get("limit"), "offset": kw.get("offset"),
                "total": 0, "has_more": False}

    def list_postmortems(self, **kw):
        self.calls["pm"] = kw
        return {"items": [], "limit": kw.get("limit"), "offset": 0,
                "total": 0, "has_more": False}

    def get_signal(self, sid, **kw):
        self.calls["get"] = sid
        return {"id": sid, "symbol": "BTC"} if sid == "known" else None

    def archive_signal(self, sid, **kw):
        self.calls["archive"] = sid
        return {"applied": True}

    def upsert_postmortem(self, sid, **kw):
        self.calls["upsert"] = (sid, kw)
        return {"signal_id": sid, **kw}

    def usage_report(self, **kw):
        return {"database_size_bytes": 1, "signals_total": 0}

    def attach_targets(self, rows, **kw):
        self.calls["attach"] = len(rows)
        for r in rows:
            r.setdefault("targets", [])
        return rows


@pytest.fixture()
def fake(monkeypatch):
    f = _FakeStore()
    monkeypatch.setattr(appmod, "_signal_store", lambda: f)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x?sslmode=disable")
    return f


# ── Availability ────────────────────────────────────────────────────────────

def test_routes_503_when_no_database_configured(client):
    for path in ("/api/signals/active", "/api/signals/history",
                 "/api/signals/outcomes", "/api/signals/postmortems",
                 "/api/signals/known"):
        r = client.get(path)
        assert r.status_code == 503, path
        assert r.get_json()["error_code"] == "DB_NOT_CONFIGURED"


def test_health_reports_unconfigured_without_leaking_anything(client):
    r = client.get("/api/db/health")
    assert r.status_code == 503
    body = r.get_json()
    assert body["ok"] is False
    assert body["configured"] is False
    for forbidden in ("dsn", "url", "host", "user", "password"):
        assert forbidden not in {k.lower() for k in body}


def test_health_never_exposes_the_connection_string(client, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://admin:sup3rs3cret@db-pooler.example.neon.tech/prod?sslmode=require")
    db.reset_engine()
    try:
        blob = repr(client.get("/api/db/health").get_json())
        assert "sup3rs3cret" not in blob
        assert "db-pooler.example.neon.tech" not in blob
        assert "admin" not in blob
    finally:
        db.reset_engine()


# ── Reads ───────────────────────────────────────────────────────────────────

def test_active_returns_items(client, fake):
    r = client.get("/api/signals/active")
    assert r.status_code == 200
    assert r.get_json()["count"] == 1


def test_history_passes_filters_through(client, fake):
    r = client.get("/api/signals/history?symbol=btc&direction=LONG&timeframe=2H"
                   "&status=OPEN&status=TP_HIT&strategy_version=v1&limit=10&offset=5")
    assert r.status_code == 200
    call = fake.calls["list"]
    assert call["symbol"] == "btc"
    assert call["direction"] == "LONG"
    assert call["statuses"] == ["OPEN", "TP_HIT"]
    assert call["strategy_version"] == "v1"
    assert call["limit"] == 10 and call["offset"] == 5


def test_history_hides_archived_by_default(client, fake):
    client.get("/api/signals/history")
    assert fake.calls["list"]["include_archived"] is False
    client.get("/api/signals/history?include_archived=1")
    assert fake.calls["list"]["include_archived"] is True


def test_limit_is_capped_and_garbage_falls_back(client, fake):
    client.get("/api/signals/history?limit=99999")
    assert fake.calls["list"]["limit"] == _FakeStore.MAX_PAGE_SIZE
    client.get("/api/signals/history?limit=notanumber&offset=-5")
    assert fake.calls["list"]["limit"] == _FakeStore.DEFAULT_PAGE_SIZE
    assert fake.calls["list"]["offset"] == 0


def test_bad_filter_is_a_400_not_a_500(client, fake, monkeypatch):
    def boom(**kw):
        raise _FakeStore.SignalValidationError("unknown status filter: NOPE")
    monkeypatch.setattr(fake, "list_signals", boom)
    r = client.get("/api/signals/history?status=NOPE")
    assert r.status_code == 400
    assert r.get_json()["error_code"] == "BAD_REQUEST"


def test_outcomes_requests_only_terminal_statuses(client, fake):
    client.get("/api/signals/outcomes")
    assert set(fake.calls["list"]["statuses"]) == {
        "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"}


def test_detail_404_for_unknown_signal(client, fake):
    assert client.get("/api/signals/known").status_code == 200
    r = client.get("/api/signals/missing")
    assert r.status_code == 404 and r.get_json()["error_code"] == "NOT_FOUND"


def test_database_error_is_a_sanitized_503(client, fake, monkeypatch):
    def boom(**kw):
        raise RuntimeError("FATAL: password authentication failed for user 'admin' "
                           "postgresql://admin:hunter2@host/db")
    monkeypatch.setattr(fake, "list_active_signals", boom)
    r = client.get("/api/signals/active")
    assert r.status_code == 503
    blob = repr(r.get_json())
    assert r.get_json()["error_code"] == "DB_UNAVAILABLE"
    assert "hunter2" not in blob and "postgresql://" not in blob


# ── Mutations require internal auth ─────────────────────────────────────────

MUTATIONS = (("/api/signals/known/archive", {}),
             ("/api/signals/known/postmortem", {"outcome": "LOSS"}))


@pytest.mark.parametrize("path,body", MUTATIONS)
def test_mutations_closed_when_no_secret_configured(client, fake, path, body):
    # Fail SAFE: with no CRON_SECRET set, mutation endpoints stay closed rather
    # than open to the world.
    r = client.post(path, json=body)
    assert r.status_code == 401
    assert r.get_json()["error_code"] == "FORBIDDEN"


@pytest.mark.parametrize("path,body", MUTATIONS)
def test_mutations_reject_a_wrong_secret(client, fake, monkeypatch, path, body):
    monkeypatch.setenv("CRON_SECRET", "right")
    r = client.post(path, json=body, headers={"x-cron-secret": "wrong"})
    assert r.status_code == 401


@pytest.mark.parametrize("path,body", MUTATIONS)
def test_mutations_accept_bearer_and_header_forms(client, fake, monkeypatch, path, body):
    monkeypatch.setenv("CRON_SECRET", "right")
    assert client.post(path, json=body,
                       headers={"x-cron-secret": "right"}).status_code == 200
    assert client.post(path, json=body,
                       headers={"authorization": "Bearer right"}).status_code == 200


def test_archive_conflict_is_409(client, fake, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right")

    def boom(sid, **kw):
        raise _FakeStore.InvalidTransition("refusing to archive a signal in OPEN")

    monkeypatch.setattr(fake, "archive_signal", boom)
    r = client.post("/api/signals/known/archive", headers={"x-cron-secret": "right"})
    assert r.status_code == 409
    assert r.get_json()["error_code"] == "INVALID_TRANSITION"


def test_postmortem_requires_an_outcome(client, fake, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right")
    r = client.post("/api/signals/known/postmortem", json={},
                    headers={"x-cron-secret": "right"})
    assert r.status_code == 400
    assert r.get_json()["error_code"] == "BAD_REQUEST"


def test_postmortem_defaults_the_strategy_version(client, fake, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right")
    client.post("/api/signals/known/postmortem", json={"outcome": "WIN"},
                headers={"x-cron-secret": "right"})
    _, kw = fake.calls["upsert"]
    assert kw["strategy_version"], "a postmortem must record which rules it judges"


def test_usage_report_is_internal_only(client, fake, monkeypatch):
    assert client.get("/api/db/usage").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "right")
    assert client.get("/api/db/usage",
                      headers={"x-cron-secret": "right"}).status_code == 200


# ── Tracker ─────────────────────────────────────────────────────────────────

def test_tracker_needs_a_database(client):
    r = client.get("/api/signals/tracker")
    assert r.status_code == 503
    assert r.get_json()["error_code"] == "DB_NOT_CONFIGURED"


def test_tracker_returns_live_and_closed_sections(client, fake, monkeypatch):
    monkeypatch.setattr(appmod, "get_analysis",
                        lambda sym, tf, *a, **k: {"live_price": 101.0})
    body = client.get("/api/signals/tracker").get_json()
    assert set(body) >= {"live", "closed", "summary", "window_days", "environment"}
    assert body["window_days"] == 3
    assert [r["symbol"] for r in body["live"]] == ["BTC"]


def test_tracker_asks_only_for_terminal_statuses_in_the_closed_list(client, fake,
                                                                    monkeypatch):
    monkeypatch.setattr(appmod, "get_analysis", lambda *a, **k: {})
    client.get("/api/signals/tracker")
    assert set(fake.calls["list"]["statuses"]) == {
        "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"}


def test_tracker_window_is_bounded(client, fake, monkeypatch):
    monkeypatch.setattr(appmod, "get_analysis", lambda *a, **k: {})
    assert client.get("/api/signals/tracker?days=999").get_json()["window_days"] == 30
    assert client.get("/api/signals/tracker?days=junk").get_json()["window_days"] == 3


def test_tracker_survives_a_price_lookup_failure(client, fake, monkeypatch):
    # Market data being down must not take the tracker down with it — the rows
    # still render, just without live progress.
    def boom(*a, **k):
        raise RuntimeError("binance unreachable")
    monkeypatch.setattr(appmod, "get_analysis", boom)
    body = client.get("/api/signals/tracker").get_json()
    assert body["live"][0]["live_price"] is None
    assert body["live"][0]["move_pct"] is None


def test_tracker_is_read_only(client, fake, monkeypatch):
    # It reports what the monitor recorded; it must never advance a signal.
    monkeypatch.setattr(appmod, "get_analysis", lambda *a, **k: {})
    for name in ("record_target_hit", "record_stop_loss_hit", "expire_signal"):
        monkeypatch.setattr(fake, name, lambda *a, **k: pytest.fail(
            f"tracker called {name}"), raising=False)
    assert client.get("/api/signals/tracker").status_code == 200


# ── Monitor ─────────────────────────────────────────────────────────────────

def test_monitor_is_internal_only(client, fake, monkeypatch):
    assert client.post("/api/signals/monitor").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "right")
    monkeypatch.setattr(appmod, "_fetch_closed_spot", lambda sym, tf: [])
    r = client.post("/api/signals/monitor", headers={"x-cron-secret": "right"})
    assert r.status_code == 200
    assert set(r.get_json()) >= {"checked", "targets_hit", "stopped", "expired"}


def test_monitor_reports_rather_than_crashing_on_a_data_failure(client, fake,
                                                                monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right")

    def boom(sym, tf):
        raise RuntimeError("no candles")
    monkeypatch.setattr(appmod, "_fetch_closed_spot", boom)
    r = client.post("/api/signals/monitor", headers={"x-cron-secret": "right"})
    assert r.status_code == 200, "one bad symbol must not fail the whole run"
    assert r.get_json()["errors"]


# ── Health route must surface the migration state distinctly ────────────────

def test_health_route_reports_not_migrated_as_its_own_code(client, monkeypatch):
    import db as _db
    monkeypatch.setattr(_db, "healthcheck", lambda: {
        "configured": True, "required": False, "driver": "psycopg3",
        "pooled_endpoint": True, "tls": True,
        "ok": False, "reachable": True, "migrated": False,
        "error_code": "DB_NOT_MIGRATED", "migrations_applied": [],
        "missing_tables": ["signals"],
        "hint": "Connection works. Run database/migrations/… once.",
    })
    r = client.get("/api/db/health")
    body = r.get_json()
    assert r.status_code == 503, "persistence really is unavailable"
    assert body["reachable"] is True, "must not look like a connection fault"
    assert body["error_code"] == "DB_NOT_MIGRATED"
    assert "Run database/migrations" in body["hint"]


def test_health_route_reports_unreachable_distinctly(client, monkeypatch):
    import db as _db
    monkeypatch.setattr(_db, "healthcheck", lambda: {
        "configured": True, "required": False, "driver": "psycopg3",
        "pooled_endpoint": True, "tls": True,
        "ok": False, "reachable": False, "migrated": False,
        "error_code": "DB_UNAVAILABLE", "hint": "Check DATABASE_URL…",
    })
    body = client.get("/api/db/health").get_json()
    assert body["reachable"] is False
    assert body["error_code"] == "DB_UNAVAILABLE"


# ── Environment scoping ─────────────────────────────────────────────────────

def test_active_is_scoped_to_this_deployment_by_default(client, fake, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    body = client.get("/api/signals/active").get_json()
    assert fake.calls["active"]["environment"] is None, \
        "no filter argument means 'this environment' — the store decides"
    assert body["environment"] == "production"


def test_environment_argument_is_passed_through(client, fake):
    client.get("/api/signals/active?environment=all")
    assert fake.calls["active"]["environment"] == "all"
    client.get("/api/signals/history?environment=preview")
    assert fake.calls["list"]["environment"] == "preview"
    client.get("/api/signals/outcomes?environment=preview")
    assert fake.calls["list"]["environment"] == "preview"


@pytest.mark.parametrize("path", ["/api/signals/active",
                                  "/api/signals/history",
                                  "/api/signals/outcomes"])
def test_a_junk_environment_is_a_400_not_a_500(client, fake, monkeypatch, path):
    def boom(**kw):
        raise _FakeStore.SignalValidationError("environment filter must be a slug")
    monkeypatch.setattr(fake, "list_active_signals", boom)
    monkeypatch.setattr(fake, "list_signals", boom)
    r = client.get(path + "?environment=%27%3B+DROP+TABLE+signals")
    assert r.status_code == 400
    assert r.get_json()["error_code"] == "BAD_REQUEST"


def test_health_route_200_only_when_fully_healthy(client, monkeypatch):
    import db as _db
    monkeypatch.setattr(_db, "healthcheck", lambda: {
        "configured": True, "required": True, "driver": "psycopg3",
        "pooled_endpoint": True, "tls": True,
        "ok": True, "reachable": True, "migrated": True,
        "migrations_applied": ["001"],
    })
    r = client.get("/api/db/health")
    assert r.status_code == 200
    assert r.get_json()["migrations_applied"] == ["001"]
