"""
v53 strength calibration (rec_policy.apply_tier_calibration).

The v52 postmortem, split by strength band, showed the Confirmed tier (>= 69)
anti-predictive versus the Strong tier (51-69): a higher published number made
outcomes worse. The band diff pinned two over-promoted traits — chased entries
and wide 1H/2H splits. v53 caps both below the Confirmed floor (68), demoting
them into Strong, WITHOUT cutting volume. These tests pin that behaviour, and
that screen_candidate applies it to the published strength (confidence_score).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import rec_policy as rp                                             # noqa: E402


# ── the tier edge and cap are the analytics boundary ─────────────────────────

def test_cap_sits_just_below_the_confirmed_floor():
    # Confirmed is [69, inf); the cap lands a demoted setup at the top of Strong.
    assert rp.CONFIRMED_TIER_FLOOR == 69.0
    assert rp.TIER_DEMOTE_CAP == 68.0


# ── chase cap ─────────────────────────────────────────────────────────────────

def test_chase_caps_a_confirmed_setup_into_strong():
    out = rp.apply_tier_calibration(90, h1_strength=88, h2_strength=90, chased=True)
    assert out["strength"] == 68.0            # 90 (Confirmed) -> 68 (top of Strong)
    assert "chase_capped_to_strong" in out["notes"]


def test_chase_does_not_touch_a_setup_already_in_strong():
    # A chased entry already below the Confirmed floor is left alone — the cap
    # only fires when the strength is above it, so there is nothing to demote.
    out = rp.apply_tier_calibration(60, h1_strength=59, h2_strength=60, chased=True)
    assert out["strength"] == 60.0
    assert out["notes"] == []


def test_no_flags_leaves_a_confirmed_setup_confirmed():
    out = rp.apply_tier_calibration(90, h1_strength=88, h2_strength=90, chased=False)
    assert out["strength"] == 90.0
    assert out["notes"] == []


# ── timeframe-split dock ─────────────────────────────────────────────────────

def test_tf_split_caps_and_docks_proportionally():
    # spread = |60 - 90| = 30. Cap to 68, then dock (30 - 20) * 0.5 = 5 -> 63.
    out = rp.apply_tier_calibration(90, h1_strength=60, h2_strength=90, chased=False)
    assert out["strength"] == 63.0
    assert "tf_split_docked" in out["notes"]


def test_tf_split_at_exactly_the_gap_only_caps():
    # spread = 20 (the threshold): caps to 68, proportional dock is zero.
    out = rp.apply_tier_calibration(95, h1_strength=75, h2_strength=95, chased=False)
    assert out["strength"] == 68.0
    assert "tf_split_docked" in out["notes"]


def test_tf_split_below_the_gap_does_nothing():
    out = rp.apply_tier_calibration(90, h1_strength=80, h2_strength=90, chased=False)
    assert out["strength"] == 90.0
    assert out["notes"] == []


def test_chase_and_split_compound_but_never_below_the_floor():
    # Both fire; a very wide split would dock past the publication floor, but a
    # would-publish trade is never demoted below MIN_ADJUSTED_STRENGTH (51).
    out = rp.apply_tier_calibration(95, h1_strength=10, h2_strength=95, chased=True)
    # spread 85 -> raw dock (85-20)*0.5 = 32.5 from 68 -> 35.5, floored at 51.
    assert out["strength"] == float(rp.MIN_ADJUSTED_STRENGTH)
    assert set(out["notes"]) == {"chase_capped_to_strong", "tf_split_docked"}


# ── never raises, never rescues ──────────────────────────────────────────────

def test_calibration_only_ever_lowers():
    for s in (52, 60, 68, 69, 80, 100):
        out = rp.apply_tier_calibration(s, h1_strength=s, h2_strength=s, chased=True)
        assert out["strength"] <= float(s)


def test_a_sub_floor_signal_is_not_rescued_upward():
    # Below the floor already: calibration must not raise it to the floor (it
    # will be rejected by the min-strength gate; v53 never invents conviction).
    out = rp.apply_tier_calibration(40, h1_strength=10, h2_strength=40, chased=True)
    assert out["strength"] <= 40.0


def test_missing_timeframe_strengths_skip_the_split_dock():
    out = rp.apply_tier_calibration(90, h1_strength=None, h2_strength=None, chased=False)
    assert out["strength"] == 90.0
    assert out["notes"] == []


# ── chase detection matches the postmortem's snapshot predicate ──────────────

def test_candidate_is_chased_reads_structure_factors():
    assert rp.candidate_is_chased({"structure_factors": [{"factor": "range_chase",
                                                           "points": -8}]}) is True
    assert rp.candidate_is_chased({"structure_factors": [{"factor": "bos_aligned"}]}) is False
    assert rp.candidate_is_chased({"structure_factors": []}) is False
    assert rp.candidate_is_chased({}) is False              # field absent -> not chased
    assert rp.candidate_is_chased(None) is False


# ── screen_candidate applies it to the published strength ─────────────────────

def _long_leg(strength, *, chased=False):
    """A minimal 1H/2H reading that clears every gate, LONG, R/R 3.0."""
    sig = {"entry": 100.0, "sl": 98.0, "tp_targets": [103.0, 106.0],
           "rr_ratio": 3.0, "current_price": 100.0}
    if chased:
        sig["structure_factors"] = [{"factor": "range_chase", "points": -8}]
    else:
        sig["structure_factors"] = [{"factor": "bos_aligned", "points": 5}]
    return {"direction": "LONG", "strength": strength, "tradeable": True,
            "sig": sig, "signal_price": 100.0, "live_price": 100.0,
            "current_price": 100.0}


def _neutral_influence():
    # scale 0 -> BTC leaves strength untouched, so the calibration is what moves it.
    return rp.btc_influence("NEUTRAL", 0)


def test_screen_candidate_publishes_confirmed_when_clean():
    h2 = _long_leg(90)
    h1 = _long_leg(88)
    out = rp.screen_candidate(h1, h2, {}, corr_factor=1.0, influence=_neutral_influence())
    assert out["ok"] and out["strength"] == 90.0
    assert out["calibration_notes"] == []


def test_screen_candidate_demotes_a_chased_confirmed_setup():
    h2 = _long_leg(90, chased=True)
    h1 = _long_leg(88)
    out = rp.screen_candidate(h1, h2, {}, corr_factor=1.0, influence=_neutral_influence())
    assert out["ok"]
    assert out["strength"] == 68.0            # capped out of Confirmed
    assert out["strength"] < rp.CONFIRMED_TIER_FLOOR
    assert "chase_capped_to_strong" in out["calibration_notes"]


def test_screen_candidate_demotes_a_split_confirmed_setup():
    h2 = _long_leg(90)
    h1 = _long_leg(60)                          # spread 30
    out = rp.screen_candidate(h1, h2, {}, corr_factor=1.0, influence=_neutral_influence())
    assert out["ok"] and out["strength"] == 63.0
    assert "tf_split_docked" in out["calibration_notes"]
