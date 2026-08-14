"""
Liquidation / squeeze "max pain" direction from realized liquidations + OI.

Not the Coinglass resting-leverage map (paid tier) — a narrower read from data
we already have: the OI squeeze quadrant says who is crowding in now (weighted
strongest), and the realized long/short liquidation skew corroborates who is
already over-leveraged. Direction = the side price would move to inflict the
most forced-position pain.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                  # noqa: E402

_bias = appmod._liquidation_bias


def test_short_squeeze_fuel_plus_short_liqs_points_upside_strong():
    b = _bias({"longs_liquidated": 2_360_000_000, "shorts_liquidated": 4_850_000_000},
              {"squeeze": "short_squeeze_fuel"})
    assert b["direction"] == "upside"
    assert b["strength"] == "strong"          # squeeze (2) + skew (1) = 3 vs 0


def test_long_squeeze_risk_points_downside():
    b = _bias({"longs_liquidated": 5_000_000, "shorts_liquidated": 1_000_000},
              {"squeeze": "long_squeeze_risk"})
    assert b["direction"] == "downside"


def test_liquidation_skew_alone_is_a_lean():
    # No OI squeeze — only the realized skew. Shorts hit harder → upside lean.
    b = _bias({"longs_liquidated": 1_000_000, "shorts_liquidated": 4_000_000}, {})
    assert b["direction"] == "upside"
    assert b["strength"] == "lean"


def test_balanced_when_nothing_skews():
    b = _bias({"longs_liquidated": 1_000_000, "shorts_liquidated": 1_000_000}, {})
    assert b["direction"] == "balanced"
    assert b["strength"] is None


def test_squeeze_and_skew_can_disagree_squeeze_wins():
    # Longs got liquidated more recently (skew → downside 1), but OI shows shorts
    # now piling in (short-squeeze fuel → upside 2). Forward read wins.
    b = _bias({"longs_liquidated": 4_000_000, "shorts_liquidated": 1_000_000},
              {"squeeze": "short_squeeze_fuel"})
    assert b["direction"] == "upside"
    assert b["strength"] == "lean"            # up 2 vs down 1 → diff 1


def test_missing_liquidation_values_do_not_crash():
    b = _bias({}, {"squeeze": "short_squeeze_fuel"})
    assert b["direction"] == "upside"


# ── v46: the bias as a strength nudge ────────────────────────────────────────
# The bias above was reporting-only until v46, which folds it into strength as a
# small signed confluence delta via signals.liquidation_squeeze_delta.

from signals import liquidation_squeeze_delta                         # noqa: E402


def test_a_long_aligned_with_upside_pain_is_confirmed():
    assert liquidation_squeeze_delta(
        "LONG", {"direction": "upside", "strength": "strong"}) == 4
    assert liquidation_squeeze_delta(
        "LONG", {"direction": "upside", "strength": "lean"}) == 2


def test_a_long_against_downside_pain_is_docked():
    assert liquidation_squeeze_delta(
        "LONG", {"direction": "downside", "strength": "strong"}) == -4


def test_a_short_aligned_with_downside_pain_is_confirmed():
    assert liquidation_squeeze_delta(
        "SHORT", {"direction": "downside", "strength": "lean"}) == 2
    assert liquidation_squeeze_delta(
        "SHORT", {"direction": "upside", "strength": "lean"}) == -2


def test_balanced_absent_or_directionless_is_no_nudge():
    assert liquidation_squeeze_delta("LONG", {"direction": "balanced"}) == 0
    assert liquidation_squeeze_delta("LONG", {}) == 0
    assert liquidation_squeeze_delta("LONG", None) == 0
    assert liquidation_squeeze_delta(
        "NEUTRAL", {"direction": "upside", "strength": "strong"}) == 0


def test_the_nudge_is_small_enough_to_stay_advisory():
    # Strength is a 0-100 scale; even the strong case is 4 points — the same
    # order as the market-structure nudge — so it corroborates or docks but can
    # never manufacture a publishable signal on its own.
    assert abs(liquidation_squeeze_delta(
        "LONG", {"direction": "upside", "strength": "strong"})) <= 4
