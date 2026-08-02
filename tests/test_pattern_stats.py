"""
How patterns worked out, grouped by timeframe.

The question is "how many flags on 1H were invalidated, and why", so the log has
to be able to answer it. Three things were in the way:

  1. Detectors say "failed"; the lifecycle module says "invalidated". They mean
     the same state, and the allow-list only knew the second — so every failed
     flag, the single most common invalidation, was silently DROPPED. The events
     a postmortem is looking for were the ones going missing.
  2. Everything was logged as "2H", because the call site hardcoded it. Grouping
     by timeframe over one timeframe is not grouping.
  3. Nothing aggregated it.

Counting is the subtle part. A pattern that stays confirmed for six bars logs
six observations — one per bar, which is what makes the log idempotent — so
counting ROWS would report six confirmations about one pattern.

REQUIRES a throwaway PostgreSQL (TEST_DATABASE_URL); without one these skip.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pattern_store as ps                                             # noqa: E402

_MIG = os.path.join(os.path.dirname(__file__), "..", "database", "migrations")
MIGRATIONS = [os.path.join(_MIG, f) for f in (
    "001_initial_signal_schema.sql", "002_signal_environment.sql",
    "003_entry_fill_and_excursion.sql", "004_stop_moves_and_scaleout.sql",
    "005_pattern_events.sql")]

TEST_URL = (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL_TEST") or "").strip()
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

NOW = datetime.now(timezone.utc)


# ── The dropped-status bug, which needs no database ─────────────────────────

def test_a_failed_flag_is_recorded_rather_than_dropped():
    """
    "failed" is what a flag detector says when a breakout gives back its level.
    The allow-list only knew "invalidated", so these never reached the log at
    all — losing precisely the events this feature exists to count.
    """
    rows = ps.build_events("btc", "1H", NOW, [
        {"kind": "flag", "status": "failed", "type": "bullish", "direction": "LONG"}])
    assert len(rows) == 1
    assert rows[0]["status"] == "invalidated"


def test_every_spelling_of_broken_lands_on_one_word():
    """
    A query counting invalidations must not have to know three synonyms.
    """
    for spelling in ("failed", "broken", "retest_failed", "invalidated"):
        rows = ps.build_events("eth", "2H", NOW, [
            {"kind": "flag", "status": spelling, "type": "bearish"}])
        assert rows and rows[0]["status"] == "invalidated", spelling


def test_a_status_nobody_recognises_is_still_refused():
    """Normalising must not become a way in for anything at all."""
    assert ps.build_events("btc", "1H", NOW, [
        {"kind": "flag", "status": "wobbly", "type": "x"}]) == []


# ── Aggregation ─────────────────────────────────────────────────────────────

@pytest.fixture()
def store(monkeypatch):
    from sqlalchemy import event
    if "prod" in TEST_URL.lower():
        pytest.fail("TEST_DATABASE_URL looks like production — refusing to run")
    os.environ["DATABASE_URL"] = TEST_URL
    import db
    db.reset_engine()
    engine = db.get_engine()
    name = f"p_{uuid.uuid4().hex[:12]}"

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{name}"')
        conn.exec_driver_sql(f'SET search_path TO "{name}", public')
        for path in MIGRATIONS:
            conn.exec_driver_sql(open(path, encoding="utf-8").read())

    def _set_path(dbapi_conn, _rec):
        with dbapi_conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{name}", public')
    event.listen(engine, "connect", _set_path)
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    # pattern_store probes for the table on every call rather than caching it,
    # so there is no capability state to reset between tests.
    try:
        yield ps
    finally:
        event.remove(engine, "connect", _set_path)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{name}" CASCADE')
        db.reset_engine()


def _log(sym, tf, kind, status, bars_ago=0, ptype="bullish", direction="LONG"):
    close = NOW - timedelta(hours=bars_ago)
    rows = ps.build_events(sym, tf, close, [
        {"kind": kind, "status": status, "type": ptype, "direction": direction}])
    ps.record_events(rows)


def _group(out, tf, kind):
    for g in out["groups"]:
        if g["timeframe"] == tf and g["pattern_kind"] == kind:
            return g
    return None


def test_counts_are_grouped_by_timeframe_and_kind(store):
    _log("BTC", "1H", "flag", "confirmed")
    _log("BTC", "1H", "flag", "invalidated", ptype="bearish")
    _log("BTC", "4H", "flag", "confirmed")
    _log("BTC", "1H", "rsi_divergence", "confirmed")

    out = ps.stats(days=7)
    assert out["available"] is True
    h1 = _group(out, "1H", "flag")
    assert h1["confirmed"] == 1 and h1["invalidated"] == 1
    assert _group(out, "4H", "flag")["confirmed"] == 1
    assert _group(out, "1H", "rsi_divergence")["confirmed"] == 1


def test_one_pattern_observed_on_six_bars_counts_once(store):
    """
    The log records a row per bar, which is what makes it idempotent. Counting
    rows would call one persistent flag six separate confirmations.
    """
    for bars in range(6):
        _log("SOL", "2H", "flag", "confirmed", bars_ago=bars)
    assert _group(ps.stats(days=7), "2H", "flag")["confirmed"] == 1


def test_different_patterns_on_one_symbol_count_separately(store):
    _log("ADA", "1H", "flag", "confirmed", ptype="bullish", direction="LONG")
    _log("ADA", "1H", "flag", "confirmed", ptype="bearish", direction="SHORT")
    assert _group(ps.stats(days=7), "1H", "flag")["confirmed"] == 2


def test_the_invalidation_rate_is_of_what_resolved(store):
    """
    Three confirmed, one invalidated — a 25% failure rate. Patterns still
    forming are excluded: they have not resolved, and counting them either way
    would be a guess.
    """
    for i, sym in enumerate(("BTC", "ETH", "SOL")):
        _log(sym, "1H", "flag", "confirmed", ptype=f"t{i}")
    _log("XRP", "1H", "flag", "invalidated", ptype="t9")
    _log("LINK", "1H", "flag", "forming", ptype="t8")

    g = _group(ps.stats(days=7), "1H", "flag")
    assert g["confirmed"] == 3 and g["invalidated"] == 1
    assert g["forming"] == 1
    assert g["resolved"] == 4
    assert g["invalidation_rate"] == 0.25


def test_nothing_resolved_yet_gives_no_rate_rather_than_zero(store):
    """A 0% failure rate and "nothing has finished" are different claims."""
    _log("BTC", "1H", "flag", "forming")
    g = _group(ps.stats(days=7), "1H", "flag")
    assert g["forming"] == 1
    assert g["invalidation_rate"] is None


def test_the_window_is_honoured_and_reported(store):
    _log("BTC", "1H", "flag", "confirmed", bars_ago=24 * 40)     # 40 days back
    _log("BTC", "1H", "flag", "invalidated", bars_ago=1, ptype="bearish")

    recent = ps.stats(days=7)
    assert _group(recent, "1H", "flag")["confirmed"] == 0
    assert recent["days"] == 7

    wide = ps.stats(days=90)
    assert _group(wide, "1H", "flag")["confirmed"] == 1
    assert wide["days"] == 90


def test_it_can_be_narrowed_to_one_symbol(store):
    _log("BTC", "1H", "flag", "confirmed")
    _log("ETH", "1H", "flag", "confirmed")
    assert _group(ps.stats(days=7, symbol="BTC"), "1H", "flag")["confirmed"] == 1


def test_an_empty_log_is_not_an_error(store):
    out = ps.stats(days=7)
    assert out["available"] is True
    assert out["groups"] == []


# ── The rule that keeps it a log ────────────────────────────────────────────

def test_nothing_in_the_scoring_path_reads_the_stats():
    """
    A lopsided invalidation rate can inform a decision, but it must never move
    a signal by itself — the same rule that keeps postmortem data out of live
    strategy parameters.
    """
    for module in ("signals.py", "indicators.py", "patterns.py"):
        src = open(os.path.join(os.path.dirname(__file__), "..", "backend", module),
                   encoding="utf-8").read()
        assert "pattern_store" not in src, f"{module} imports the log"
