"""
Signal-store rules that need NO database.

These cover the parts that protect real money — price geometry, the lifecycle
state machine, decimal precision, UTC handling and event idempotency keys — so
they run in every CI environment, not only where Postgres is available.

Database-backed behaviour lives in test_signal_database.py.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signal_store import (                                            # noqa: E402
    ALLOWED_TRANSITIONS, STATUSES, TERMINAL_STATUSES,
    InvalidTransition, SignalValidationError,
    assert_transition, make_idempotency_key, validate_price_structure,
    _dec, _utc,
)


# ── LONG / SHORT price structure ────────────────────────────────────────────

def test_valid_long_and_short_structures_pass():
    validate_price_structure("LONG", "100", "90", ["110", "120", "130"])
    validate_price_structure("SHORT", "100", "110", ["90", "80", "70"])


def test_long_rejects_stop_at_or_above_entry():
    with pytest.raises(SignalValidationError, match="below entry"):
        validate_price_structure("LONG", "100", "100", ["110"])
    with pytest.raises(SignalValidationError, match="below entry"):
        validate_price_structure("LONG", "100", "101", ["110"])


def test_long_rejects_target_at_or_below_entry():
    with pytest.raises(SignalValidationError, match="above entry"):
        validate_price_structure("LONG", "100", "90", ["99"])
    with pytest.raises(SignalValidationError, match="above entry"):
        validate_price_structure("LONG", "100", "90", ["110", "100"])


def test_short_rejects_stop_at_or_below_entry():
    with pytest.raises(SignalValidationError, match="above entry"):
        validate_price_structure("SHORT", "100", "100", ["90"])
    with pytest.raises(SignalValidationError, match="above entry"):
        validate_price_structure("SHORT", "100", "99", ["90"])


def test_short_rejects_target_at_or_above_entry():
    with pytest.raises(SignalValidationError, match="below entry"):
        validate_price_structure("SHORT", "100", "110", ["101"])


def test_targets_must_step_away_from_entry_in_order():
    # TP2 closer than TP1 would make "TP2 hit" mean less profit than "TP1 hit".
    with pytest.raises(SignalValidationError, match="TP1 < TP2"):
        validate_price_structure("LONG", "100", "90", ["120", "110"])
    with pytest.raises(SignalValidationError, match="TP1 > TP2"):
        validate_price_structure("SHORT", "100", "110", ["80", "90"])


def test_non_positive_and_non_numeric_prices_rejected():
    with pytest.raises(SignalValidationError):
        validate_price_structure("LONG", "0", "90", ["110"])
    with pytest.raises(SignalValidationError):
        validate_price_structure("LONG", "100", "-1", ["110"])
    with pytest.raises(SignalValidationError):
        validate_price_structure("LONG", "100", "90", ["0"])
    with pytest.raises(SignalValidationError):
        validate_price_structure("LONG", "abc", "90", ["110"])
    with pytest.raises(SignalValidationError):
        validate_price_structure("LONG", float("inf"), "90", ["110"])


def test_direction_must_be_long_or_short():
    for bad in ("NEUTRAL", "long", "", None):
        with pytest.raises(SignalValidationError):
            validate_price_structure(bad, "100", "90", ["110"])


def test_signal_with_no_targets_is_allowed():
    # Entry + stop is a complete trade; targets are optional.
    validate_price_structure("LONG", "100", "90", [])


# ── Decimal precision ───────────────────────────────────────────────────────

def test_prices_go_through_str_not_float():
    # Decimal(0.1) is 0.1000000000000000055511151231257827.
    assert _dec("0.1", "p") == Decimal("0.1")
    assert _dec(0.1, "p") == Decimal("0.1")


def test_satoshi_scale_precision_survives():
    tiny = "0.000000012345"
    assert _dec(tiny, "p") == Decimal(tiny)


def test_tiny_prices_serialize_without_scientific_notation():
    # str(Decimal("0.000000012345")) is "1.2345E-8". Exact, but a consumer
    # parsing an API response should not have to handle E-notation, so the
    # row serialiser uses format(v, "f").
    from signal_store import _row_to_dict

    class _FakeRow:
        _mapping = {"entry_price": Decimal("0.000000012345")}

    assert _row_to_dict(_FakeRow())["entry_price"] == "0.000000012345"


def test_large_price_precision_survives():
    big = "64123.456789012345"
    assert _dec(big, "p") == Decimal(big)


# ── UTC handling ────────────────────────────────────────────────────────────

def test_naive_datetime_is_rejected_not_assumed_utc():
    with pytest.raises(SignalValidationError, match="naive"):
        _utc(datetime(2026, 1, 1, 12, 0), "candle_close_time")


def test_epoch_millis_and_iso_and_aware_all_normalize_to_utc():
    expected = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert _utc(1767268800000, "t") == expected
    assert _utc("2026-01-01T12:00:00Z", "t") == expected
    assert _utc("2026-01-01T12:00:00+00:00", "t") == expected
    # A non-UTC aware value is converted, not rejected.
    sgt = timezone(timedelta(hours=8))
    assert _utc(datetime(2026, 1, 1, 20, 0, tzinfo=sgt), "t") == expected


# ── Lifecycle state machine ─────────────────────────────────────────────────

def test_open_may_reach_every_other_state():
    for target in ("PARTIAL_TP", "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"):
        assert_transition("OPEN", target)


def test_partial_tp_may_repeat_and_terminate():
    for target in ("PARTIAL_TP", "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"):
        assert_transition("PARTIAL_TP", target)


def test_terminal_states_are_dead_ends():
    for terminal in sorted(TERMINAL_STATUSES):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset(), f"{terminal} must be terminal"
        for target in STATUSES:
            with pytest.raises(InvalidTransition, match="terminal"):
                assert_transition(terminal, target)


def test_a_terminal_signal_can_never_reopen():
    for terminal in sorted(TERMINAL_STATUSES):
        for reopened in ("OPEN", "PARTIAL_TP"):
            with pytest.raises(InvalidTransition):
                assert_transition(terminal, reopened)


def test_tp_hit_and_sl_hit_cannot_both_be_the_outcome():
    # Whichever lands first is terminal, so the other is refused.
    with pytest.raises(InvalidTransition):
        assert_transition("TP_HIT", "SL_HIT")
    with pytest.raises(InvalidTransition):
        assert_transition("SL_HIT", "TP_HIT")


def test_unknown_statuses_are_rejected():
    with pytest.raises(InvalidTransition):
        assert_transition("NOT_A_STATUS", "OPEN")
    with pytest.raises(InvalidTransition):
        assert_transition("OPEN", "NOT_A_STATUS")


# ── Event idempotency keys ──────────────────────────────────────────────────

def test_idempotency_key_is_stable_for_the_same_source_event():
    a = make_idempotency_key("sig-1", "TARGET_HIT", "2026-01-01T00:00:00+00:00")
    b = make_idempotency_key("sig-1", "TARGET_HIT", "2026-01-01T00:00:00+00:00")
    assert a == b


def test_idempotency_key_varies_by_signal_type_and_time():
    base = ("sig-1", "TARGET_HIT", "2026-01-01T00:00:00+00:00")
    assert make_idempotency_key(*base) != make_idempotency_key("sig-2", base[1], base[2])
    assert make_idempotency_key(*base) != make_idempotency_key(base[0], "STOP_LOSS_HIT", base[2])
    assert make_idempotency_key(*base) != make_idempotency_key(
        base[0], base[1], "2026-01-01T02:00:00+00:00")


def test_idempotency_key_is_not_wall_clock_dependent():
    # Same source timestamp expressed two ways must collapse to one key, or a
    # replay a second later would look like a brand-new event.
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert (make_idempotency_key("s", "CLOSED", dt)
            == make_idempotency_key("s", "CLOSED", dt.isoformat()))


def test_unknown_event_type_rejected():
    with pytest.raises(SignalValidationError):
        make_idempotency_key("s", "NOT_AN_EVENT", "2026-01-01T00:00:00+00:00")
