"""
Database engine + configuration for persistent signal tracking.

SERVER-SIDE ONLY. Nothing in dashboard/ imports this module, and DATABASE_URL
must never be exposed to a client bundle, returned from an API or logged.

Design notes
------------
* **Lazy.** The engine is built on first use, not at import. Importing this
  module during a Vercel build (or in a test run with no database) must not
  require DATABASE_URL.
* **NullPool.** Serverless functions are frozen and thawed between requests, so
  a pooled TCP connection is very likely dead by the time it is reused. NullPool
  opens a connection per checkout and closes it on return, which is what Neon's
  own serverless guidance recommends. Point DATABASE_URL at Neon's `-pooler`
  host to get connection pooling on the server side instead.
* **Every session is closed.** `session_scope()` commits on success, rolls back
  on any exception and disposes the connection either way.
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from typing import Optional

__all__ = [
    "DatabaseNotConfigured", "DatabaseUnavailable",
    "db_configured", "db_required", "db_enabled",
    "get_engine", "session_scope", "healthcheck", "reset_engine", "EXPECTED_TABLES",
    "sanitize_db_error", "safe_dsn_summary", "classify_db_failure",
]


# Tables the initial migration creates. Used by the health probe to tell
# "connected but not migrated" apart from "cannot connect".
EXPECTED_TABLES = ("schema_migrations", "signals", "signal_targets",
                   "signal_indicator_snapshots", "signal_events",
                   "signal_postmortems")


class DatabaseNotConfigured(RuntimeError):
    """DATABASE_URL is missing or unusable."""


class DatabaseUnavailable(RuntimeError):
    """The database is configured but the operation could not be completed."""


# ── Configuration ────────────────────────────────────────────────────────────

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def db_configured() -> bool:
    """True when a connection string is present. Never returns the value."""
    return bool(_env("DATABASE_URL"))


def db_required() -> bool:
    """
    When true, a signal may not be published unless it was persisted.

    Defaults to FALSE so an existing deployment that has not provisioned the
    database keeps working exactly as before. Production sets DB_REQUIRED=true
    explicitly (see .env.example and the README).
    """
    return _env("DB_REQUIRED", "false").lower() in ("1", "true", "yes", "on")


def db_enabled() -> bool:
    """Should we attempt to persist at all?"""
    return db_configured()


def _normalize_url(raw: str) -> str:
    """
    Coerce a Neon/Vercel connection string to the psycopg 3 driver and require
    TLS. Accepts the `postgres://`, `postgresql://` and already-qualified
    `postgresql+psycopg://` forms.
    """
    url = raw.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+psycopg://"):
        raise DatabaseNotConfigured(
            "DATABASE_URL must be a PostgreSQL connection string "
            "(postgres:// or postgresql://)."
        )
    # Neon always requires TLS. Only add it when the caller has not been
    # explicit, so a local test database on sslmode=disable still works.
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


# ── Secret-safe error handling ───────────────────────────────────────────────

# A connection string can appear inside driver exception text. Scrub anything
# that looks like one before the message reaches a log or an HTTP response.
_DSN_RE = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s'\"]+", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"(password|pwd)\s*=\s*[^\s'\";]+", re.IGNORECASE)
# Driver errors name the host WITHOUT a full DSN, e.g.
#   failed to resolve host 'db-pooler.example.neon.tech'
# which still discloses infrastructure. Redact bare hostnames too.
_HOST_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_ALLOWED_HOSTS = ("sqlalche.me", "localhost")


def sanitize_db_error(exc: BaseException, limit: int = 200) -> str:
    """
    Render an exception safely for logs and API responses.

    Strips connection strings, password parameters and bare hostnames. Callers
    should STILL prefer a generic error code over this text on public
    endpoints — this is the last line of defence, not the first.
    """
    text = f"{type(exc).__name__}: {exc}"
    text = _DSN_RE.sub("[redacted-dsn]", text)
    text = _PASSWORD_RE.sub(r"\1=[redacted]", text)
    text = _HOST_RE.sub(
        lambda m: m.group(0) if m.group(0).lower() in _ALLOWED_HOSTS else "[redacted-host]",
        text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


# Coarse, non-identifying classification of a connection failure. Lets a health
# endpoint say WHY without echoing driver text that can carry hostnames,
# database names or credentials.
_FAILURE_SIGNATURES = (
    ("startup_parameter_rejected", ("unsupported startup parameter",
                                    "unsupported startup parameters")),
    ("authentication",             ("password authentication failed",
                                    "authentication failed", "role \"",
                                    "no password supplied")),
    ("database_missing",          ("database \"", "does not exist")),
    ("dns",                       ("could not translate host name",
                                    "failed to resolve host",
                                    "name or service not known",
                                    "nodename nor servname")),
    ("tls",                       ("ssl ", "certificate", "sslmode")),
    ("timeout",                   ("timeout expired", "connection timed out",
                                    "timeout", "statement timeout")),
    ("refused",                   ("connection refused", "could not connect",
                                    "is the server running")),
    ("too_many_connections",      ("too many clients", "too many connections")),
    ("driver_missing",            ("sqlalchemy is not installed",
                                    "no module named")),
)


def classify_db_failure(exc: BaseException) -> str:
    """
    One-word cause for a connection failure. Safe to return to a client.

    Deliberately a fixed vocabulary rather than the driver's message: the
    message can contain the host, the database name or a role name, and this is
    surfaced on an unauthenticated endpoint.
    """
    blob = f"{type(exc).__name__}: {exc}".lower()
    for label, needles in _FAILURE_SIGNATURES:
        if any(n in blob for n in needles):
            return label
    return "other"


def safe_dsn_summary() -> dict:
    """
    Non-identifying description of the configured target, for health output.

    Deliberately excludes host, username, password, database name and the
    connection string itself — a health endpoint is often public.
    """
    raw = _env("DATABASE_URL")
    return {
        "configured": bool(raw),
        "required":   db_required(),
        "driver":     "psycopg3" if raw else None,
        "pooled_endpoint": ("-pooler." in raw) if raw else None,
        "tls":        ("sslmode=disable" not in raw) if raw else None,
    }


# ── Engine (lazy, process-wide) ──────────────────────────────────────────────

_engine = None
_engine_lock = threading.Lock()
_engine_url_fingerprint: Optional[int] = None


def get_engine():
    """
    Build (once) and return the SQLAlchemy engine.

    Raises DatabaseNotConfigured when DATABASE_URL is absent, so callers can
    decide between degrading and failing based on db_required().
    """
    global _engine, _engine_url_fingerprint

    raw = _env("DATABASE_URL")
    if not raw:
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Add it in Vercel -> Project -> Settings "
            "-> Environment Variables (server-side only), then redeploy."
        )

    # Rebuild if the URL changed under us (tests switch databases).
    fingerprint = hash(raw)
    if _engine is not None and fingerprint == _engine_url_fingerprint:
        return _engine

    with _engine_lock:
        if _engine is not None and fingerprint == _engine_url_fingerprint:
            return _engine
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
        except ImportError as exc:                     # pragma: no cover
            raise DatabaseNotConfigured(
                "SQLAlchemy is not installed. Add the requirements.txt entries "
                "'SQLAlchemy>=2.0' and 'psycopg[binary]>=3.1'."
            ) from exc

        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass

        _engine = create_engine(
            _normalize_url(raw),
            poolclass=NullPool,          # see module docstring
            future=True,
            # NOTE: no `options` startup parameter here. Neon's pooled endpoint
            # is PgBouncer-based and rejects `options` ("unsupported startup
            # parameter"), so passing `-c statement_timeout=...` at connect time
            # made every connection through the -pooler host fail outright.
            # statement_timeout is applied per transaction in session_scope()
            # with SET LOCAL, which is pooler-safe and correctly scoped.
            connect_args={
                "connect_timeout": int(_env("DB_CONNECT_TIMEOUT", "10") or 10),
            },
        )
        _engine_url_fingerprint = fingerprint
        return _engine


def reset_engine() -> None:
    """Dispose the cached engine. Used by tests when switching databases."""
    global _engine, _engine_url_fingerprint
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass
        _engine = None
        _engine_url_fingerprint = None


@contextmanager
def session_scope():
    """
    Transactional scope. Commits on success, rolls back on ANY exception, and
    always returns the connection.

        with session_scope() as s:
            s.execute(...)

    The rollback path is what makes "a failed write never publishes a signal"
    true: partial state cannot survive an error.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    engine = get_engine()
    session = Session(engine, future=True)
    try:
        # Per-transaction statement timeout. SET LOCAL (not a connect-time
        # `options` parameter) because Neon's pooled endpoint is PgBouncer-based
        # and rejects `options` at startup. SET LOCAL is also correctly scoped:
        # it reverts at COMMIT/ROLLBACK, so it cannot leak into whatever
        # transaction reuses this pooled connection next.
        _ms = int(_env("DB_STATEMENT_TIMEOUT_MS", "15000") or 15000)
        if _ms > 0:
            try:
                session.execute(text(f"SET LOCAL statement_timeout = {_ms}"))
            except Exception as exc:
                # Never let a timeout hint break the actual work.
                print(f"[db] could not set statement_timeout: {sanitize_db_error(exc)}")
        yield session
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        session.close()


# What to actually DO about each failure class. Kept next to the classifier so
# a new signature cannot be added without an accompanying remedy.
_UNREACHABLE_HINTS = {
    "startup_parameter_rejected":
        "The pooled endpoint rejected a startup parameter. Do not pass libpq "
        "`options` when using Neon's -pooler host.",
    "authentication":
        "Credentials rejected. Re-copy DATABASE_URL from the Neon dashboard; "
        "a rotated password invalidates the old one.",
    "database_missing":
        "The database or role named in DATABASE_URL does not exist. Check you "
        "are pointing at the right Neon branch.",
    "dns":
        "Host could not be resolved. Check DATABASE_URL for a typo and that "
        "the Neon project still exists.",
    "tls":
        "TLS negotiation failed. Neon requires sslmode=require.",
    "timeout":
        "Connection timed out. A scale-to-zero Neon compute can be slow to "
        "wake; retry, and raise DB_CONNECT_TIMEOUT if it persists.",
    "refused":
        "Connection refused. Check the host and port in DATABASE_URL.",
    "too_many_connections":
        "Connection limit reached. Use Neon's -pooler host.",
    "driver_missing":
        "The Postgres driver is not installed. Check requirements.txt has "
        "SQLAlchemy and psycopg[binary], and redeploy.",
    "other":
        "Check DATABASE_URL, the Neon project state and that the variable is "
        "enabled for this environment.",
}


def healthcheck() -> dict:
    """
    Connectivity + migration probe for the health endpoint.

    Reports REACHABILITY and MIGRATION STATE separately, because they are
    different problems with different fixes:

      * not reachable  -> check DATABASE_URL, the Neon project, the network
      * reachable but not migrated -> run the migration

    An earlier version ran `SELECT 1` and the migrations query in one try block,
    so a perfectly healthy but unmigrated database reported DB_UNAVAILABLE and
    sent you looking for a connection fault that did not exist.

    Returns a dict that is always safe to serialise to a client: no DSN, host,
    user or password, and any driver error is sanitized away entirely.
    """
    info = safe_dsn_summary()
    if not info["configured"]:
        return {**info, "ok": False, "reachable": False, "migrated": False,
                "error_code": "DB_NOT_CONFIGURED",
                "hint": "Set DATABASE_URL in the server environment, then redeploy."}

    from sqlalchemy import text

    # ── 1. Can we reach it at all? ───────────────────────────────────────────
    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:
        cause = classify_db_failure(exc)
        print(f"[db] not reachable ({cause}): {sanitize_db_error(exc)}")
        return {**info, "ok": False, "reachable": False, "migrated": False,
                "error_code": "DB_UNAVAILABLE",
                "failure": cause,
                "hint": _UNREACHABLE_HINTS.get(cause, _UNREACHABLE_HINTS["other"])}

    # ── 2. Reachable. Has the migration been run? ───────────────────────────
    try:
        with session_scope() as s:
            # Unqualified names resolve through search_path, so this works
            # whether the tables live in public or in a custom schema — some
            # Neon setups use one, and tests isolate into a throwaway schema.
            has_table = s.execute(text(
                "SELECT to_regclass('schema_migrations') IS NOT NULL"
            )).scalar()
            applied = list(s.execute(text(
                "SELECT version FROM schema_migrations ORDER BY version"
            )).scalars().all()) if has_table else []
            missing = [t for t in EXPECTED_TABLES if not s.execute(text(
                "SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar()]
    except Exception as exc:
        print(f"[db] migration probe failed: {sanitize_db_error(exc)}")
        return {**info, "ok": False, "reachable": True, "migrated": False,
                "error_code": "DB_SCHEMA_UNREADABLE",
                "hint": "Connected, but the schema could not be inspected. Check "
                        "the role's privileges on the public schema."}

    if missing:
        return {**info, "ok": False, "reachable": True, "migrated": False,
                "error_code": "DB_NOT_MIGRATED",
                "migrations_applied": applied,
                "missing_tables": missing,
                "hint": "Connection works. Run database/migrations/"
                        "001_initial_signal_schema.sql once (Neon Console -> SQL "
                        "Editor), then verify with database/verify_schema.sql."}

    return {**info, "ok": True, "reachable": True, "migrated": True,
            "migrations_applied": applied}
