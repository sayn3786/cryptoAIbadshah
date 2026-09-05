"""
v50 confluence brakes — RSI swing-reversal, OBV divergence (CVD-guarded), and
Fibonacci golden-pocket misalignment.

Each was shown but never scored before v50, and each was over-represented in the
v49 losers while the highest-strength tier lost the most. So all three are
BRAKES: they dock a trade fighting the read and never reward one agreeing with it
(which would push more trades into the losing top tier). These tests pin that
asymmetry and the CVD double-count guard.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signals                                                        # noqa: E402


# ── RSI swing-reversal brake ──────────────────────────────────────────────────

def test_rsi_reversal_docks_a_long_under_an_overbought_top():
    m = [{"timestamp": 5, "kind": "overbought_top"}, {"timestamp": 1, "kind": "oversold_bottom"}]
    assert signals.rsi_reversal_delta("LONG", m) == -6          # latest marker opposes


def test_rsi_reversal_docks_a_short_under_an_oversold_bottom():
    assert signals.rsi_reversal_delta("SHORT", [{"timestamp": 9, "kind": "oversold_bottom"}]) == -6


def test_rsi_reversal_is_a_brake_not_a_bonus():
    # A marker that AGREES with the trade earns nothing.
    assert signals.rsi_reversal_delta("LONG", [{"timestamp": 9, "kind": "oversold_bottom"}]) == 0


def test_rsi_reversal_uses_only_the_latest_marker():
    # Older opposing marker, newer agreeing one → not braked.
    m = [{"timestamp": 1, "kind": "overbought_top"}, {"timestamp": 9, "kind": "oversold_bottom"}]
    assert signals.rsi_reversal_delta("LONG", m) == 0


def test_rsi_reversal_none_or_neutral_is_zero():
    assert signals.rsi_reversal_delta("LONG", None) == 0
    assert signals.rsi_reversal_delta("LONG", []) == 0
    assert signals.rsi_reversal_delta("NEUTRAL", [{"timestamp": 1, "kind": "overbought_top"}]) == 0


# ── OBV divergence brake (CVD-guarded) ────────────────────────────────────────

def test_obv_docks_a_long_on_bearish_divergence_when_cvd_is_silent():
    assert signals.obv_guard_delta("LONG", {"divergence": "bearish"}, False) == -5


def test_obv_does_not_fire_when_cvd_already_spoke():
    # The whole point of the guard: never double-count volume against CVD.
    assert signals.obv_guard_delta("LONG", {"divergence": "bearish"}, True) == 0


def test_obv_is_a_brake_not_a_bonus():
    assert signals.obv_guard_delta("LONG", {"divergence": "bullish"}, False) == 0
    assert signals.obv_guard_delta("SHORT", {"divergence": "bullish"}, False) == -5


def test_obv_no_divergence_is_zero():
    assert signals.obv_guard_delta("LONG", {"divergence": None}, False) == 0
    assert signals.obv_guard_delta("LONG", {}, False) == 0


# ── Fibonacci golden-pocket brake ─────────────────────────────────────────────

def test_fib_docks_a_long_into_a_downleg_premium_zone():
    # bias "short" = down-leg, the 0.618–0.786 band is premium/resistance.
    assert signals.fib_alignment_delta("LONG", {"in_golden_pocket": True, "bias": "short"}) == -5


def test_fib_docks_a_short_into_an_upleg_discount_zone():
    assert signals.fib_alignment_delta("SHORT", {"in_entry_zone": True, "bias": "long"}) == -5


def test_fib_is_a_brake_not_a_bonus():
    # Trading WITH the zone bias is not braked (and earns no bonus).
    assert signals.fib_alignment_delta("LONG", {"in_golden_pocket": True, "bias": "long"}) == 0


def test_fib_only_fires_inside_the_zone():
    # A misaligned bias but price not in the entry zone → not actionable, no dock.
    assert signals.fib_alignment_delta("LONG", {"bias": "short"}) == 0


def test_fib_none_or_missing_is_zero():
    assert signals.fib_alignment_delta("LONG", None) == 0
    assert signals.fib_alignment_delta("NEUTRAL", {"in_golden_pocket": True, "bias": "short"}) == 0
