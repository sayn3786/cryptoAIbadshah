"""
Reward/risk and price-geometry validation must FAIL CLOSED (item 2).

Before v51, meets_rr() passed None and unreadable ratios, and the gate trusted a
supplied rr_ratio it never checked against the geometry — so a trade with no
defined reward, or a fabricated ratio, could reach publication. These pin the
new contract: only a finite, geometrically-consistent R/R at or above MIN_RR
publishes, for both LONG and SHORT, and the same shared pure functions run in
production and the backtest.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import rec_policy as rp                                              # noqa: E402


# ── meets_rr fails closed ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, "", "abc", float("nan"),
                                 float("inf"), float("-inf"), 0, 0.0, -1.0, -3.2])
def test_meets_rr_rejects_unreadable_and_nonpositive(bad):
    assert rp.meets_rr(bad) is False


@pytest.mark.parametrize("good", [1.5, 2.0, 3.7, "1.6"])
def test_meets_rr_accepts_finite_at_or_above_floor(good):
    assert rp.meets_rr(good) is True


def test_meets_rr_rejects_just_below_floor():
    assert rp.meets_rr(1.49) is False


# ── recompute_rr ──────────────────────────────────────────────────────────────

def test_recompute_rr_matches_the_hand_figure():
    # LONG: entry 100, stop 90 (risk 10), target 120 (reward 20) → 2.0
    assert rp.recompute_rr(100, 90, 120) == pytest.approx(2.0)


@pytest.mark.parametrize("e,s,t", [
    (None, 90, 120), (100, None, 120), (100, 90, None),
    (float("nan"), 90, 120), (100, float("inf"), 120), (100, 90, float("nan")),
    (100, 100, 120),                      # zero risk (stop == entry)
    (-100, 90, 120), (100, -90, 120), (100, 90, -120),   # non-positive
    ("x", 90, 120),
])
def test_recompute_rr_returns_none_on_bad_input(e, s, t):
    assert rp.recompute_rr(e, s, t) is None


# ── validate_geometry_and_rr: LONG ────────────────────────────────────────────

def _long(entry=100.0, sl=90.0, tps=(110.0, 120.0, 130.0), rr=None):
    return rp.validate_geometry_and_rr("LONG", entry, sl, tps, rr)


def test_long_valid_geometry_passes():
    v = _long()                                         # rr = (120-100)/10 = 2.0
    assert v["ok"] and v["reason"] is None
    assert v["rr"] == pytest.approx(2.0)


def test_long_reversed_stop_is_invalid_geometry():
    # Stop ABOVE entry on a LONG — reversed structure.
    assert _long(sl=110.0)["reason"] == "INVALID_GEOMETRY"


def test_long_target_below_entry_is_invalid_geometry():
    assert _long(tps=(110.0, 90.0, 130.0))["reason"] == "INVALID_GEOMETRY"


@pytest.mark.parametrize("tps", [(), (None, None, None), (float("nan"),)])
def test_long_missing_or_unreadable_targets_is_invalid_geometry(tps):
    assert _long(tps=tps)["reason"] == "INVALID_GEOMETRY"


@pytest.mark.parametrize("bad_entry", [None, float("nan"), float("inf"), 0, -100])
def test_long_bad_entry_is_invalid_geometry(bad_entry):
    assert _long(entry=bad_entry)["reason"] == "INVALID_GEOMETRY"


def test_long_low_rr_is_low_rr():
    # entry 100, sl 90 (risk 10), TP2 112 (reward 12) → 1.2 < 1.5
    v = _long(tps=(105.0, 112.0, 120.0))
    assert v["reason"] == "LOW_RR" and v["rr"] == pytest.approx(1.2)


def test_long_fabricated_stored_rr_is_invalid_rr():
    # Geometry gives 2.0 but the candidate claims 5.0 — reject the lie.
    v = _long(rr=5.0)
    assert v["reason"] == "INVALID_RR"


def test_long_stored_rr_within_tolerance_passes():
    v = _long(rr=2.02)                                  # ~rounding of 2.0
    assert v["ok"]


# ── validate_geometry_and_rr: SHORT ───────────────────────────────────────────

def _short(entry=100.0, sl=110.0, tps=(90.0, 80.0, 70.0), rr=None):
    return rp.validate_geometry_and_rr("SHORT", entry, sl, tps, rr)


def test_short_valid_geometry_passes():
    v = _short()                                        # rr = (100-80)/10 = 2.0
    assert v["ok"] and v["rr"] == pytest.approx(2.0)


def test_short_reversed_stop_is_invalid_geometry():
    assert _short(sl=90.0)["reason"] == "INVALID_GEOMETRY"


def test_short_target_above_entry_is_invalid_geometry():
    assert _short(tps=(90.0, 120.0, 70.0))["reason"] == "INVALID_GEOMETRY"


def test_short_low_rr_is_low_rr():
    # entry 100, sl 110 (risk 10), TP2 88 (reward 12) → 1.2
    v = _short(tps=(95.0, 88.0, 80.0))
    assert v["reason"] == "LOW_RR" and v["rr"] == pytest.approx(1.2)


def test_short_fabricated_stored_rr_is_invalid_rr():
    assert _short(rr=6.0)["reason"] == "INVALID_RR"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_short_bad_stop_is_invalid_geometry(bad):
    assert _short(sl=bad)["reason"] == "INVALID_GEOMETRY"


def test_unknown_direction_is_invalid_geometry():
    assert rp.validate_geometry_and_rr("SIDEWAYS", 100, 90, (110, 120))["reason"] == "INVALID_GEOMETRY"


# ── The new reasons are registered ────────────────────────────────────────────

def test_new_rejection_reasons_are_declared():
    assert "INVALID_GEOMETRY" in rp.REJECTION_REASONS
    assert "INVALID_RR" in rp.REJECTION_REASONS
    assert "LOW_RR" in rp.REJECTION_REASONS
