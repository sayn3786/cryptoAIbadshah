"""
The ladder shares the reader is actually shown get written down.

signal_targets.exit_fraction was added by migration 004 and read by
signal_store._realized_pct — but nothing ever WROTE it, so it was NULL on every
target row in production. The reader's documented fallback is an even split, so
every scale-out was silently scored as thirds.

That is not what the dashboard says. It has always told the reader
"TP1 — sell 50%, TP2 — sell 30%, TP3 — sell 20%". A trade that banked TP1 and
then reversed was therefore booked as if a third had come off at TP1 when the
advice given was half — understating the realised return of exactly the trades
the weighting was introduced to stop mis-scoring.

The database-backed tests here need a throwaway PostgreSQL (TEST_DATABASE_URL);
without one they skip and the pure tests still run.
"""
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_store as store                                          # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
_MIG = os.path.join(ROOT, "database", "migrations")
MIGRATIONS = (os.path.join(_MIG, "001_initial_signal_schema.sql"),
              os.path.join(_MIG, "002_signal_environment.sql"),
              os.path.join(_MIG, "003_entry_fill_and_excursion.sql"),
              os.path.join(_MIG, "004_stop_moves_and_scaleout.sql"))

TEST_URL = (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL_TEST") or "").strip()

needs_db = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set — database tests skipped")


# ── The plan itself ─────────────────────────────────────────────────────────

def test_the_standard_ladder_is_fifty_thirty_twenty():
    assert store.ladder_shares(3) == (Decimal("0.5"), Decimal("0.3"), Decimal("0.2"))


def test_the_shares_close_the_whole_position():
    """A ladder that leaves part of the position unaccounted for is a bug."""
    for count, shares in store.SCALE_OUT_SHARES.items():
        assert len(shares) == count
        assert sum(shares) == Decimal(1), f"{count}-rung ladder sums to {sum(shares)}"
        assert all(Decimal(0) < s <= Decimal(1) for s in shares)


def test_a_ladder_length_with_no_published_plan_has_no_opinion():
    """
    None means "no opinion" — the reader splits evenly, as documented. It must
    never be confused with zero, and must never be a guess.
    """
    for n in (0, 1, 2, 4, 5):
        assert store.ladder_shares(n) is None


def test_published_shares_match_what_the_dashboard_tells_the_reader():
    """
    The guard against drift. If someone edits the dashboard copy to 40/40/20
    without touching the constant, the record and the advice part company
    silently — which is the exact failure this whole change is fixing.
    """
    js = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"),
              encoding="utf-8").read()
    shown = re.findall(r"TP\s*(\d)\s*<span[^>]*>(?:—\s*sell\s*)?(\d+)%", js)
    assert shown, "could not find the scale-out copy in the dashboard"

    plan = store.ladder_shares(3)
    for rung, pct in shown:
        expected = plan[int(rung) - 1] * Decimal(100)
        assert Decimal(pct) == expected, (
            f"dashboard says TP{rung} sells {pct}% but SCALE_OUT_SHARES says {expected}%")


# ── Why it matters ──────────────────────────────────────────────────────────

def test_thirds_understate_a_banked_tp1_that_then_reverses():
    """
    +10% at TP1, the rest stopped at -5%. Half off is meaningfully better than
    a third off, and the record was reporting the third.
    """
    half = store.weighted_return("LONG", 100, [(Decimal("0.5"), 110)], 95)
    third = store.weighted_return("LONG", 100, [(Decimal(1) / Decimal(3), 110)], 95)
    assert half > third
    assert half == pytest.approx(Decimal("2.5"), abs=Decimal("0.0001"))


# ── Written to the database ─────────────────────────────────────────────────

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
    return f"x_{uuid.uuid4().hex[:12]}"


def _db(schema, sql):
    """Autocommit connection outside the engine — migrations carry their own BEGIN."""
    import psycopg
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
            cur.execute(sql)
            try:
                return cur.fetchall()
            except psycopg.ProgrammingError:
                return []


def _schema_with(engine, schema, migrations, monkeypatch):
    from sqlalchemy import event
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'SET search_path TO "{schema}", public')
        for path in migrations:
            conn.exec_driver_sql(open(path, encoding="utf-8").read())

    def _set_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
    event.listen(engine, "connect", _set_path)

    store.reset_capabilities()
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    return _set_path


@pytest.fixture()
def migrated(engine, schema, monkeypatch):
    """All four migrations — the shape production actually has."""
    from sqlalchemy import event
    listener = _schema_with(engine, schema, MIGRATIONS, monkeypatch)
    try:
        yield schema
    finally:
        store.reset_capabilities()
        event.remove(engine, "connect", listener)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture()
def pre_004(engine, schema, monkeypatch):
    """001-003 only: the column does not exist yet."""
    from sqlalchemy import event
    listener = _schema_with(engine, schema, MIGRATIONS[:3], monkeypatch)
    try:
        yield schema
    finally:
        store.reset_capabilities()
        event.remove(engine, "connect", listener)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


BASE = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)


def _payload(**over):
    p = dict(
        symbol="btc", exchange="binance", timeframe="2H", direction="LONG",
        strategy_name="mtf", strategy_version="v45_shares",
        candle_open_time=BASE, candle_close_time=BASE + timedelta(hours=2),
        generated_at=BASE + timedelta(hours=2),
        entry_price="100", stop_loss="95",
        targets=["110", "120", "130"],
        indicator_values={"rsi": 55.5}, market_context={"symbol": "BTC"},
        source_timestamps={"last_closed_candle_ms": 1767268800000},
        input_candle_count=60, confidence_score="61.5",
    )
    p.update(over)
    return p


def _fractions(schema, symbol="BTC"):
    return [r[0] for r in _db(schema, f"""
        SELECT t.exit_fraction FROM signal_targets t
        JOIN   signals s ON s.id = t.signal_id
        WHERE  s.symbol = '{symbol}'
        ORDER  BY t.target_number
    """)]


@needs_db
def test_creating_a_signal_records_the_published_shares(migrated):
    store.create_signal(**_payload())
    assert _fractions(migrated) == [Decimal("0.5"), Decimal("0.3"), Decimal("0.2")]


@needs_db
def test_a_ladder_we_have_no_plan_for_is_left_to_the_even_split(migrated):
    store.create_signal(**_payload(symbol="eth", targets=["110", "120"]))
    assert _fractions(migrated, "ETH") == [None, None]


@needs_db
def test_a_banked_tp1_is_scored_at_half_not_a_third(migrated):
    """End to end: the write feeds the read that produces realised return."""
    created = store.create_signal(**_payload())
    sid = created["signal"]["id"]
    store.record_entry_fill(sid, "100", BASE + timedelta(hours=2))
    store.record_target_hit(sid, 1, "110", BASE + timedelta(hours=4))
    closed = store.record_stop_loss_hit(sid, "95", BASE + timedelta(hours=6))

    realized = Decimal(str(closed["signal"]["realized_return_pct"]))
    # 0.5 * +10% + 0.5 * -5% = +2.5%. Under the old even split it was +1.67%.
    assert realized == pytest.approx(Decimal("2.5"), abs=Decimal("0.01"))


@needs_db
def test_publishing_still_works_before_the_column_exists(pre_004):
    """
    Deploy-before-migrate. Without migration 004 the shares are simply not
    written and the reader falls back to an even split, exactly as before.
    """
    created = store.create_signal(**_payload())
    assert created["created"] is True
    rows = _db(pre_004, "SELECT count(*) FROM signal_targets")
    assert rows[0][0] == 3
