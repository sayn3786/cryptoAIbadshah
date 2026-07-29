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
    "get_engine", "session_scope", "healthcheck", "reset_engine",
    "sanitize_db_error", "safe_dsn_summary",
]


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
            connect_args={
                "connect_timeout": int(_env("DB_CONNECT_TIMEOUT", "10") or 10),
                # Keep a stuck query from pinning a serverless invocation open
                # for its whole 60s budget.
                "options": f"-c statement_timeout={int(_env('DB_STATEMENT_TIMEOUT_MS', '15000') or 15000)}",
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
    from sqlalchemy.orm import Session

    engine = get_engine()
    session = Session(engine, future=True)
    try:
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


def healthcheck() -> dict:
    """
    Connectivity probe for the health endpoint.

    Returns a dict that is always safe to serialise to a client: no DSN, host,
    user or password, and any driver error is sanitized.
    """
    info = safe_dsn_summary()
    if not info["configured"]:
        return {**info, "ok": False, "error_code": "DB_NOT_CONFIGURED"}
    try:
        from sqlalchemy import text
        with session_scope() as s:
            s.execute(text("SELECT 1"))
            applied = s.execute(text(
                "SELECT version FROM schema_migrations ORDER BY version"
            )).scalars().all()
        return {**info, "ok": True, "migrations_applied": list(applied)}
    except Exception as exc:
        # The health endpoint is reachable without auth, so it returns a CODE
        # ONLY. Even a sanitized driver message can disclose infrastructure
        # (hostnames, database names, driver versions). The detail goes to the
        # server log, where operators can see it and clients cannot.
        print(f"[db] healthcheck failed: {sanitize_db_error(exc)}")
        return {**info, "ok": False, "error_code": "DB_UNAVAILABLE"}
