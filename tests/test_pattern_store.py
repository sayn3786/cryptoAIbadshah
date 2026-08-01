"""
The pattern lifecycle log.

Pattern state was entirely ephemeral: recomputed from candles on every request,
so "this divergence was confirmed on the 4pm bar and expired eleven candles
later" survived only while those candles stayed inside the lookback window.

**A log, never an input.** The detectors read candles and are the only source of
truth about pattern state. If a row here ever disagreed with a recomputation the
recomputation is right, so nothing in the scoring path may read it — the same
rule that keeps postmortem data from modifying live strategy parameters.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import lifecycle as L                                              # noqa: E402
import pattern_store as ps                                         # noqa: E402


CLOSE = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)


def _pat(kind, age, **extra):
    life = L.classify(age, kind)
    assert life, f"{kind} at age {age} should not be dropped by this fixture"
    return {"kind": kind, **life, **extra}


# ── Building rows ──────────────────────────────────────────────────────────

def test_a_row_is_built_per_pattern():
    rows = ps.build_events("tao", "2H", CLOSE,
                           [_pat("choch", 2, signal="bullish"),
                            _pat("rsi_divergence", 1, type="bullish")])
    assert len(rows) == 2
    assert {r["pattern_kind"] for r in rows} == {"choch", "rsi_divergence"}


def test_the_symbol_is_upper_cased():
    # The table CHECK-constrains this; sending lower-case would fail the insert.
    assert ps.build_events("tao", "2H", CLOSE,
                           [_pat("choch", 1, signal="bullish")])[0]["symbol"] == "TAO"


def test_a_pattern_with_no_status_is_skipped():
    # Half a row is worse than no row: it cannot say what it observed.
    assert ps.build_events("TAO", "2H", CLOSE, [{"kind": "choch"}]) == []


def test_an_unknown_kind_is_not_recorded_under_a_name_nothing_recognises():
    assert ps.build_events("TAO", "2H", CLOSE,
                           [{"kind": "astrology", "status": "confirmed"}]) == []


def test_every_recordable_kind_has_a_lifecycle_window():
    # A kind the store accepts but the lifecycle has never heard of would be
    # logged with a window nothing chose.
    for kind in ps.PATTERN_KINDS:
        assert kind in L.FRESH_BARS, f"{kind} has no window"


def test_the_statuses_match_the_lifecycle_plus_invalidated():
    # `invalidated` comes from the flag/wedge detectors, which have carried it
    # since before the lifecycle module existed.
    assert set(L.STATUSES) <= set(ps.OBSERVABLE_STATUSES)


# ── The detail allow-list ──────────────────────────────────────────────────

def test_detail_copies_only_named_keys():
    rows = ps.build_events("TAO", "2H", CLOSE, [
        _pat("choch", 1, signal="bullish", level=188.4,
             api_key="sk-must-never-be-stored", candles=[{"o": 1}] * 500)])
    detail = rows[0]["detail"]
    assert detail["signal"] == "bullish" and detail["level"] == 188.4
    assert "api_key" not in detail
    assert "candles" not in detail, "raw series must never reach the log"


def test_long_strings_are_clipped():
    rows = ps.build_events("TAO", "2H", CLOSE,
                           [_pat("choch", 1, signal="bullish", description="x" * 5000)])
    assert len(rows[0]["detail"]["description"]) < 500


# ── Idempotency is on the BAR ──────────────────────────────────────────────

def test_the_same_observation_on_the_same_bar_is_one_event():
    # The analysis is recomputed on every dashboard load. Without this, a busy
    # afternoon would write the same CHoCH hundreds of times.
    a = ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed", CLOSE)
    b = ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed", CLOSE)
    assert a == b


def test_a_different_status_on_the_same_bar_is_a_different_event():
    # forming -> confirmed on the same bar is the lifecycle moving, and that is
    # exactly what the log exists to capture.
    a = ps.idempotency_key("production", "TAO", "2H", "choch", "forming", CLOSE)
    b = ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed", CLOSE)
    assert a != b


def test_a_later_bar_is_a_different_event():
    later = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    assert ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed", CLOSE) \
        != ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed", later)


def test_environments_do_not_collide():
    # DATABASE_URL is shared, so without this a preview deploy's observations
    # would be indistinguishable from production's.
    assert ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed", CLOSE) \
        != ps.idempotency_key("preview", "TAO", "2H", "choch", "confirmed", CLOSE)


def test_a_naive_timestamp_is_refused():
    from signal_store import SignalValidationError
    with pytest.raises(SignalValidationError):
        ps.idempotency_key("production", "TAO", "2H", "choch", "confirmed",
                           datetime(2026, 8, 1, 8))


# ── It must stay a log ─────────────────────────────────────────────────────

def test_the_scoring_path_never_reads_the_log():
    # The whole design rests on this. If signals.py ever imports the store, the
    # database becomes a second opinion about pattern state — and it would be
    # the wrong one, because only the detectors read candles.
    for module in ("signals.py", "indicators.py", "patterns.py"):
        src = open(os.path.join(os.path.dirname(__file__), "..", "backend", module),
                   encoding="utf-8").read()
        assert "pattern_store" not in src, f"{module} must not read the log"


def test_recording_is_a_no_op_before_the_migration_runs():
    # Deploy-then-migrate: the code ships first. Recording must degrade, not
    # raise on every publication.
    import inspect
    src = inspect.getsource(ps.record_events)
    assert "has_pattern_events" in src
    assert "MIGRATION_005_NOT_APPLIED" in src


def test_an_empty_batch_writes_nothing():
    assert ps.record_events([])["recorded"] == 0


def test_the_publish_path_never_lets_the_log_break_publication():
    import inspect
    import app
    src = inspect.getsource(app._compute_recommendations)
    block = src.split("pattern log", 1)[1]
    assert "except Exception" in block, "logging must not stop a signal publishing"
