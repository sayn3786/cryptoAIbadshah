#!/usr/bin/env python3
"""Run selected tests against a new local socket-only PostgreSQL cluster.

Requires pgserver (binary distribution), or TEST_POSTGRES_BIN pointing to a
trusted local PostgreSQL bin directory, plus psycopg and pytest in the test venv.
Never reads a configured production DSN. Stops the cluster even on test failure.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

def main():
    configured = os.environ.get('TEST_POSTGRES_BIN')
    if configured:
        bins = Path(configured).resolve()
    else:
        try:
            from pgserver._commands import POSTGRES_BIN_PATH
        except ImportError:
            raise SystemExit('Install pgserver or set TEST_POSTGRES_BIN to local PostgreSQL binaries')
        bins = Path(POSTGRES_BIN_PATH)
    for command in ('initdb', 'pg_ctl', 'postgres'):
        if not (bins / command).is_file() or not os.access(bins / command, os.X_OK):
            raise SystemExit(f'Missing executable PostgreSQL binary: {command}')
    with tempfile.TemporaryDirectory(prefix="cstars-pg-", dir="/private/tmp") as tmp:
        root = Path(tmp)
        data = root / "data"
        env = {k: v for k, v in os.environ.items()
               if not any(s in k.upper() for s in ("DATABASE", "SECRET", "TOKEN", "API_KEY"))}
        env["PYTHON_DOTENV_DISABLED"] = "1"
        subprocess.run([str(bins / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust"], env=env, check=True, stdout=subprocess.DEVNULL)
        try:
            subprocess.run([str(bins / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
                            "-o", f"-h '' -k {root}", "-w", "start"], env=env, check=True)
            url = f"postgresql://postgres@/postgres?host={root}&sslmode=disable"
            env.update(TEST_DATABASE_URL=url, DATABASE_URL=url, DATABASE_URL_TEST=url)
            return subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], env=env).returncode
        finally:
            if (data / "postmaster.pid").exists():
                subprocess.run([str(bins / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
