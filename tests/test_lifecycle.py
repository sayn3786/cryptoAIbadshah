"""
One vocabulary for "how old is this pattern, and does it still count?".

Every detector had its own answer and none said it out loud. CHoCH faded over 10
candles, the liquidity grab over 5 — both as bare divisions buried inside
`signals.py`, invisible to the UI and to anything else. Flags carried a status
but no weight. RSI divergence had no age term at all.

So the scorer knew how stale a pattern was and nothing else did, and adding a
detector meant inventing another convention.

The rule that constrains this module: **unifying the vocabulary must not change
a single score.** CHoCH and the liquidity grab fade from the moment they happen;
a called turn holds full weight inside its window then fades. Those are
different curves, and collapsing them into one would silently double the weight
of a 5-candle-old CHoCH — a strategy change nobody asked for.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import lifecycle as L                                              # noqa: E402
import patterns                                                    # noqa: E402


# ── The curves signals.py has always used must survive untouched ───────────

@pytest.mark.parametrize("age", range(0, 15))
def test_choch_decay_is_bit_identical_to_the_old_formula(age):
    old = max(0, 1 - age / 10)
    new = (L.classify(age, "choch") or {}).get("freshness", 0.0)
    assert abs(old - new) < 1e-9


@pytest.mark.parametrize("age", range(0, 10))
def test_liquidity_grab_decay_is_bit_identical(age):
    old = max(0, 1 - age / 5)
    new = (L.classify(age, "liquidity_grab") or {}).get("freshness", 0.0)
    assert abs(old - new) < 1e-9


def test_the_two_curves_are_genuinely_different():
    # If they ever collapse into one, the assertion above would still pass while
    # the strategy quietly changed. A 5-candle-old CHoCH is worth half; a
    # 5-candle-old divergence is worth all of it.
    assert L.classify(5, "choch")["freshness"] == 0.5
    assert L.classify(5, "rsi_divergence")["freshness"] == 1.0


# ── The lifecycle itself ───────────────────────────────────────────────────

def test_forming_has_no_freshness_to_lose():
    r = L.classify(None, "rsi_divergence", forming=True)
    assert r["status"] == "forming" and r["freshness"] == 1.0


@pytest.mark.parametrize("age", [0, 6, 12])
def test_inside_the_window_is_confirmed_at_full_weight(age):
    r = L.classify(age, "rsi_divergence")
    assert r["status"] == "confirmed" and r["freshness"] == 1.0


def test_past_the_window_fades_rather_than_cliffs():
    seen = [L.classify(a, "rsi_divergence") for a in (13, 14, 15)]
    assert [r["status"] for r in seen] == ["expired"] * 3
    fresh = [r["freshness"] for r in seen]
    assert fresh == sorted(fresh, reverse=True), "must fade monotonically"
    assert all(0 < f < 1 for f in fresh)


def test_far_enough_past_it_is_dropped_entirely():
    assert L.classify(16, "rsi_divergence") is None


def test_the_grace_window_is_the_one_flags_already_use():
    # Not a second idea of "recently failed".
    assert L.GRACE_BARS == patterns.FAILURE_SHOW_BARS


def test_every_kind_declares_both_a_window_and_a_curve():
    for kind in L.FRESH_BARS:
        assert kind in L.CURVE, f"{kind} has a window but no curve"
        assert L.CURVE[kind] in ("linear", "window")


def test_an_unknown_detector_degrades_instead_of_crashing():
    r = L.classify(3, "some_detector_added_next_year")
    assert r["status"] == "confirmed"
    assert L.window("some_detector_added_next_year") > 0


def test_a_missing_age_is_treated_as_live_not_stale():
    # Assuming stale would silently mute a pattern that is actually current.
    r = L.classify(None, "choch")
    assert r["status"] == "confirmed" and r["freshness"] == 1.0


def test_a_negative_age_is_clamped():
    assert L.classify(-5, "choch")["age_candles"] == 0


# ── annotate ───────────────────────────────────────────────────────────────

def test_annotate_adds_the_fields_without_losing_the_payload():
    out = L.annotate({"signal": "bullish", "candles_ago": 2, "level": 9.5}, "choch")
    assert out["signal"] == "bullish" and out["level"] == 9.5
    assert out["status"] == "confirmed" and out["age_candles"] == 2


def test_annotate_does_not_mutate_its_input():
    src = {"signal": "bullish", "candles_ago": 2}
    L.annotate(src, "choch")
    assert "status" not in src, "the caller's dict must not be edited underneath it"


def test_annotate_returns_none_when_the_pattern_has_aged_out():
    assert L.annotate({"signal": "bullish", "candles_ago": 99}, "choch") is None


def test_annotate_passes_empty_payloads_straight_through():
    assert L.annotate(None, "choch") is None
    assert L.annotate({}, "choch") == {}


# ── decay ──────────────────────────────────────────────────────────────────

def test_decay_weights_points():
    assert L.decay(20, 1.0) == 20
    assert L.decay(20, 0.5) == 10


def test_decay_never_rounds_a_live_signal_away_to_nothing():
    # 2 points at 0.2 floors to 0, silently dropping something that still counts.
    assert L.decay(2, 0.2) == 1
    assert L.decay(-2, 0.2) == -1


def test_only_a_dead_signal_reaches_zero():
    assert L.decay(20, 0.0) == 0


def test_decay_treats_a_missing_freshness_as_full():
    assert L.decay(20, None) == 20
