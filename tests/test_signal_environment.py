"""
Environment tagging: telling preview rows from production rows in ONE database.

DATABASE_URL is shared across Vercel environments, so these tests cover the two
things that follow from that: every row records which deployment wrote it, and
idempotency is per environment so a preview cannot claim a candle and make
production's write look like a duplicate.

Everything here runs against migration 001 + 002 in a throwaway schema. The
pre-002 behaviour (writes still work, untagged) is covered in
test_signal_database.py, which applies 001 only — that pairing is deliberate:
between them they prove the code runs on BOTH sides of the migration.

REQUIRES a throwaway PostgreSQL database in TEST_DATABASE_URL. Skips without one.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

TEST_URL = (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL_TEST") or "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_URL,
    reason="TEST_DATABASE_URL not set — database tests skipped",
)

_MIG = os.path.join(os.path.dirname(__file__), "..", "database", "migrations")
MIGRATIONS = (os.path.join(_MIG, "001_initial_signal_schema.sql"),
              os.path.join(_MIG, "002_signal_environment.sql"))


@pytest.fixture()
def engine():
    if "prod" in TEST_URL.lower():
        pytest.fail("TEST_DATABASE_URL looks like a production database — refusing to run")
    os.environ["DATABASE_URL"] = TEST_URL
    import db
    db.reset_engine()
    yield db.get_engine()
    db.reset_engine()


@pytest.fixture()
def schema(engine):
    """Name of this test's throwaway schema, so a test can talk to it directly."""
    return f"e_{uuid.uuid4().hex[:12]}"


def raw_sql(schema: str, sql: str):
    """
    Run SQL on a connection OUTSIDE SQLAlchemy's engine.

    The store fixture pins every engine connection to the test schema with a
    `connect` listener, and that listener's own statement opens a transaction —
    so asking the engine for an AUTOCOMMIT connection afterwards fails. A file
    that contains its own BEGIN/COMMIT (every migration does) needs autocommit,
    hence this bypass.
    """
    import psycopg
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
            cur.execute(sql)
            try:
                return cur.fetchall()
            except psycopg.ProgrammingError:
                return []


@pytest.fixture()
def store(engine, schema, monkeypatch):
    """Throwaway schema with 001 AND 002 applied; dropped afterwards."""
    from sqlalchemy import event

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'SET search_path TO "{schema}", public')
        for path in MIGRATIONS:
            conn.exec_driver_sql(open(path, "r", encoding="utf-8").read())

    def _set_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
    event.listen(engine, "connect", _set_path)

    import signal_store
    signal_store.reset_capabilities()
    # Default this process to a known label so tests never depend on the
    # machine's own VERCEL_ENV.
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    try:
        yield signal_store
    finally:
        signal_store.reset_capabilities()
        event.remove(engine, "connect", _set_path)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


BASE = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)


def _payload(**over):
    p = dict(
        symbol="btc", exchange="binance", timeframe="2H", direction="LONG",
        strategy_name="mtf", strategy_version="v42_tpfilter",
        candle_open_time=BASE, candle_close_time=BASE + timedelta(hours=2),
        generated_at=BASE + timedelta(hours=2),
        entry_price="100.5", stop_loss="95.25", targets=["110.5", "120.75"],
        indicator_values={"rsi": 55.5}, market_context={"symbol": "BTC"},
        source_timestamps={"last_closed_candle_ms": 1767268800000},
        input_candle_count=60, confidence_score="61.5",
    )
    p.update(over)
    return p


# ── The migration itself ────────────────────────────────────────────────────

def test_migration_002_records_itself_and_is_re_runnable(store, schema):
    applied = {r[0] for r in raw_sql(schema, "SELECT version FROM schema_migrations")}
    assert {"001", "002"} <= applied

    # Re-running must be a no-op, not an error — the property 001 has too.
    raw_sql(schema, open(MIGRATIONS[1], "r", encoding="utf-8").read())
    still = {r[0] for r in raw_sql(schema, "SELECT version FROM schema_migrations")}
    assert applied == still


def test_the_wide_index_replaced_the_narrow_one(store, engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        idx = set(conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'signals'"
        )).scalars().all())
    assert "signals_idempotency_env_uidx" in idx
    assert "signals_idempotency_uidx" not in idx, \
        "the narrow index must be gone, or environments cannot both publish a candle"


def test_existing_rows_would_be_labelled_production(store, engine):
    # The column default is what backfills pre-002 history, and 'production' is
    # the honest label for it: nothing else was writing before this migration.
    from sqlalchemy import text
    with engine.connect() as conn:
        default = conn.execute(text("""
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'signals' AND column_name = 'environment'
        """)).scalar()
    assert "production" in (default or "")


def test_the_constraint_is_added_even_if_the_name_exists_elsewhere(engine, schema):
    """
    Constraint names are unique per TABLE, not per database.

    The guard in 002 originally matched on conname alone, so any other schema
    holding a constraint of the same name made this one silently skip — leaving
    `environment` completely unguarded while the migration reported success.
    """
    decoy = f"d_{uuid.uuid4().hex[:10]}"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{decoy}"')
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        # A same-named constraint on an unrelated table, in another schema.
        conn.exec_driver_sql(
            f'CREATE TABLE "{decoy}".other (environment TEXT '
            f'CONSTRAINT signals_environment_chk CHECK (environment <> \'\'))')
    try:
        for path in MIGRATIONS:
            raw_sql(schema, open(path, "r", encoding="utf-8").read())

        found = raw_sql(schema, """
            SELECT count(*) FROM pg_constraint
            WHERE conrelid = to_regclass('signals')
              AND conname  = 'signals_environment_chk'
        """)
        assert found[0][0] == 1, "the column would be storing unvalidated labels"
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{decoy}" CASCADE')
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def test_the_check_constraint_rejects_junk_labels(store, engine):
    from sqlalchemy import text
    import sqlalchemy.exc as sa_exc
    with pytest.raises(sa_exc.DatabaseError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO signals (symbol, exchange, timeframe, direction, "
                "strategy_name, strategy_version, candle_open_time, "
                "candle_close_time, generated_at, entry_price, stop_loss, status, "
                "environment) VALUES ('BTC','binance','2H','LONG','m','v1',"
                "now(), now() + interval '2 hours', now(), 1, 0.5, 'OPEN', "
                "'Preview; DROP TABLE')"))


# ── Both sides of the migration, in one process ─────────────────────────────

@pytest.fixture()
def store_001(engine, schema, monkeypatch):
    """Throwaway schema with ONLY migration 001 — the pre-002 world."""
    from sqlalchemy import event

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'SET search_path TO "{schema}", public')
        conn.exec_driver_sql(open(MIGRATIONS[0], "r", encoding="utf-8").read())

    def _set_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
    event.listen(engine, "connect", _set_path)

    import signal_store
    signal_store.reset_capabilities()
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    try:
        yield signal_store
    finally:
        signal_store.reset_capabilities()
        event.remove(engine, "connect", _set_path)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def test_writes_still_work_before_the_migration_is_applied(store_001):
    """
    Deploying this code BEFORE running 002 must not break publishing.

    If the insert assumed the column, every write would fail — and with
    DB_REQUIRED=true that means production stops publishing entirely. So the
    column is probed, and its absence just means untagged rows.
    """
    out = store_001.create_signal(**_payload())
    assert out["created"] is True
    assert "environment" not in out["signal"]


def test_an_environment_filter_is_ignored_before_the_migration(store_001):
    # Asking for one environment cannot filter on a column that does not exist;
    # it must degrade to "everything" rather than raising.
    created = store_001.create_signal(**_payload())
    items = store_001.list_signals(environment="production")["items"]
    assert [r["id"] for r in items] == [created["signal"]["id"]]


def test_tagging_starts_working_as_soon_as_the_migration_lands(store_001, schema):
    before = store_001.create_signal(**_payload())
    assert "environment" not in before["signal"]

    raw_sql(schema, open(MIGRATIONS[1], "r", encoding="utf-8").read())
    store_001.reset_capabilities()   # what a fresh deployment / cold start does

    after = store_001.create_signal(**_payload(symbol="eth"))
    assert after["signal"]["environment"] == "preview"

    # The row written before the migration takes the column default. It was
    # written by a preview deploy, but 'production' is the only honest guess the
    # database can make — which is why the migration should be run first.
    rows = dict(raw_sql(schema, "SELECT symbol, environment FROM signals"))
    assert rows == {"BTC": "production", "ETH": "preview"}


def test_health_reports_whether_tagging_is_available(store_001, monkeypatch):
    import db
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    health = db.healthcheck()
    assert health["ok"] is True
    assert health["environment"] == "preview"
    assert health["environment_tagging"] is False
    assert "002_signal_environment.sql" in health["hint"]


def test_health_reports_tagging_once_migrated(store, monkeypatch):
    import db
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    health = db.healthcheck()
    assert health["ok"] is True
    assert health["environment"] == "production"
    assert health["environment_tagging"] is True
    assert "hint" not in health


def test_health_never_leaks_the_branch_or_commit(store, monkeypatch):
    # /api/db/health is public. The environment NAME is fine to publish; the git
    # branch and sha are not published even though they are not secrets.
    import db
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "feat/secret-looking-branch")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "deadbeefcafe1234")
    blob = repr(db.healthcheck())
    assert "secret-looking-branch" not in blob
    assert "deadbeefcafe" not in blob


# ── Writes are tagged ───────────────────────────────────────────────────────

def test_a_signal_records_the_environment_that_wrote_it(store):
    out = store.create_signal(**_payload())
    assert out["created"] is True
    assert out["signal"]["environment"] == "production"


def test_the_label_follows_the_deployment_not_the_caller(store, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    store.reset_capabilities()
    out = store.create_signal(**_payload(symbol="eth"))
    assert out["signal"]["environment"] == "preview"


def test_an_explicit_environment_argument_wins(store):
    out = store.create_signal(**_payload(symbol="sol"), environment="staging")
    assert out["signal"]["environment"] == "staging"


def test_a_malformed_label_is_normalised_rather_than_failing_the_write(store):
    # Never lose a real signal over a bad env var. The CHECK constraint would
    # reject this, so it must be normalised before it reaches the insert.
    out = store.create_signal(**_payload(symbol="ada"), environment="Prod Env!!")
    assert out["created"] is True
    assert out["signal"]["environment"] == "unknown"


def test_vercel_env_is_used_when_no_override_is_set(store, monkeypatch):
    monkeypatch.delenv("SIGNAL_ENVIRONMENT", raising=False)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    out = store.create_signal(**_payload(symbol="dot"))
    assert out["signal"]["environment"] == "preview"


def test_the_created_event_records_which_deployment(store, monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "feat/signal-environment-tag")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abcdef1234567890")
    out = store.create_signal(**_payload(symbol="link"))
    detail = store.get_signal(out["signal"]["id"])
    created = [e for e in detail["events"] if e["event_type"] == "CREATED"][0]
    dep = created["metadata"]["deployment"]
    assert dep["environment"] == "production"
    assert dep["ref"] == "feat/signal-environment-tag"
    assert dep["sha"] == "abcdef123456", "sha is truncated, not stored whole"


# ── Idempotency is per environment ──────────────────────────────────────────

def test_preview_cannot_suppress_a_production_signal(store, monkeypatch):
    """
    THE bug this migration exists for.

    Under the old key, whichever environment evaluated the candle first won and
    the other one's write came back as a duplicate — so a preview deploy could
    silently stop production from recording a real signal.
    """
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    first = store.create_signal(**_payload())
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    second = store.create_signal(**_payload())

    assert first["created"] is True
    assert second["created"] is True, \
        "production must record its own signal even though preview got there first"
    assert second["idempotent_hit"] is False
    assert first["signal"]["id"] != second["signal"]["id"]
    assert {first["signal"]["environment"], second["signal"]["environment"]} == \
        {"preview", "production"}


def test_idempotency_still_holds_within_one_environment(store):
    # The whole point of the key is preserved: a re-run in the SAME environment
    # is still a no-op.
    first = store.create_signal(**_payload())
    again = store.create_signal(**_payload())
    assert first["created"] is True
    assert again["created"] is False
    assert again["idempotent_hit"] is True
    assert again["signal"]["id"] == first["signal"]["id"]


def test_the_returned_duplicate_is_the_row_from_this_environment(store, monkeypatch):
    # A duplicate lookup must not hand back the OTHER environment's row.
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    prev = store.create_signal(**_payload())
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    prod = store.create_signal(**_payload())
    again = store.create_signal(**_payload())

    assert again["idempotent_hit"] is True
    assert again["signal"]["id"] == prod["signal"]["id"]
    assert again["signal"]["id"] != prev["signal"]["id"]


# ── Reads are scoped ────────────────────────────────────────────────────────

@pytest.fixture()
def two_environments(store, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    prev = store.create_signal(**_payload(symbol="eth"))
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    prod = store.create_signal(**_payload(symbol="btc"))
    return prev["signal"], prod["signal"]


def test_reads_default_to_this_environment_only(store, two_environments):
    prev, prod = two_environments
    items = store.list_signals()["items"]
    ids = {r["id"] for r in items}
    assert prod["id"] in ids
    assert prev["id"] not in ids, "production must not serve a preview deploy's signals"


def test_active_list_is_scoped_too(store, two_environments):
    prev, prod = two_environments
    ids = {r["id"] for r in store.list_active_signals()}
    assert ids == {prod["id"]}


def test_environment_all_shows_everything(store, two_environments):
    prev, prod = two_environments
    ids = {r["id"] for r in store.list_signals(environment="all")["items"]}
    assert ids == {prev["id"], prod["id"]}


def test_an_explicit_environment_can_be_inspected(store, two_environments):
    prev, _ = two_environments
    res = store.list_signals(environment="preview")
    assert [r["id"] for r in res["items"]] == [prev["id"]]
    assert res["total"] == 1, "the total must be scoped as well, not just the page"


def test_a_junk_environment_filter_is_a_validation_error(store):
    with pytest.raises(store.SignalValidationError):
        store.list_signals(environment="'; DROP TABLE signals; --")


def test_usage_report_breaks_rows_down_by_environment(store, two_environments):
    rep = store.usage_report()
    assert rep["signals_by_environment"] == {"preview": 1, "production": 1}
    assert rep["environment"] == "production"


# ── Publish path ────────────────────────────────────────────────────────────

def test_the_publish_path_reports_the_recorded_environment(store, monkeypatch):
    import signal_publish as sp
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "preview")
    rec = {"symbol": "BTC", "direction": "LONG", "timeframe": "2H",
           "entry": 100.5, "sl": 95.25, "tp_targets": [110.5, 120.0],
           "display_strength": 61.5}
    ms = int((BASE + timedelta(hours=2)).timestamp() * 1000)
    analysis = {"symbol": "BTC", "timeframe": "2H", "data_source": "binance",
                "signal_candle_closed_at": ms,
                "candles": [{"timestamp": ms - i * 7200_000, "open": 1, "high": 2,
                             "low": 0.5, "close": 1.5} for i in range(60)][::-1],
                "signal": {"score": 70, "strength": 61.5, "rr_ratio": 2.1},
                "data_quality": "good"}

    out = sp.persist_recommendations([rec], {"BTC": analysis})
    assert out["all_actionable"] is True
    assert out["environment"] == "preview"
    assert out["environment_recorded"] == "preview", \
        "reported from the stored row, not from the env var"
