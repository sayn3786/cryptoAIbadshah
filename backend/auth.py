"""
Dashboard authentication — signed-cookie sessions on top of user_store.

Flask's session is a signed (not encrypted) cookie: we store only the user id,
username and role in it, never the password or its hash, and the signature (from
app.secret_key / APP_SECRET_KEY) is what makes it tamper-evident. Serverless-
friendly: no server-side session store to keep in sync across cold starts.

Enforcement is a SEPARATE, default-OFF switch (AUTH_REQUIRED). With it off,
nothing changes for existing traffic — login still works, but the app is not
gated, so this can deploy before any account exists without locking anyone out.
Turn it on only after the first admin is created.
"""
from __future__ import annotations

import functools
import os
from typing import Any, Callable, Dict, Optional

from flask import jsonify, session

import user_store

_SESSION_KEY = "u"


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def enforcement_enabled() -> bool:
    """Whether the app should REQUIRE a session for protected routes. Default OFF
    so deploying the feature does not lock the owner out before a user exists."""
    return _flag("AUTH_REQUIRED")


def secret_configured() -> bool:
    """A stable APP_SECRET_KEY must exist to sign session cookies. Without it,
    login is refused rather than signing with an unstable/insecure key."""
    return bool((os.getenv("APP_SECRET_KEY") or "").strip())


# ── session read/write ───────────────────────────────────────────────────────

def set_session(user: Dict[str, Any]) -> None:
    session.permanent = True
    session[_SESSION_KEY] = {"id": str(user.get("id")),
                             "username": user.get("username"),
                             "role": user.get("role") or "user"}


def clear_session() -> None:
    session.pop(_SESSION_KEY, None)


def current_user() -> Optional[Dict[str, Any]]:
    """The signed-in user from the cookie, or None. This trusts the cookie's
    SIGNATURE, not the database, so it stays cheap on every request."""
    u = session.get(_SESSION_KEY)
    if isinstance(u, dict) and u.get("id"):
        return u
    return None


def is_admin() -> bool:
    u = current_user()
    return bool(u and u.get("role") == "admin")


# ── decorators ───────────────────────────────────────────────────────────────

def login_required(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def _wrap(*a, **k):
        if current_user() is None:
            return jsonify({"error": "Authentication required",
                            "error_code": "AUTH_REQUIRED"}), 401
        return fn(*a, **k)
    return _wrap


def admin_required(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def _wrap(*a, **k):
        u = current_user()
        if u is None:
            return jsonify({"error": "Authentication required",
                            "error_code": "AUTH_REQUIRED"}), 401
        if u.get("role") != "admin":
            return jsonify({"error": "Admin only", "error_code": "FORBIDDEN"}), 403
        return fn(*a, **k)
    return _wrap


# ── login / logout logic (thin; endpoints wrap these) ────────────────────────

def do_login(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Verify and start a session. Returns the public user on success, else None.
    Records the login time as a side effect."""
    user = user_store.verify_credentials(username, password)
    if not user:
        return None
    set_session(user)
    user_store.touch_login(user["id"])
    return user
