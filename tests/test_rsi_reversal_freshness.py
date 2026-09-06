"""
v51: the RSI swing-reversal brake fades with the marker's AGE in closed bars.

Before v51 the newest marker docked a trade at full weight even when it was
~30 candles old. Now the penalty is full only while the marker is fresh, decays
across a documented window, and is zero once expired. Age is a bar count, never a
wall clock, so the brake stays pure and the backtest reproduces it. LONG and
SHORT behave identically. The maximum penalty (−6) is preserved from v50.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signals                                                       # noqa: E402

FRESH = signals.REVERSAL_FRESH_BARS
EXPIRE = signals.REVERSAL_EXPIRE_BARS
MAXP = signals.REVERSAL_MAX_PENALTY


def _m(kind, bars_ago, ts=100):
    return [{"timestamp": ts, "kind": kind, "rsi": 75, "price": 1.0, "bars_ago": bars_ago}]


# ── The decay curve ──────────────────────────────────────────────────────────

def test_full_penalty_when_fresh():
    assert signals._reversal_freshness(0) == 1.0
    assert signals._reversal_freshness(FRESH) == 1.0
    assert signals.rsi_reversal_delta("LONG", _m("overbought_top", FRESH)) == -MAXP


def test_zero_once_expired():
    assert signals._reversal_freshness(EXPIRE) == 0.0
    assert signals._reversal_freshness(EXPIRE + 5) == 0.0
    assert signals.rsi_reversal_delta("LONG", _m("overbought_top", EXPIRE)) == 0
    assert signals.rsi_reversal_delta("LONG", _m("overbought_top", EXPIRE + 10)) == 0


def test_decays_monotonically_between_fresh_and_expiry():
    prev = 1.1
    for b in range(FRESH, EXPIRE + 1):
        f = signals._reversal_freshness(b)
        assert 0.0 <= f <= 1.0
        assert f <= prev + 1e-9                 # never increases with age
        prev = f
    # Halfway through the fade window the penalty is materially reduced but not 0.
    mid = (FRESH + EXPIRE) // 2
    d = signals.rsi_reversal_delta("SHORT", _m("oversold_bottom", mid))
    assert -MAXP < d < 0


def test_a_still_fresh_marker_never_rounds_to_zero_before_expiry():
    # One bar before expiry the fade is small, but the brake must still bite.
    d = signals.rsi_reversal_delta("LONG", _m("overbought_top", EXPIRE - 1))
    assert d == -1


# ── Missing / malformed age ──────────────────────────────────────────────────

def test_missing_bars_ago_applies_no_penalty():
    # Unknown age is NOT assumed fresh — that was the v50 bug.
    assert signals.rsi_reversal_delta("LONG", [{"timestamp": 1, "kind": "overbought_top"}]) == 0


@pytest.mark.parametrize("bad", [None, "x", float("nan"), -3])
def test_unreadable_age_reads_as_no_penalty(bad):
    assert signals._reversal_freshness(bad) == 0.0


# ── LONG / SHORT symmetry ────────────────────────────────────────────────────

@pytest.mark.parametrize("bars_ago,expected", [(FRESH, -MAXP), (EXPIRE, 0)])
def test_long_and_short_are_symmetric(bars_ago, expected):
    long_d = signals.rsi_reversal_delta("LONG", _m("overbought_top", bars_ago))
    short_d = signals.rsi_reversal_delta("SHORT", _m("oversold_bottom", bars_ago))
    assert long_d == short_d == expected


def test_agreeing_marker_never_penalised_regardless_of_freshness():
    assert signals.rsi_reversal_delta("LONG", _m("oversold_bottom", 0)) == 0
    assert signals.rsi_reversal_delta("SHORT", _m("overbought_top", 0)) == 0


# ── The window is documented and the max penalty is preserved ────────────────

def test_window_constants_are_sane_and_penalty_unchanged():
    assert 0 < FRESH < EXPIRE
    assert MAXP == 6                            # not tuned on the discovery sample
