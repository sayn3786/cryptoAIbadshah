#!/usr/bin/env python3
"""
Explicit migration runner.

    python database/migrate.py status     # what is applied, what is pending
    python database/migrate.py up         # apply pending migrations
    python database/migrate.py verify     # re-run the read-only checks

This is NEVER invoked automatically. It does not run on import, on app start,
on an API request or during a Vercel build — creating tables implicitly is how
production schemas drift. Run it yourself, or paste the .sql into Neon's Query
editor (see README).

Rollback scripts under migrations/rollback/ are deliberately NOT executable
from here. They are destructive and exist for review only.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MIGRATIONS = os.path.join(_HERE, "migrations")

sys.path.insert(0, os.path.join(_ROOT, "backend"))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"))
    except Exception:
        pass


def discover():
    """Return [(version, path)] for migrations/NNN_*.sql, ordered."""
    if not os.path.isdir(_MIGRATIONS):
        return []
    out = []
    for name in sorted(os.listdir(_MIGRATIONS)):
        if not name.endswith(".sql"):
            continue
        version = name.split("_", 1)[0]
        if not version.isdigit():
            continue
        out.append((version, os.path.join(_MIGRATIONS, name)))
    return out


def applied_versions(session) -> set:
    from sqlalchemy import text
    exists = session.execute(text(
        "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
    )).scalar()
    if not exists:
        return set()
    return set(session.execute(text("SELECT version FROM schema_migrations")).scalars().all())


def cmd_status() -> int:
    from db import session_scope, sanitize_db_error
    try:
        with session_scope() as s:
            done = applied_versions(s)
    except Exception as exc:
        print(f"ERROR: {sanitize_db_error(exc)}")
        return 2
    print("version  state    file")
    for version, path in discover():
        state = "applied" if version in done else "PENDING"
        print(f"{version:<8} {state:<8} {os.path.basename(path)}")
    unknown = done - {v for v, _ in discover()}
    for v in sorted(unknown):
        print(f"{v:<8} applied  (no file in migrations/)")
    return 0


def cmd_up() -> int:
    """
    Apply pending migrations.

    Each .sql file manages its own BEGIN/COMMIT, so it is executed as a single
    script and either lands completely or not at all.
    """
    from sqlalchemy import text
    from db import get_engine, session_scope, sanitize_db_error

    try:
        with session_scope() as s:
            done = applied_versions(s)
    except Exception as exc:
        print(f"ERROR: cannot reach the database — {sanitize_db_error(exc)}")
        return 2

    pending = [(v, p) for v, p in discover() if v not in done]
    if not pending:
        print("Nothing to do — all migrations are applied.")
        return 0

    engine = get_engine()
    for version, path in pending:
        sql = open(path, "r", encoding="utf-8").read()
        print(f"applying {version} ({os.path.basename(path)}) …")
        try:
            # AUTOCOMMIT: the file supplies its own transaction boundaries.
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                # Straight to the driver cursor with NO parameter argument.
                # exec_driver_sql passes an empty parameter set, which makes
                # psycopg parse the text for placeholders — so a migration whose
                # COMMENTS mention a percentage died with "incomplete
                # placeholder: '%'". A migration is a script, not a query; it
                # has no parameters and must not be scanned for any.
                cur = conn.connection.dbapi_connection.cursor()
                try:
                    cur.execute(sql)
                finally:
                    cur.close()
        except Exception as exc:
            print(f"FAILED on {version}: {sanitize_db_error(exc)}")
            print("Nothing from this migration was committed. Fix and re-run.")
            return 1
        print(f"  ok {version}")

    with session_scope() as s:
        now = sorted(applied_versions(s))
    print(f"applied versions: {', '.join(now)}")
    return 0


def cmd_verify() -> int:
    from sqlalchemy import text
    from db import session_scope, sanitize_db_error
    expected = ["schema_migrations", "signals", "signal_targets",
                "signal_indicator_snapshots", "signal_events", "signal_postmortems"]
    try:
        with session_scope() as s:
            found = set(s.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )).scalars().all())
            versions = sorted(applied_versions(s))
            floats = s.execute(text("""
                SELECT table_name, column_name, data_type
                FROM   information_schema.columns
                WHERE  table_schema='public'
                  AND  table_name IN ('signals','signal_targets','signal_postmortems')
                  AND  data_type IN ('double precision','real')
            """)).all()
    except Exception as exc:
        print(f"ERROR: {sanitize_db_error(exc)}")
        return 2

    ok = True
    for t in expected:
        mark = "ok " if t in found else "MISSING"
        if t not in found:
            ok = False
        print(f"  {mark} {t}")
    print(f"  applied migrations: {', '.join(versions) or '(none)'}")
    if not versions:
        ok = False
    if floats:
        ok = False
        print("  FAIL float column(s) found where numeric is required:")
        for row in floats:
            print(f"    {row[0]}.{row[1]} is {row[2]}")
    else:
        print("  ok  no floating-point money columns")
    print("VERIFY OK" if ok else "VERIFY FAILED")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "up", "verify"])
    args = parser.parse_args()

    _load_dotenv()
    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.")
        print("Set it in your shell or .env, or paste the .sql into Neon -> Query.")
        print("Never paste the connection string into a chat or commit it.")
        return 2

    return {"status": cmd_status, "up": cmd_up, "verify": cmd_verify}[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
