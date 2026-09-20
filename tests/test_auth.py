"""
Dashboard authentication: the login endpoints, the default-OFF enforcement gate,
session admin acceptance on the HL endpoints, and user_store validation.

No database: user_store's DB calls are stubbed, so these test the auth wiring
(sessions, gating, roles) rather than Postgres.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import auth as authmod                                              # noqa: E402
import user_store as us                                            # noqa: E402


def _app():
    pytest.importorskip("flask")
    import app
    return app


# ── user_store validation (no DB — validation runs before any query) ─────────

def test_create_user_rejects_bad_input():
    with pytest.raises(us.UserValidationError):
        us.create_user("", "longenough1")               # empty username
    with pytest.raises(us.UserValidationError):
        us.create_user("has space", "longenough1")      # space in username
    with pytest.raises(us.UserValidationError):
        us.create_user("alice", "short")                # password < 8
    with pytest.raises(us.UserValidationError):
        us.create_user("alice", "longenough1", role="superuser")   # bad role


# ── enforcement + secret switches ────────────────────────────────────────────

def test_enforcement_and_secret_flags(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    assert authmod.enforcement_enabled() is False
    assert authmod.secret_configured() is False
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 20)
    assert authmod.enforcement_enabled() is True
    assert authmod.secret_configured() is True


# ── login / me / logout ───────────────────────────────────────────────────────

def test_login_refused_without_secret(monkeypatch):
    app = _app()
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    r = app.app.test_client().post("/api/auth/login",
                                   json={"username": "a", "password": "bbbbbbbb"})
    assert r.status_code == 503 and r.get_json()["error_code"] == "AUTH_NOT_CONFIGURED"


def test_me_is_401_when_signed_out(monkeypatch):
    app = _app()
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    r = app.app.test_client().get("/api/auth/me")
    assert r.status_code == 401
    assert r.get_json()["authenticated"] is False


def test_login_success_sets_a_session(monkeypatch):
    app = _app()
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-123456")
    # Stub the DB-backed verification.
    monkeypatch.setattr(us, "verify_credentials",
                        lambda u, p: {"id": "u1", "username": "alice",
                                      "role": "admin"} if p == "correct-pass" else None)
    monkeypatch.setattr(us, "touch_login", lambda *_a, **_k: None)
    c = app.app.test_client()

    bad = c.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert bad.status_code == 401 and bad.get_json()["error_code"] == "INVALID_CREDENTIALS"

    ok = c.post("/api/auth/login", json={"username": "alice", "password": "correct-pass"})
    assert ok.status_code == 200 and ok.get_json()["user"]["role"] == "admin"

    me = c.get("/api/auth/me")               # same client keeps the session cookie
    assert me.status_code == 200 and me.get_json()["user"]["username"] == "alice"

    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401


# ── the enforcement gate ──────────────────────────────────────────────────────

def test_gate_off_by_default_lets_api_through(monkeypatch):
    app = _app()
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    # A protected endpoint's OWN auth still applies, but the gate does not add a
    # 401 — /api/auth/me is reachable and simply reports signed-out.
    assert app.app.test_client().get("/api/auth/me").status_code == 401


def test_gate_on_blocks_unauthenticated_api(monkeypatch):
    app = _app()
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    c = app.app.test_client()
    r = c.get("/api/hl/auto-status")
    assert r.status_code == 401 and r.get_json()["error_code"] == "AUTH_REQUIRED"
    # auth endpoints stay open under enforcement
    assert c.get("/api/auth/me").status_code == 401           # reachable, reports signed-out


def test_gate_bypassed_by_internal_token(monkeypatch):
    app = _app()
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    r = app.app.test_client().get("/api/hl/auto-status",
                                  headers={"x-cron-secret": "s3cret"})
    assert r.status_code == 200                                # gate + hl admin both satisfied


def test_gate_fail_closed_when_secret_unset(monkeypatch):
    # AUTH_REQUIRED on but CRON_SECRET unset must NOT open the gate (the gate uses
    # the fail-closed internal check, not the permissive cron check).
    app = _app()
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    r = app.app.test_client().get("/api/signals/active")
    assert r.status_code == 401 and r.get_json()["error_code"] == "AUTH_REQUIRED"


# ── user creation: admin session or bootstrap token ──────────────────────────

def test_create_user_endpoint_needs_admin_or_token(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    r = app.app.test_client().post("/api/auth/users",
                                   json={"username": "bob", "password": "bbbbbbbb"})
    assert r.status_code == 403


def test_create_user_endpoint_bootstraps_with_token(monkeypatch):
    app = _app()
    monkeypatch.setenv("HL_ADMIN_TOKEN", "hl-token-123")
    created = {}
    monkeypatch.setattr(us, "create_user",
                        lambda u, p, role="user": created.update(
                            {"username": u, "role": role}) or
                        {"id": "u9", "username": u, "role": role, "disabled": False})
    r = app.app.test_client().post(
        "/api/auth/users", headers={"x-hl-token": "hl-token-123"},
        json={"username": "bob", "password": "bbbbbbbb", "role": "admin"})
    assert r.status_code == 200 and r.get_json()["user"]["username"] == "bob"
    assert created == {"username": "bob", "role": "admin"}


def test_hl_admin_accepts_an_admin_session(monkeypatch):
    # An admin session authorizes the HL endpoints (so the UI buttons work),
    # even without the token.
    app = _app()
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-123456")
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(us, "verify_credentials",
                        lambda u, p: {"id": "u1", "username": "alice", "role": "admin"})
    monkeypatch.setattr(us, "touch_login", lambda *_a, **_k: None)
    c = app.app.test_client()
    c.post("/api/auth/login", json={"username": "alice", "password": "x"})
    # auto-status runs (returns 200) because the admin session satisfies _hl_admin_ok
    assert c.get("/api/hl/auto-status").status_code == 200
