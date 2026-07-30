"""
Database-backed tests for persistent signal tracking.

REQUIRES a throwaway PostgreSQL database. Set TEST_DATABASE_URL (or
DATABASE_URL_TEST); without one, every test here SKIPS and the rest of the
suite still runs.

    createdb cryptomonk_test
    TEST_DATABASE_URL="postgresql://localhost/cryptomonk_test" python -m pytest tests/test_signal_database.py

NEVER point this at production Neon. Each test runs inside its own dedicated
schema, which is dropped afterwards, so it cannot touch anything else — but the
guard below also refuses a URL that looks like production.
"""
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

TEST_URL = (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL_TEST") or "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_URL,
    reason="TEST_DATABASE_URL not set — database tests skipped (see module docstring)",
)

MIGRATION = os.path.join(os.path.dirname(__file__), "..",
                         "database", "migrations", "001_initial_signal_schema.sql")


def _guard_not_production(url: str) -> None:
    """Refuse to run destructive tests against anything that looks live."""
    lowered = url.lower()
    if "prod" in lowered or "production" in lowered:
        pytest.fail("TEST_DATABASE_URL looks like a production database — refusing to run")


@pytest.fixture()
def engine():
    """
    The engine the code under test will actually use.

    Function-scoped and resolved through db.get_engine() every time, because
    tests that simulate an outage call reset_engine(). A module-scoped object
    would go stale after that, and any `connect` listener pinned on it (see the
    `store` fixture's search_path) would silently stop applying to the engine
    the code is really talking to.
    """
    _guard_not_production(TEST_URL)
    os.environ["DATABASE_URL"] = TEST_URL
    import db
    db.reset_engine()
    yield db.get_engine()
    db.reset_engine()


@pytest.fixture()
def store(engine):
    """
    Fresh schema per test, migration applied into it, dropped afterwards.

    Isolating by schema (rather than by transaction) means the tests exercise
    the real COMMIT path — which is exactly what "the signal was persisted"
    has to mean.

    """
    from sqlalchemy import text
    schema = f"t_{uuid.uuid4().hex[:12]}"

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    sql = open(MIGRATION, "r", encoding="utf-8").read()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'SET search_path TO "{schema}", public')
        conn.exec_driver_sql(sql)

    # Pin every pooled connection for this test to the throwaway schema.
    from sqlalchemy import event
    def _set_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
    event.listen(engine, "connect", _set_path)

    import signal_store
    try:
        yield signal_store
    finally:
        event.remove(engine, "connect", _set_path)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


BASE = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)


def _payload(**over):
    p = dict(
        symbol="btc", exchange="binance", timeframe="2H", direction="LONG",
        strategy_name="mtf", strategy_version="v1",
        candle_open_time=BASE, candle_close_time=BASE + timedelta(hours=2),
        generated_at=BASE + timedelta(hours=2),
        entry_price="100.5", stop_loss="95.25",
        targets=["110.5", "120.75", "130"],
        indicator_values={"rsi": 55.5}, market_context={"symbol": "BTC"},
        source_timestamps={"last_closed_candle_ms": 1767268800000},
        input_candle_count=60, confidence_score="61.5",
    )
    p.update(over)
    return p


def _short(**over):
    base = dict(direction="SHORT", entry_price="100", stop_loss="110",
                targets=["90", "80", "70"])
    base.update(over)
    return _payload(**base)


# ── Migration ───────────────────────────────────────────────────────────────

def test_migration_creates_every_table_on_an_empty_database(store, engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        found = set(conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )).scalars().all())
    for t in ("schema_migrations", "signals", "signal_targets",
              "signal_indicator_snapshots", "signal_events", "signal_postmortems"):
        assert t in found, f"{t} missing after migration"


def test_migration_records_its_version(store, engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        versions = conn.execute(text(
            "SELECT version FROM schema_migrations")).scalars().all()
    assert "001" in versions


def test_no_floating_point_money_columns(store, engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        floats = conn.execute(text("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name IN ('signals','signal_targets','signal_postmortems')
              AND data_type IN ('double precision','real')
        """)).all()
    assert floats == [], f"float columns would corrupt prices: {floats}"


# ── Persistence ─────────────────────────────────────────────────────────────

def test_long_signal_persists_with_targets_snapshot_and_event(store):
    out = store.create_signal(**_payload())
    assert out["created"] is True
    full = store.get_signal(out["signal"]["id"])
    assert full["direction"] == "LONG"
    assert full["symbol"] == "BTC", "symbol must be normalized uppercase"
    assert full["status"] == "OPEN"
    assert [t["target_number"] for t in full["targets"]] == [1, 2, 3]
    assert full["indicator_snapshot"]["indicator_values"]["rsi"] == 55.5
    assert full["indicator_snapshot"]["input_candle_count"] == 60
    assert [e["event_type"] for e in full["events"]] == ["CREATED"]


def test_short_signal_persists(store):
    out = store.create_signal(**_short(symbol="ETH"))
    full = store.get_signal(out["signal"]["id"])
    assert full["direction"] == "SHORT" and full["symbol"] == "ETH"
    assert [t["target_price"].rstrip("0").rstrip(".") for t in full["targets"]] == ["90", "80", "70"]


def test_decimal_precision_round_trips_exactly(store):
    exact = "0.000000012345"
    out = store.create_signal(**_payload(symbol="PEPE", entry_price=exact,
                                         stop_loss="0.000000010000",
                                         targets=["0.000000020000"]))
    full = store.get_signal(out["signal"]["id"])
    from decimal import Decimal
    assert Decimal(full["entry_price"]) == Decimal(exact)
    assert "E" not in full["entry_price"].upper(), "must not serialize in scientific notation"


def test_timestamps_stored_and_returned_in_utc(store):
    sgt = timezone(timedelta(hours=8))
    local_close = (BASE + timedelta(hours=2)).astimezone(sgt)
    out = store.create_signal(**_payload(candle_close_time=local_close))
    full = store.get_signal(out["signal"]["id"])
    assert full["candle_close_time"].endswith("+00:00")
    assert datetime.fromisoformat(full["candle_close_time"]) == BASE + timedelta(hours=2)


def test_invalid_long_structure_is_never_written(store):
    with pytest.raises(store.SignalValidationError):
        store.create_signal(**_payload(stop_loss="105"))     # stop above entry
    assert store.list_signals()["total"] == 0


def test_invalid_short_structure_is_never_written(store):
    with pytest.raises(store.SignalValidationError):
        store.create_signal(**_short(stop_loss="90"))        # stop below entry
    assert store.list_signals()["total"] == 0


# ── Idempotency ─────────────────────────────────────────────────────────────

def test_same_closed_candle_returns_the_existing_signal(store):
    a = store.create_signal(**_payload())
    b = store.create_signal(**_payload())
    assert b["created"] is False and b["idempotent_hit"] is True
    assert b["signal"]["id"] == a["signal"]["id"]
    assert store.list_signals()["total"] == 1


def test_opposite_direction_on_the_same_candle_cannot_create_a_second_signal(store):
    a = store.create_signal(**_payload())
    b = store.create_signal(**_short())          # same candle, other direction
    assert b["created"] is False
    assert b["signal"]["id"] == a["signal"]["id"]
    assert b["signal"]["direction"] == "LONG", "the first decision stands"
    assert store.list_signals()["total"] == 1


def test_next_closed_candle_allows_a_new_signal_for_the_same_token(store):
    store.create_signal(**_payload())
    nxt = store.create_signal(**_payload(
        candle_open_time=BASE + timedelta(hours=2),
        candle_close_time=BASE + timedelta(hours=4)))
    assert nxt["created"] is True
    assert store.list_signals()["total"] == 2


def test_multiple_signals_same_day_on_different_candles(store):
    for i in range(4):                      # four 2H candles, one calendar day
        store.create_signal(**_payload(
            candle_open_time=BASE + timedelta(hours=2 * i),
            candle_close_time=BASE + timedelta(hours=2 * (i + 1))))
    rows = store.list_signals()["items"]
    assert len(rows) == 4
    days = {r["candle_close_time"][:10] for r in rows}
    assert len(days) == 1, "all four are on the same calendar day"


def test_strategy_versions_are_tracked_independently(store):
    a = store.create_signal(**_payload(strategy_version="v1"))
    b = store.create_signal(**_payload(strategy_version="v2"))
    assert a["created"] and b["created"]
    assert a["signal"]["id"] != b["signal"]["id"]
    assert store.list_signals(strategy_version="v2")["total"] == 1


def test_different_timeframes_are_independent(store):
    store.create_signal(**_payload(timeframe="1H"))
    store.create_signal(**_payload(timeframe="2H"))
    assert store.list_signals()["total"] == 2


# ── Atomicity ───────────────────────────────────────────────────────────────

def test_rollback_when_target_persistence_fails(store, engine, monkeypatch):
    """A failure writing targets must leave NO signal behind."""
    real_execute = None

    import sqlalchemy.orm

    calls = {"n": 0}
    orig = sqlalchemy.orm.Session.execute

    def boom(self, clause, *a, **kw):
        text_ = str(clause)
        if "INSERT INTO signal_targets" in text_:
            calls["n"] += 1
            raise RuntimeError("simulated target write failure")
        return orig(self, clause, *a, **kw)

    monkeypatch.setattr(sqlalchemy.orm.Session, "execute", boom)
    with pytest.raises(RuntimeError, match="simulated target write failure"):
        store.create_signal(**_payload())
    monkeypatch.undo()

    assert calls["n"] == 1
    assert store.list_signals()["total"] == 0, "signal row survived a failed target write"


def test_rollback_when_snapshot_persistence_fails(store, monkeypatch):
    import sqlalchemy.orm
    orig = sqlalchemy.orm.Session.execute

    def boom(self, clause, *a, **kw):
        if "INSERT INTO signal_indicator_snapshots" in str(clause):
            raise RuntimeError("simulated snapshot write failure")
        return orig(self, clause, *a, **kw)

    monkeypatch.setattr(sqlalchemy.orm.Session, "execute", boom)
    with pytest.raises(RuntimeError, match="simulated snapshot write failure"):
        store.create_signal(**_payload())
    monkeypatch.undo()

    assert store.list_signals()["total"] == 0
    full = store.list_signals(include_archived=True)
    assert full["total"] == 0, "no orphaned signal or targets may remain"


def test_concurrent_duplicate_creates_produce_exactly_one_signal(store):
    """Two workers racing on the same closed candle must not both insert."""
    results, errors = [], []

    def worker():
        try:
            results.append(store.create_signal(**_payload()))
        except Exception as exc:                       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent create raised: {errors}"
    assert store.list_signals()["total"] == 1
    created = [r for r in results if r["created"]]
    assert len(created) == 1, "exactly one thread may claim creation"
    ids = {r["signal"]["id"] for r in results}
    assert len(ids) == 1, "every thread must see the same signal"


# ── Lifecycle ───────────────────────────────────────────────────────────────

def _mk(store, **over):
    return store.create_signal(**_payload(**over))["signal"]["id"]


def test_partial_then_final_target_hits(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    r1 = store.record_target_hit(sid, 1, "110.5", t, source_ts=t)
    assert r1["status"] == "PARTIAL_TP"
    r3 = store.record_target_hit(sid, 3, "130", t + timedelta(hours=1),
                                 source_ts=t + timedelta(hours=1))
    assert r3["status"] == "TP_HIT"
    from decimal import Decimal
    assert Decimal(r3["signal"]["realized_return_pct"]) > 0


def test_duplicate_target_hit_is_ignored(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    first = store.record_target_hit(sid, 1, "110.5", t, source_ts=t)
    again = store.record_target_hit(sid, 1, "110.5", t, source_ts=t)
    assert first["applied"] is True
    assert again["applied"] is False and again["duplicate"] is True
    full = store.get_signal(sid)
    hits = [e for e in full["events"] if e["event_type"] == "TARGET_HIT"]
    assert len(hits) == 1, "a replayed target hit must not append a second event"


def test_duplicate_stop_loss_is_ignored(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    assert store.record_stop_loss_hit(sid, "95.25", t, source_ts=t)["applied"] is True
    again = store.record_stop_loss_hit(sid, "95.25", t, source_ts=t)
    assert again["applied"] is False and again["duplicate"] is True
    full = store.get_signal(sid)
    assert len([e for e in full["events"] if e["event_type"] == "STOP_LOSS_HIT"]) == 1


def test_stop_loss_records_a_negative_return_for_a_long(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    r = store.record_stop_loss_hit(sid, "95.25", t, source_ts=t)
    from decimal import Decimal
    assert r["status"] == "SL_HIT"
    assert Decimal(r["signal"]["realized_return_pct"]) < 0


def test_terminal_signal_cannot_be_reopened_or_re_terminated(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    store.record_stop_loss_hit(sid, "95.25", t, source_ts=t)
    with pytest.raises(store.InvalidTransition):
        store.record_target_hit(sid, 1, "110.5", t, source_ts=t + timedelta(minutes=1))
    with pytest.raises(store.InvalidTransition):
        store.close_signal(sid, "99", t, source_ts=t + timedelta(minutes=2))
    assert store.get_signal(sid)["status"] == "SL_HIT"


def test_tp_hit_and_sl_hit_cannot_both_become_the_outcome(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    store.record_target_hit(sid, 3, "130", t, source_ts=t)       # terminal TP_HIT
    with pytest.raises(store.InvalidTransition):
        store.record_stop_loss_hit(sid, "95.25", t + timedelta(minutes=5),
                                   source_ts=t + timedelta(minutes=5))
    assert store.get_signal(sid)["status"] == "TP_HIT"


def test_concurrent_terminal_updates_yield_one_outcome(store):
    """A TP worker and an SL worker racing must not both terminate the signal."""
    sid = _mk(store)
    t = BASE + timedelta(hours=3)
    outcomes, errors = [], []

    def tp():
        try:
            outcomes.append(store.record_target_hit(sid, 3, "130", t, source_ts=t)["status"])
        except store.InvalidTransition:
            errors.append("tp-rejected")

    def sl():
        try:
            outcomes.append(store.record_stop_loss_hit(sid, "95.25", t, source_ts=t)["status"])
        except store.InvalidTransition:
            errors.append("sl-rejected")

    threads = [threading.Thread(target=tp), threading.Thread(target=sl)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(outcomes) == 1, f"exactly one terminal update may win, got {outcomes}"
    assert len(errors) == 1, "the loser must be rejected, not silently applied"
    assert store.get_signal(sid)["status"] in ("TP_HIT", "SL_HIT")


def test_close_expire_and_cancel(store):
    t = BASE + timedelta(hours=5)
    a = _mk(store, candle_close_time=BASE + timedelta(hours=2))
    b = _mk(store, candle_close_time=BASE + timedelta(hours=4))
    c = _mk(store, candle_close_time=BASE + timedelta(hours=6))
    assert store.close_signal(a, "105", t, source_ts=t)["status"] == "CLOSED"
    assert store.expire_signal(b, t, source_ts=t)["status"] == "EXPIRED"
    assert store.cancel_signal(c, t, source_ts=t)["status"] == "CANCELLED"


def test_cancelled_signal_keeps_its_original_decision_intact(store):
    sid = _mk(store)
    before = store.get_signal(sid)
    t = BASE + timedelta(hours=5)
    store.cancel_signal(sid, t, source_ts=t)
    after = store.get_signal(sid)
    for field in ("direction", "entry_price", "stop_loss",
                  "strategy_version", "candle_close_time"):
        assert before[field] == after[field], f"{field} must never be rewritten"
    assert before["targets"] == after["targets"]
    assert before["indicator_snapshot"] == after["indicator_snapshot"]


# ── Pagination, filters, archival ───────────────────────────────────────────

def test_pagination_and_filters(store):
    for i in range(7):
        store.create_signal(**_payload(
            symbol="BTC" if i % 2 else "ETH",
            candle_open_time=BASE + timedelta(hours=2 * i),
            candle_close_time=BASE + timedelta(hours=2 * (i + 1))))
    page1 = store.list_signals(limit=3, offset=0)
    page2 = store.list_signals(limit=3, offset=3)
    assert len(page1["items"]) == 3 and page1["total"] == 7 and page1["has_more"]
    assert {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]} == set()
    assert store.list_signals(symbol="btc")["total"] == 3
    assert store.list_signals(direction="LONG")["total"] == 7
    assert store.list_signals(direction="SHORT")["total"] == 0


def test_page_size_is_capped(store):
    assert store.list_signals(limit=10_000)["limit"] == store.MAX_PAGE_SIZE


def test_unknown_status_filter_is_rejected(store):
    with pytest.raises(store.SignalValidationError):
        store.list_signals(statuses=["NOT_A_STATUS"])


def test_archived_signals_are_hidden_by_default(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=5)
    store.close_signal(sid, "105", t, source_ts=t)
    store.archive_signal(sid)
    assert store.list_signals()["total"] == 0
    assert store.list_signals(include_archived=True)["total"] == 1
    assert store.list_active_signals() == []
    assert store.get_signal(sid) is not None, "archiving must not delete anything"


def test_open_signals_cannot_be_archived(store):
    sid = _mk(store)
    with pytest.raises(store.InvalidTransition):
        store.archive_signal(sid)


def test_active_list_excludes_terminal_signals(store):
    live = _mk(store, candle_close_time=BASE + timedelta(hours=2))
    done = _mk(store, candle_close_time=BASE + timedelta(hours=4))
    t = BASE + timedelta(hours=5)
    store.close_signal(done, "105", t, source_ts=t)
    ids = {s["id"] for s in store.list_active_signals()}
    assert ids == {live}


# ── Postmortems / usage ─────────────────────────────────────────────────────

def test_postmortem_upsert_and_listing(store):
    sid = _mk(store)
    t = BASE + timedelta(hours=5)
    store.record_stop_loss_hit(sid, "95.25", t, source_ts=t)
    store.upsert_postmortem(sid, outcome="LOSS", strategy_version="v1",
                            mfe_pct="1.5", mae_pct="-5.25", duration_minutes=180,
                            failed_conditions=["volume_confirmation_failed"],
                            analysis_summary="Breakout failed on weak volume.")
    # Revising ONE field must not wipe the others.
    again = store.upsert_postmortem(sid, outcome="LOSS", strategy_version="v1",
                                    analysis_summary="Revised.")
    assert again["analysis_summary"] == "Revised."
    assert again["failed_conditions"] == ["volume_confirmation_failed"]
    assert again["duration_minutes"] == 180
    listing = store.list_postmortems(outcome="LOSS")
    assert listing["total"] == 1
    assert listing["items"][0]["symbol"] == "BTC"
    full = store.get_signal(sid)
    assert full["postmortem"]["failed_conditions"] == ["volume_confirmation_failed"]


def test_postmortem_does_not_alter_the_original_signal(store):
    sid = _mk(store)
    before = store.get_signal(sid)
    store.upsert_postmortem(sid, outcome="WIN", strategy_version="v1")
    after = store.get_signal(sid)
    for f in ("direction", "entry_price", "stop_loss", "status", "strategy_version"):
        assert before[f] == after[f]


def test_usage_report_shape(store):
    sid = _mk(store, candle_close_time=BASE + timedelta(hours=2))
    other = _mk(store, candle_close_time=BASE + timedelta(hours=4))
    t = BASE + timedelta(hours=5)
    store.close_signal(other, "105", t, source_ts=t)
    store.archive_signal(other)
    rep = store.usage_report()
    assert rep["signals_total"] == 2
    assert rep["active_signals"] == 1
    assert rep["archived_signals"] == 1
    assert rep["database_size_bytes"] > 0
    assert rep["oldest_signal_at"] and rep["newest_signal_at"]
    assert "60-70%" in rep["review_guidance"]


# ── Outage behaviour ────────────────────────────────────────────────────────

def test_database_outage_produces_a_controlled_failure(monkeypatch):
    """An unreachable database must raise a clean error, never leak the DSN."""
    import db
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:hunter2@127.0.0.1:1/nope?sslmode=disable&connect_timeout=1")
    db.reset_engine()
    try:
        import signal_store
        with pytest.raises(Exception) as exc:
            signal_store.list_signals()
        assert "hunter2" not in db.sanitize_db_error(exc.value)
        health = db.healthcheck()
        assert health["ok"] is False
        assert health["error_code"] == "DB_UNAVAILABLE"
        assert "hunter2" not in repr(health)
    finally:
        db.reset_engine()
        monkeypatch.undo()
        db.reset_engine()


# ── Health probe must separate reachability from migration state ────────────
# Regression: `SELECT 1` and the migrations query ran in one try block, so a
# perfectly healthy but UNMIGRATED database reported DB_UNAVAILABLE — sending
# you to look for a connection fault that did not exist.

def test_health_on_a_reachable_but_unmigrated_database(engine):
    """The reported preview state: connected fine, migration never run."""
    import db
    from sqlalchemy import text
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    # Point search_path at an EMPTY schema and hide public, so nothing is found.
    from sqlalchemy import event

    def _empty_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')

    event.listen(engine, "connect", _empty_path)
    try:
        h = db.healthcheck()
        assert h["reachable"] is True, "the connection itself works"
        assert h["migrated"] is False
        assert h["ok"] is False
        assert h["error_code"] == "DB_NOT_MIGRATED", \
            "must NOT be reported as an unreachable database"
        assert h["missing_tables"], "say which tables are absent"
        assert "SQL Editor" in h["hint"], "the hint must be actionable"
    finally:
        event.remove(engine, "connect", _empty_path)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def test_health_on_a_fully_migrated_database(store, engine):
    import db
    h = db.healthcheck()
    assert h["ok"] is True, f"unexpected: {h}"
    assert h["reachable"] is True and h["migrated"] is True
    assert "001" in h["migrations_applied"]
    assert "error_code" not in h
    assert "missing_tables" not in h


def test_health_still_flags_a_genuinely_unreachable_database(monkeypatch):
    import db
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:secret@127.0.0.1:1/x?sslmode=disable&connect_timeout=1")
    db.reset_engine()
    try:
        h = db.healthcheck()
        assert h["reachable"] is False and h["migrated"] is False
        assert h["error_code"] == "DB_UNAVAILABLE"
        assert "secret" not in repr(h), "still must not leak the password"
    finally:
        db.reset_engine()
        monkeypatch.undo()
        db.reset_engine()


def test_health_reports_expected_tables_consistently_with_the_migration(engine):
    """EXPECTED_TABLES must match what the migration actually creates."""
    import db
    from sqlalchemy import text
    sql = open(MIGRATION, encoding="utf-8").read()
    for table in db.EXPECTED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, \
            f"{table} is expected by the health probe but not created by the migration"
