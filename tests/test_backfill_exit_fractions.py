"""
Migration 006: rescore the history the way the advice was actually given.

The migration has to agree with signal_store.weighted_return, because that is
what produced every number it is replacing and what will produce every number
after it. Two implementations of the same arithmetic in two languages is a
drift risk, so the tests here run BOTH over the same rows and compare.

REQUIRES a throwaway PostgreSQL (TEST_DATABASE_URL). Without one these skip.
NEVER point that at production.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_store as store                                          # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
_MIG = os.path.join(ROOT, "database", "migrations")
BASE_MIGRATIONS = [os.path.join(_MIG, f) for f in (
    "001_initial_signal_schema.sql",
    "002_signal_environment.sql",
    "003_entry_fill_and_excursion.sql",
    "004_stop_moves_and_scaleout.sql",
)]
BACKFILL = os.path.join(_MIG, "006_backfill_exit_fractions.sql")
ROLLBACK = os.path.join(_MIG, "rollback", "006_rollback_backfill_exit_fractions.sql")

TEST_URL = (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL_TEST") or "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set — migration tests skipped")

BASE = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine():
    if "prod" in TEST_URL.lower():
        pytest.fail("TEST_DATABASE_URL looks like production — refusing to run")
    os.environ["DATABASE_URL"] = TEST_URL
    import db
    db.reset_engine()
    yield db.get_engine()
    db.reset_engine()


@pytest.fixture()
def schema(engine, monkeypatch):
    """001-004 applied, exit_fraction deliberately NOT written — pre-fix shape."""
    from sqlalchemy import event
    name = f"m_{uuid.uuid4().hex[:12]}"

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{name}"')
        conn.exec_driver_sql(f'SET search_path TO "{name}", public')
        for path in BASE_MIGRATIONS:
            conn.exec_driver_sql(open(path, encoding="utf-8").read())

    def _set_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{name}", public')
    event.listen(engine, "connect", _set_path)

    store.reset_capabilities()
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    try:
        yield name
    finally:
        store.reset_capabilities()
        event.remove(engine, "connect", _set_path)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{name}" CASCADE')


def _sql(schema, statement, fetch=True):
    import psycopg
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
            cur.execute(statement)
            if not fetch:
                return []
            try:
                return cur.fetchall()
            except psycopg.ProgrammingError:
                return []


def _run(schema, path):
    _sql(schema, open(path, encoding="utf-8").read(), fetch=False)


def _make(schema, *, symbol, direction, entry, stop, targets, n=3):
    """A pre-fix signal: three rungs, every exit_fraction NULL."""
    created = store.create_signal(
        symbol=symbol, exchange="binance", timeframe="2H", direction=direction,
        strategy_name="mtf", strategy_version="v43_wedgefix",
        candle_open_time=BASE, candle_close_time=BASE + timedelta(hours=2),
        generated_at=BASE + timedelta(hours=2),
        entry_price=str(entry), stop_loss=str(stop),
        targets=[str(t) for t in targets[:n]],
        indicator_values={}, market_context={}, source_timestamps={},
        input_candle_count=60)
    sid = created["signal"]["id"]
    # The application writes shares now; this migration exists for rows that
    # predate that, so put them back to NULL to recreate the real situation.
    _sql(schema, f"UPDATE signal_targets SET exit_fraction = NULL "
                 f"WHERE signal_id = '{sid}'", fetch=False)
    return sid


def _close(sid, *, hit_targets, hit_prices, close_price, direction, entry):
    store.record_entry_fill(sid, str(entry), BASE + timedelta(hours=3))
    for n, price in zip(hit_targets, hit_prices):
        store.record_target_hit(sid, n, str(price), BASE + timedelta(hours=4 + n))
    # Hitting the last rung is already terminal (TP_HIT) and closes the whole
    # position, so there is no remainder for a stop to take and the state
    # machine rightly refuses one.
    if len(hit_targets) < 3:
        store.record_stop_loss_hit(sid, str(close_price), BASE + timedelta(hours=12))


def _fracs(schema, sid):
    return [r[0] for r in _sql(schema, f"SELECT exit_fraction FROM signal_targets "
                                       f"WHERE signal_id='{sid}' ORDER BY target_number")]


def _realized(schema, sid):
    return _sql(schema, f"SELECT realized_return_pct FROM signals WHERE id='{sid}'")[0][0]


# ── The shares ──────────────────────────────────────────────────────────────

def test_a_three_rung_ladder_is_filled_in(schema):
    sid = _make(schema, symbol="btc", direction="LONG", entry=100, stop=95,
                targets=[110, 120, 130])
    assert _fracs(schema, sid) == [None, None, None]
    _run(schema, BACKFILL)
    assert _fracs(schema, sid) == [Decimal("0.5"), Decimal("0.3"), Decimal("0.2")]


def test_a_ladder_of_another_length_is_left_alone(schema):
    """No published plan for two rungs — NULL correctly means split evenly."""
    sid = _make(schema, symbol="eth", direction="LONG", entry=100, stop=95,
                targets=[110, 120], n=2)
    _run(schema, BACKFILL)
    assert _fracs(schema, sid) == [None, None]


def test_shares_written_by_the_application_are_not_overwritten(schema):
    """A post-fix signal must pass through untouched."""
    created = store.create_signal(
        symbol="sol", exchange="binance", timeframe="2H", direction="LONG",
        strategy_name="mtf", strategy_version="v45",
        candle_open_time=BASE, candle_close_time=BASE + timedelta(hours=2),
        generated_at=BASE + timedelta(hours=2),
        entry_price="100", stop_loss="95", targets=["110", "120", "130"],
        indicator_values={}, market_context={}, source_timestamps={},
        input_candle_count=60)
    sid = created["signal"]["id"]
    before = _fracs(schema, sid)
    assert before == [Decimal("0.5"), Decimal("0.3"), Decimal("0.2")]
    _run(schema, BACKFILL)
    assert _fracs(schema, sid) == before
    # It must also not be in the snapshot, or a rollback would NULL it.
    assert not _sql(schema, f"SELECT 1 FROM backfill_006_target_before "
                            f"WHERE signal_id='{sid}'")


# ── The rescore, which is the half that matters ─────────────────────────────

def test_a_banked_tp1_that_reverses_is_rescored_upward(schema):
    """
    THE case. Entry 100, TP1 110 hit, then stopped at 95.
    Thirds: 1/3*10 + 2/3*-5 = +0.0. Published plan: 0.5*10 + 0.5*-5 = +2.5.
    """
    sid = _make(schema, symbol="ada", direction="LONG", entry=100, stop=95,
                targets=[110, 120, 130])
    _close(sid, hit_targets=[1], hit_prices=[110], close_price=95, direction="LONG", entry=100)
    assert _realized(schema, sid) == pytest.approx(Decimal("0"), abs=Decimal("0.01"))
    _run(schema, BACKFILL)
    assert _realized(schema, sid) == pytest.approx(Decimal("2.5"), abs=Decimal("0.01"))


def test_a_trade_that_never_banked_anything_is_untouched(schema):
    """
    The whole position closes at close_price under either convention, so the
    number cannot move. If it does, the arithmetic is wrong.
    """
    sid = _make(schema, symbol="xrp", direction="LONG", entry=100, stop=95,
                targets=[110, 120, 130])
    store.record_entry_fill(sid, "100", BASE + timedelta(hours=3))
    store.record_stop_loss_hit(sid, "95", BASE + timedelta(hours=12))
    before = _realized(schema, sid)
    _run(schema, BACKFILL)
    assert _realized(schema, sid) == before


def test_a_short_is_rescored_in_the_right_direction(schema):
    """Direction sign errors are silent and inverted — worth its own case."""
    sid = _make(schema, symbol="bnb", direction="SHORT", entry=100, stop=105,
                targets=[90, 80, 70])
    _close(sid, hit_targets=[1], hit_prices=[90], close_price=105, direction="SHORT", entry=100)
    _run(schema, BACKFILL)
    # 0.5 * +10% + 0.5 * -5% = +2.5
    assert _realized(schema, sid) == pytest.approx(Decimal("2.5"), abs=Decimal("0.01"))


def test_the_sql_agrees_with_weighted_return(schema):
    """
    The anti-drift test. The migration reimplements weighted_return in SQL;
    if the two ever disagree, the history and the live scoring disagree.
    """
    cases = [
        ("LONG", 100, [110], [1], 95),
        ("LONG", 100, [110, 120], [1, 2], 97),
        ("LONG", 100, [110, 120, 130], [1, 2, 3], 130),
        ("SHORT", 100, [90], [1], 106),
        ("SHORT", 200, [180, 160], [1, 2], 210),
    ]
    ids = []
    for i, (direction, entry, prices, nums, close) in enumerate(cases):
        tps = [110, 120, 130] if direction == "LONG" else [90, 80, 70]
        if entry == 200:
            tps = [180, 160, 140]
        stop = entry * 0.95 if direction == "LONG" else entry * 1.05
        sid = _make(schema, symbol=f"c{i}", direction=direction, entry=entry,
                    stop=stop, targets=tps)
        _close(sid, hit_targets=nums, hit_prices=prices, close_price=close,
               direction=direction, entry=entry)
        ids.append((sid, direction, entry, prices, nums, close))

    _run(schema, BACKFILL)

    shares = {1: Decimal("0.5"), 2: Decimal("0.3"), 3: Decimal("0.2")}
    for sid, direction, entry, prices, nums, close in ids:
        expected = store.weighted_return(
            direction, entry,
            [(shares[n], p) for n, p in zip(nums, prices)],
            close)
        got = _realized(schema, sid)
        assert got == pytest.approx(expected, abs=Decimal("0.0001")), (
            f"{direction} entry={entry} hits={nums}: SQL {got} != python {expected}")


# ── Re-running and undoing ──────────────────────────────────────────────────

def test_running_it_twice_changes_nothing_the_second_time(schema):
    sid = _make(schema, symbol="inj", direction="LONG", entry=100, stop=95,
                targets=[110, 120, 130])
    _close(sid, hit_targets=[1], hit_prices=[110], close_price=95, direction="LONG", entry=100)
    _run(schema, BACKFILL)
    once = (_fracs(schema, sid), _realized(schema, sid))
    _run(schema, BACKFILL)
    assert (_fracs(schema, sid), _realized(schema, sid)) == once


def test_a_second_run_does_not_corrupt_the_snapshot(schema):
    """
    If the snapshot were rewritten on a re-run it would capture the MIGRATED
    values, and the rollback would then restore the migration instead of
    undoing it — a rollback that silently does nothing is worse than none.
    """
    sid = _make(schema, symbol="qnt", direction="LONG", entry=100, stop=95,
                targets=[110, 120, 130])
    _close(sid, hit_targets=[1], hit_prices=[110], close_price=95, direction="LONG", entry=100)
    _run(schema, BACKFILL)
    _run(schema, BACKFILL)
    snap = _sql(schema, f"SELECT exit_fraction FROM backfill_006_target_before "
                        f"WHERE signal_id='{sid}' ORDER BY target_number")
    assert [r[0] for r in snap] == [None, None, None], "snapshot was overwritten"


def test_the_rollback_restores_exactly_what_was_there(schema):
    sid = _make(schema, symbol="fet", direction="LONG", entry=100, stop=95,
                targets=[110, 120, 130])
    _close(sid, hit_targets=[1], hit_prices=[110], close_price=95, direction="LONG", entry=100)
    before = (_fracs(schema, sid), _realized(schema, sid))

    _run(schema, BACKFILL)
    assert (_fracs(schema, sid), _realized(schema, sid)) != before

    _run(schema, ROLLBACK)
    assert (_fracs(schema, sid), _realized(schema, sid)) == before
    assert not _sql(schema, "SELECT version FROM schema_migrations WHERE version='006'")


def test_the_migration_records_its_version(schema):
    _run(schema, BACKFILL)
    assert _sql(schema, "SELECT version FROM schema_migrations WHERE version='006'")
