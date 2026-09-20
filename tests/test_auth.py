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
    monkeypatch.setattr(us, "revalidate",
                        lambda uid: ("ok", {"id": "u1", "username": "alice", "role": "admin"}))
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


def _admin_client(app, monkeypatch, role="admin"):
    """A test client with a signed-in session of the given role. Stubs revalidate
    to 'ok' so the DB-backed session check (fail-closed) passes; tests that want a
    revoked/changed session override us.revalidate afterwards."""
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-123456")
    monkeypatch.setattr(us, "verify_credentials",
                        lambda u, p: {"id": "me", "username": "admin", "role": role})
    monkeypatch.setattr(us, "touch_login", lambda *_a, **_k: None)
    monkeypatch.setattr(us, "revalidate",
                        lambda uid: ("ok", {"id": "me", "username": "admin", "role": role}))
    c = app.app.test_client()
    c.post("/api/auth/login", json={"username": "admin", "password": "x"})
    return c


# ── user management: reset / role / disable / delete ─────────────────────────

def test_management_requires_admin_session(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    c = app.app.test_client()   # signed out
    assert c.post("/api/auth/users/x/password", json={"password": "abcdefgh"}).status_code == 403
    assert c.post("/api/auth/users/x/role", json={"role": "user"}).status_code == 403
    assert c.post("/api/auth/users/x/disable", json={"disabled": True}).status_code == 403
    assert c.delete("/api/auth/users/x").status_code == 403


def test_management_bootstrap_token_is_NOT_accepted(monkeypatch):
    # Mutating existing accounts needs a real admin SESSION — the bootstrap token
    # only creates the first admin, it does not manage users.
    app = _app()
    monkeypatch.setenv("HL_ADMIN_TOKEN", "hl-token-123")
    r = app.app.test_client().post("/api/auth/users/x/password",
                                   headers={"x-hl-token": "hl-token-123"},
                                   json={"password": "abcdefgh"})
    assert r.status_code == 403


def test_admin_can_reset_role_disable_delete(monkeypatch):
    app = _app()
    c = _admin_client(app, monkeypatch)
    monkeypatch.setattr(us, "set_password", lambda uid, pw: {"id": uid, "username": "bob"})
    monkeypatch.setattr(us, "set_role", lambda uid, role: {"id": uid, "role": role})
    monkeypatch.setattr(us, "set_disabled", lambda uid, d: {"id": uid, "disabled": d})
    monkeypatch.setattr(us, "delete_user", lambda uid: True)

    assert c.post("/api/auth/users/u2/password", json={"password": "abcdefgh"}).status_code == 200
    assert c.post("/api/auth/users/u2/role", json={"role": "admin"}).get_json()["user"]["role"] == "admin"
    assert c.post("/api/auth/users/u2/disable", json={"disabled": True}).get_json()["user"]["disabled"] is True
    assert c.delete("/api/auth/users/u2").get_json()["deleted"] is True


def test_disable_requires_a_real_boolean(monkeypatch):
    # A truthy string like "false" must be rejected, not silently disable.
    app = _app()
    c = _admin_client(app, monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(us, "set_disabled",
                        lambda uid, d: called.__setitem__("n", called["n"] + 1) or {"id": uid})
    bad = c.post("/api/auth/users/u2/disable", json={"disabled": "false"})
    assert bad.status_code == 400 and bad.get_json()["error_code"] == "BAD_PARAMS"
    assert called["n"] == 0                               # never reached the store
    ok = c.post("/api/auth/users/u2/disable", json={"disabled": True})
    assert ok.status_code == 200 and called["n"] == 1


def test_non_string_fields_are_400_not_500(monkeypatch):
    # Syntactically valid JSON with a non-string role/password must be a clean
    # 400, never an AttributeError-driven 500.
    app = _app()
    c = _admin_client(app, monkeypatch)
    monkeypatch.setattr(us, "set_role", lambda uid, role: {"id": uid, "role": role})
    monkeypatch.setattr(us, "set_password", lambda uid, pw: {"id": uid})
    monkeypatch.setattr(us, "create_user",
                        lambda u, p, role="user": {"id": "x", "username": u, "role": role})
    assert c.post("/api/auth/users/u2/role", json={"role": 1}).status_code == 400
    assert c.post("/api/auth/users/u2/password", json={"password": 123}).status_code == 400
    assert c.post("/api/auth/users", json={"username": 1, "password": "abcdefgh"}).status_code == 400
    assert c.post("/api/auth/password",
                  json={"current_password": "x", "new_password": 9}).status_code == 400


def test_last_admin_guard_returns_409(monkeypatch):
    app = _app()
    c = _admin_client(app, monkeypatch)

    def boom(*_a, **_k):
        raise us.LastAdminError("cannot demote the last enabled admin")
    monkeypatch.setattr(us, "set_role", boom)
    r = c.post("/api/auth/users/me/role", json={"role": "user"})
    assert r.status_code == 409 and r.get_json()["error_code"] == "LAST_ADMIN"


def test_change_own_password_checks_current(monkeypatch):
    app = _app()
    c = _admin_client(app, monkeypatch, role="user")

    def _change(uid, cur, new):
        if cur != "right-now":
            raise us.BadCurrentPassword()
        return {"id": uid, "username": "admin", "role": "user", "session_version": 1}
    monkeypatch.setattr(us, "change_own_password", _change)

    wrong = c.post("/api/auth/password",
                   json={"current_password": "nope", "new_password": "abcdefgh"})
    assert wrong.status_code == 401 and wrong.get_json()["error_code"] == "INVALID_CREDENTIALS"

    ok = c.post("/api/auth/password",
                json={"current_password": "right-now", "new_password": "abcdefgh"})
    assert ok.status_code == 200 and ok.get_json()["ok"] is True


def test_password_reset_refused_without_migration_010(monkeypatch):
    # If session_version is unavailable the reset cannot revoke sessions, so it is
    # refused (503) rather than falsely reporting success.
    app = _app()
    c = _admin_client(app, monkeypatch)

    def _raise(*_a, **_k):
        raise us.MigrationRequired("run migration 010")
    monkeypatch.setattr(us, "set_password", _raise)
    r = c.post("/api/auth/users/u2/password", json={"password": "abcdefgh"})
    assert r.status_code == 503 and r.get_json()["error_code"] == "MIGRATION_REQUIRED"


def test_change_own_password_requires_login(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    r = app.app.test_client().post("/api/auth/password",
                                   json={"current_password": "a", "new_password": "abcdefgh"})
    assert r.status_code == 401


# ── sessions are revalidated against the live table (Codex P1) ───────────────

def test_a_revoked_session_loses_access_immediately(monkeypatch):
    # An admin signs in, then is disabled/deleted: the very next request must be
    # denied even though the signed cookie still says "admin".
    app = _app()
    c = _admin_client(app, monkeypatch)
    assert c.get("/api/auth/me").status_code == 200        # still ok while "unknown"/valid
    monkeypatch.setattr(us, "revalidate", lambda uid: ("revoked", None))
    assert c.get("/api/auth/me").status_code == 401         # revoked → signed out
    # and the HL admin surface no longer accepts the dead session
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    assert c.get("/api/hl/auto-status").status_code == 401


def test_role_change_takes_effect_from_the_db_not_the_cookie(monkeypatch):
    # Cookie was minted as admin, but the DB now says user → admin-only routes deny.
    app = _app()
    c = _admin_client(app, monkeypatch)
    monkeypatch.setattr(us, "revalidate",
                        lambda uid: ("ok", {"id": "me", "username": "admin", "role": "user"}))
    me = c.get("/api/auth/me")
    assert me.status_code == 200 and me.get_json()["user"]["role"] == "user"
    # a management endpoint (admin-only) is now refused for this demoted session
    assert c.post("/api/auth/users/x/disable", json={"disabled": True}).status_code == 403


def test_password_reset_revokes_existing_sessions(monkeypatch):
    # After a password reset the DB session_version is bumped; a cookie minted
    # before the reset (sv=0) no longer matches and the session is revoked.
    app = _app()
    c = _admin_client(app, monkeypatch)                       # cookie sv=0
    assert c.get("/api/auth/me").status_code == 200
    monkeypatch.setattr(us, "revalidate",
                        lambda uid: ("ok", {"id": "me", "username": "admin",
                                            "role": "admin", "session_version": 1}))
    assert c.get("/api/auth/me").status_code == 401           # stale version → revoked


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
    monkeypatch.setattr(us, "revalidate",
                        lambda uid: ("ok", {"id": "u1", "username": "alice", "role": "admin"}))
    c = app.app.test_client()
    c.post("/api/auth/login", json={"username": "alice", "password": "x"})
    # auto-status runs (returns 200) because the admin session satisfies _hl_admin_ok
    assert c.get("/api/hl/auto-status").status_code == 200


def test_unknown_revalidation_fails_closed(monkeypatch):
    # If the account state cannot be checked (DB down / table missing), the
    # session is treated as signed-out — never trusted from the cookie's role.
    app = _app()
    c = _admin_client(app, monkeypatch)
    assert c.get("/api/auth/me").status_code == 200          # ok while revalidation says ok
    monkeypatch.setattr(us, "revalidate", lambda uid: ("unknown", None))
    assert c.get("/api/auth/me").status_code == 401          # unknown → fail closed
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    assert c.get("/api/hl/auto-status").status_code == 401   # HL surface denied too
