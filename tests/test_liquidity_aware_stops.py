"""
Liquidity-aware stop placement.

A stop sitting just short of a liquidity pool is in the worst possible place:
price runs the pool, takes the stop, then reverses — stopped out by the exact
move the trade was positioned for.

These tests protect three properties that make the change safe to run on real
money: it never TIGHTENS a stop, it never breaches the risk cap, and when it
cannot clear the pool it says so instead of quietly leaving the stop in the
sweep path.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import (                                                 # noqa: E402
    SL_POOL_CLEAR_ATR, SL_POOL_DANGER_ATR, SL_POOL_MIN_TOUCHES,
    clear_stop_of_liquidity,
)

# The reported live BTC 2H SHORT: stop landed 19.6 points under an 8-touch pool.
ENTRY = 64278.44601311
OLD_SL = 64922.04711738
SL_DIST = OLD_SL - ENTRY
ATR = 64266.4 * 0.009                       # the app's own vol_regime.atr_pct
POOLS = [{"price": 64941.625, "touches": 8},
         {"price": 64377.12857143, "touches": 7},
         {"price": 64368.425, "touches": 4},
         {"price": 65706.36, "touches": 5},
         {"price": 63782.675, "touches": 8},
         {"price": 62593.66666667, "touches": 3}]


_UNSET = object()      # so an explicit None can be tested distinctly


def _call(pools=_UNSET, *, entry=ENTRY, sl_dist=SL_DIST, is_long=False,
          atr=ATR, max_sl_abs=None):
    a = {"liquidity_pools": POOLS if pools is _UNSET else pools}
    return clear_stop_of_liquidity(
        a, entry=entry, sl_dist=sl_dist, is_long=is_long, atr=atr,
        max_sl_abs=max_sl_abs if max_sl_abs is not None else entry * 0.02)


# ── The reported case ───────────────────────────────────────────────────────

def test_the_live_btc_short_stop_is_moved_clear_of_the_pool():
    r = _call()
    assert r["moved"] is True
    assert r["pool_price"] == 64941.625
    assert r["touches"] == 8
    new_sl = ENTRY + r["sl_dist"]
    assert new_sl > 64941.625, "the stop must end up ABOVE the pool"
    assert new_sl > OLD_SL, "and further out than the original"


def test_the_moved_stop_leaves_real_clearance():
    r = _call()
    new_sl = ENTRY + r["sl_dist"]
    clearance = new_sl - r["pool_price"]
    assert clearance >= min(ATR * SL_POOL_CLEAR_ATR, ENTRY * 0.0015) * 0.99


def test_a_moved_stop_is_explained():
    r = _call()
    assert "liquidity pool" in r["note"]
    assert "64,941" in r["note"], "the note must name the level"
    assert r["blocked"] is False


# ── Never tightens ─────────────────────────────────────────────────────────

def test_never_tightens_a_stop():
    # Sweep a wide range of starting stops; the result may only ever widen.
    for mult in (0.2, 0.5, 0.8, 1.0, 1.5, 3.0):
        d = SL_DIST * mult
        r = _call(sl_dist=d, max_sl_abs=ENTRY * 0.10)
        assert r["sl_dist"] >= d, f"tightened at mult={mult}"


def test_a_stop_already_past_the_pool_is_left_alone():
    # Start beyond the pool: nothing to do.
    far = (64941.625 - ENTRY) + ATR * 1.0
    r = _call(sl_dist=far, max_sl_abs=ENTRY * 0.10)
    assert r["moved"] is False
    assert r["sl_dist"] == far


# ── Respects the risk cap ──────────────────────────────────────────────────

def test_blocked_when_clearing_would_breach_the_cap():
    r = _call(max_sl_abs=SL_DIST)          # no room at all
    assert r["moved"] is False
    assert r["blocked"] is True
    assert r["sl_dist"] == SL_DIST, "the stop must be left exactly as it was"
    assert "reduce size or wait" in r["note"]


def test_blocked_note_names_the_pool_so_it_is_actionable():
    r = _call(max_sl_abs=SL_DIST)
    assert "8-touch" in r["note"] and "64,941" in r["note"]


def test_never_exceeds_the_cap_even_when_moving():
    cap = SL_DIST * 1.25
    r = _call(max_sl_abs=cap)
    assert r["sl_dist"] <= cap


# ── Only the threatening side ──────────────────────────────────────────────

def test_pools_below_a_short_are_irrelevant():
    # A SHORT's stop is ABOVE entry; pools below it cannot take it out.
    below_only = [{"price": ENTRY - 50.0, "touches": 9},
                  {"price": 63782.675, "touches": 8}]
    r = _call(below_only)
    assert r["moved"] is False and r["pool_price"] is None


def test_long_mirror_moves_the_stop_down_past_a_pool():
    entry = 100.0
    atr = 2.0
    sl_dist = 5.0                              # stop at 95
    pools = [{"price": 94.8, "touches": 5}]    # just BELOW the stop
    r = clear_stop_of_liquidity({"liquidity_pools": pools}, entry=entry,
                                sl_dist=sl_dist, is_long=True, atr=atr,
                                max_sl_abs=entry * 0.20)
    assert r["moved"] is True
    new_sl = entry - r["sl_dist"]
    assert new_sl < 94.8, "a LONG stop must end up BELOW the pool"


def test_pools_above_a_long_are_irrelevant():
    r = clear_stop_of_liquidity(
        {"liquidity_pools": [{"price": 105.0, "touches": 9}]},
        entry=100.0, sl_dist=5.0, is_long=True, atr=2.0, max_sl_abs=20.0)
    assert r["moved"] is False


# ── Distance window ────────────────────────────────────────────────────────

def test_a_pool_far_beyond_the_stop_does_not_drag_it_out():
    # Reaching a distant pool would inflate risk for no reason: it is not what
    # takes the stop out.
    entry, atr, sl_dist = 100.0, 2.0, 5.0             # stop at 95
    far = 95.0 - atr * (SL_POOL_DANGER_ATR + 0.5)     # well outside the window
    r = clear_stop_of_liquidity({"liquidity_pools": [{"price": far, "touches": 9}]},
                                entry=entry, sl_dist=sl_dist, is_long=True,
                                atr=atr, max_sl_abs=entry * 0.5)
    assert r["moved"] is False and r["pool_price"] is None


def test_a_pool_at_the_edge_of_the_window_still_counts():
    entry, atr, sl_dist = 100.0, 2.0, 5.0
    edge = 95.0 - atr * (SL_POOL_DANGER_ATR * 0.9)
    r = clear_stop_of_liquidity({"liquidity_pools": [{"price": edge, "touches": 9}]},
                                entry=entry, sl_dist=sl_dist, is_long=True,
                                atr=atr, max_sl_abs=entry * 0.5)
    assert r["moved"] is True


def test_the_pool_forcing_the_largest_move_is_chosen():
    # Clearing the furthest in-window pool clears every nearer one too.
    entry, atr, sl_dist = 100.0, 2.0, 5.0
    pools = [{"price": 94.9, "touches": 5}, {"price": 94.6, "touches": 5}]
    r = clear_stop_of_liquidity({"liquidity_pools": pools}, entry=entry,
                                sl_dist=sl_dist, is_long=True, atr=atr,
                                max_sl_abs=entry * 0.5)
    assert r["pool_price"] == 94.6
    assert entry - r["sl_dist"] < 94.6


# ── Conservatism about which pools may move a stop ─────────────────────────

def test_thinly_touched_pools_never_move_a_stop():
    # Two touches is enough to dock conviction, but widening real risk demands
    # a better-defended level.
    thin = [{"price": 64941.625, "touches": SL_POOL_MIN_TOUCHES - 1}]
    r = _call(thin)
    assert r["moved"] is False and r["pool_price"] is None


def test_min_touches_for_moving_is_stricter_than_for_scoring():
    from signals import STOP_RUN_MIN_TOUCHES
    assert SL_POOL_MIN_TOUCHES > STOP_RUN_MIN_TOUCHES


# ── Degenerate input ───────────────────────────────────────────────────────

@pytest.mark.parametrize("pools", [
    [], None,
    [{"price": None, "touches": 5}],
    [{"price": "abc", "touches": 5}],
    [{"price": 64941.625}],                 # no touches
    [{"price": 0, "touches": 9}],
    [{"price": -5, "touches": 9}],
    [{}],
])
def test_malformed_pools_leave_the_stop_untouched(pools):
    r = _call(pools)
    assert r["sl_dist"] == SL_DIST
    assert r["moved"] is False and r["blocked"] is False


@pytest.mark.parametrize("kw", [
    {"atr": 0.0}, {"atr": -1.0}, {"entry": 0.0}, {"sl_dist": 0.0},
    {"sl_dist": -3.0},
])
def test_unusable_inputs_are_a_no_op(kw):
    r = _call(**kw)
    assert r["moved"] is False and r["blocked"] is False


def test_missing_pools_key_entirely():
    r = clear_stop_of_liquidity({}, entry=ENTRY, sl_dist=SL_DIST, is_long=False,
                                atr=ATR, max_sl_abs=ENTRY * 0.02)
    assert r["sl_dist"] == SL_DIST and r["moved"] is False


# ── Integration ────────────────────────────────────────────────────────────

def test_generate_signal_exposes_the_stop_liquidity_verdict():
    from signals import generate_signal
    from test_flag_pattern_correctness import _make_candles

    candles = _make_candles(60, up=True)
    sig = generate_signal({"symbol": "BTC", "timeframe": "2H", "candles": candles})
    assert "stop_liquidity" in sig, "the card needs to know whether the stop was moved"


def test_a_neutral_signal_has_no_stop_verdict():
    from signals import generate_signal
    sig = generate_signal({"symbol": "BTC", "timeframe": "2H", "candles": []})
    assert sig["stop_liquidity"] is None, "no stop to place on a NEUTRAL read"


def test_stop_stays_on_the_correct_side_of_entry_after_a_move():
    """The invariant that matters most: a widened stop is still a stop."""
    from signals import generate_signal
    from test_flag_pattern_correctness import _make_candles

    for up in (True, False):
        candles = _make_candles(60, up=up)
        px = candles[-1]["close"]
        sig = generate_signal({
            "symbol": "BTC", "timeframe": "2H", "candles": candles,
            # pools hugging both sides, to provoke a move whichever way it goes
            "liquidity_pools": [{"price": px * 0.985, "touches": 8},
                                {"price": px * 1.015, "touches": 8}],
        })
        if sig["direction"] == "LONG":
            assert sig["sl"] < sig["entry"]
        elif sig["direction"] == "SHORT":
            assert sig["sl"] > sig["entry"]
