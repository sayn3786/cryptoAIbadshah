"""
Dashboard user accounts — the store behind username/password login.

Small and deliberate: create a user (password hashed with werkzeug's PBKDF2),
look one up, verify a password, and record a login. No plaintext password is
ever stored, logged or returned; `_public` strips the hash from everything that
leaves this module.

Degrades cleanly before migration 009 has run: every read returns "no such
user" and a create raises AuthUnavailable, so a deploy that lands ahead of the
migration simply cannot authenticate rather than crashing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

from db import session_scope

VALID_ROLES = ("user", "admin")


class AuthUnavailable(RuntimeError):
    """The user table is missing (migration 009 not run) or the DB is down."""


class UserValidationError(ValueError):
    """A username/password/role that cannot be stored."""


class LastAdminError(RuntimeError):
    """Refused because it would remove the last enabled admin (lockout guard)."""


def _table_exists(s) -> bool:
    return bool(s.execute(text("SELECT to_regclass('app_users') IS NOT NULL")).scalar())


def _public(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A user dict safe to return/serialise — never the password hash."""
    if not row:
        return None
    return {"id": str(row.get("id")), "username": row.get("username"),
            "role": row.get("role"), "disabled": bool(row.get("disabled")),
            "created_at": row.get("created_at"),
            "last_login_at": row.get("last_login_at")}


def _norm_username(username: str) -> str:
    u = (username or "").strip()
    if not u or len(u) > 64 or any(c.isspace() for c in u):
        raise UserValidationError("username must be 1-64 chars with no spaces")
    return u


def create_user(username: str, password: str, *, role: str = "user") -> Dict[str, Any]:
    """Create a user with a hashed password. Raises UserValidationError on a bad
    input or a duplicate username, AuthUnavailable if the table is missing."""
    u = _norm_username(username)
    if not password or len(password) < 8:
        raise UserValidationError("password must be at least 8 characters")
    if role not in VALID_ROLES:
        raise UserValidationError(f"role must be one of {VALID_ROLES}")
    pw_hash = generate_password_hash(password)

    def _work(s):
        if not _table_exists(s):
            raise AuthUnavailable("app_users table is missing (run migration 009)")
        exists = s.execute(text("SELECT 1 FROM app_users WHERE lower(username)=lower(:u)"),
                           {"u": u}).first()
        if exists:
            raise UserValidationError("username already exists")
        row = s.execute(text("""
            INSERT INTO app_users (username, password_hash, role)
            VALUES (:u, :h, :r)
            RETURNING id, username, role, disabled, created_at, last_login_at
        """), {"u": u, "h": pw_hash, "r": role}).mappings().first()
        return _public(dict(row))

    with session_scope() as s:
        return _work(s)


def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Public view of a user by username, or None. Never raises for a missing
    table — returns None so callers treat "no auth configured" as "no user"."""
    u = (username or "").strip()
    if not u:
        return None

    def _work(s):
        if not _table_exists(s):
            return None
        row = s.execute(text(
            "SELECT id, username, role, disabled, created_at, last_login_at "
            "FROM app_users WHERE lower(username)=lower(:u)"), {"u": u}).mappings().first()
        return _public(dict(row)) if row else None

    with session_scope() as s:
        return _work(s)


def verify_credentials(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Return the PUBLIC user dict when the password matches and the account is
    enabled, else None. A missing table / missing user / disabled account / wrong
    password are indistinguishable to the caller (no user enumeration)."""
    u = (username or "").strip()
    if not u or not password:
        return None

    def _work(s):
        if not _table_exists(s):
            return None
        row = s.execute(text(
            "SELECT id, username, password_hash, role, disabled, created_at, "
            "last_login_at FROM app_users WHERE lower(username)=lower(:u)"),
            {"u": u}).mappings().first()
        if not row or row["disabled"]:
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        return _public(dict(row))

    with session_scope() as s:
        return _work(s)


def touch_login(user_id: str) -> None:
    """Record a successful login time. Best-effort — never raises."""
    if not user_id:
        return
    try:
        with session_scope() as s:
            if not _table_exists(s):
                return
            s.execute(text("UPDATE app_users SET last_login_at=:t WHERE id=:id"),
                      {"t": datetime.now(timezone.utc), "id": str(user_id)})
    except Exception:                                    # noqa: BLE001
        pass


def list_users() -> List[Dict[str, Any]]:
    """All users (public view), newest first. Empty when the table is missing."""
    def _work(s):
        if not _table_exists(s):
            return []
        rows = s.execute(text(
            "SELECT id, username, role, disabled, created_at, last_login_at "
            "FROM app_users ORDER BY created_at DESC")).mappings().all()
        return [_public(dict(r)) for r in rows]

    with session_scope() as s:
        return _work(s)


def user_count() -> int:
    """How many accounts exist (0 when the table is missing). Used to gate the
    first-admin bootstrap."""
    def _work(s):
        if not _table_exists(s):
            return 0
        return int(s.execute(text("SELECT count(*) FROM app_users")).scalar() or 0)

    with session_scope() as s:
        return _work(s)


# ── management: reset password, enable/disable, role, delete ─────────────────
# Every operation that could remove the last way IN — deleting, disabling, or
# demoting an admin — is guarded so the instance can never be locked out of its
# own user management.

def _get_row(s, user_id: str):
    """Unlocked read (for non-guarded lookups)."""
    return s.execute(text(
        "SELECT id, username, role, disabled FROM app_users WHERE id=:id"),
        {"id": str(user_id)}).mappings().first()


def _lock_admins_then_target(s, user_id: str):
    """For a guarded mutation, acquire locks in a CONSISTENT global order to avoid
    deadlocks: first every enabled-admin row (ordered by id), THEN the target row.

    Two concurrent disable/demote/delete transactions therefore contend on the
    same first admin row before either touches a target, so they serialize
    instead of each grabbing its own target and then dead-locking on the other's.
    Returns (enabled_admin_count, target_row). The count and the target are both
    read under the lock, so the last-admin decision cannot race a commit.
    """
    admin_ids = s.execute(text(
        "SELECT id FROM app_users WHERE role='admin' AND disabled=FALSE "
        "ORDER BY id FOR UPDATE")).all()
    row = s.execute(text(
        "SELECT id, username, role, disabled FROM app_users WHERE id=:id FOR UPDATE"),
        {"id": str(user_id)}).mappings().first()
    return len(admin_ids), row


def _would_strand(count: int, row) -> bool:
    """True when acting on `row` (an enabled admin) would remove the last one."""
    return bool(row and row["role"] == "admin" and not row["disabled"] and count <= 1)


def revalidate(user_id: str):
    """Re-check a session's user against the live table, so a demoted, disabled
    or deleted account loses access immediately — not only when the cookie
    expires. Returns a (state, user) tuple:
      * ("ok", public_user)  — exists and enabled; `role` is the CURRENT role
      * ("revoked", None)    — the table exists but the row is gone or disabled
      * ("unknown", None)    — cannot check (table missing / DB down); the caller
                               falls back to the signed cookie (degraded, but the
                               app stays usable during a DB outage)
    """
    if not user_id:
        return ("revoked", None)
    try:
        with session_scope() as s:
            if not _table_exists(s):
                return ("unknown", None)
            row = s.execute(text(
                "SELECT id, username, role, disabled, created_at, last_login_at "
                "FROM app_users WHERE id=:id"), {"id": str(user_id)}).mappings().first()
            if not row or row["disabled"]:
                return ("revoked", None)
            return ("ok", _public(dict(row)))
    except Exception:                                    # noqa: BLE001
        return ("unknown", None)


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    def _work(s):
        if not _table_exists(s):
            return None
        row = s.execute(text(
            "SELECT id, username, role, disabled, created_at, last_login_at "
            "FROM app_users WHERE id=:id"), {"id": str(user_id)}).mappings().first()
        return _public(dict(row)) if row else None

    with session_scope() as s:
        return _work(s)


def set_password(user_id: str, new_password: str) -> Dict[str, Any]:
    """Reset a user's password. Raises UserValidationError on a weak password or
    an unknown id."""
    if not new_password or len(new_password) < 8:
        raise UserValidationError("password must be at least 8 characters")
    pw_hash = generate_password_hash(new_password)

    def _work(s):
        if not _table_exists(s):
            raise AuthUnavailable("app_users table is missing (run migration 009)")
        row = _get_row(s, user_id)
        if not row:
            raise UserValidationError("no such user")
        s.execute(text("UPDATE app_users SET password_hash=:h WHERE id=:id"),
                  {"h": pw_hash, "id": str(user_id)})
        return _public(dict(_get_row(s, user_id)))

    with session_scope() as s:
        return _work(s)


def set_disabled(user_id: str, disabled: bool) -> Dict[str, Any]:
    """Enable/disable a user. Refuses to disable the last enabled admin."""
    def _work(s):
        if not _table_exists(s):
            raise AuthUnavailable("app_users table is missing (run migration 009)")
        count, row = _lock_admins_then_target(s, user_id)
        if not row:
            raise UserValidationError("no such user")
        if disabled and _would_strand(count, row):
            raise LastAdminError("cannot disable the last enabled admin")
        s.execute(text("UPDATE app_users SET disabled=:d WHERE id=:id"),
                  {"d": bool(disabled), "id": str(user_id)})
        return _public(dict(_get_row(s, user_id)))

    with session_scope() as s:
        return _work(s)


def set_role(user_id: str, role: str) -> Dict[str, Any]:
    """Change a user's role. Refuses to demote the last enabled admin."""
    if role not in VALID_ROLES:
        raise UserValidationError(f"role must be one of {VALID_ROLES}")

    def _work(s):
        if not _table_exists(s):
            raise AuthUnavailable("app_users table is missing (run migration 009)")
        count, row = _lock_admins_then_target(s, user_id)
        if not row:
            raise UserValidationError("no such user")
        if role != "admin" and _would_strand(count, row):
            raise LastAdminError("cannot demote the last enabled admin")
        s.execute(text("UPDATE app_users SET role=:r WHERE id=:id"),
                  {"r": role, "id": str(user_id)})
        return _public(dict(_get_row(s, user_id)))

    with session_scope() as s:
        return _work(s)


def delete_user(user_id: str) -> bool:
    """Delete a user. Refuses to delete the last enabled admin. Returns True when
    a row was removed, False when the id did not exist."""
    def _work(s):
        if not _table_exists(s):
            raise AuthUnavailable("app_users table is missing (run migration 009)")
        count, row = _lock_admins_then_target(s, user_id)
        if not row:
            return False
        if _would_strand(count, row):
            raise LastAdminError("cannot delete the last enabled admin")
        s.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": str(user_id)})
        return True

    with session_scope() as s:
        return _work(s)


def verify_password(user_id: str, password: str) -> bool:
    """True when `password` matches the stored hash for this id. Used for the
    self-service password change (confirm the current password first)."""
    if not password:
        return False

    def _work(s):
        if not _table_exists(s):
            return False
        row = s.execute(text("SELECT password_hash FROM app_users WHERE id=:id"),
                        {"id": str(user_id)}).mappings().first()
        return bool(row and check_password_hash(row["password_hash"], password))

    with session_scope() as s:
        return _work(s)
