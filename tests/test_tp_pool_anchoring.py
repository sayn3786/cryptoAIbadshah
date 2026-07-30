"""
Liquidity pools as take-profit anchors.

A pool ahead of the trade is where resting orders sit, so price is drawn to it.
TP snapping already used zones, trend-lines and swings but ignored pools, so the
ladder could target an ATR projection while a real wall of liquidity sat closer.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import (                                                 # noqa: E402
    TP_POOL_MIN_TOUCHES, _matching_pool, _snap_tp_to_structure, _tp_pool_levels,
)


def _pools(*specs):
    return {"liquidity_pools": [{"price": p, "touches": t} for p, t in specs]}


# ── Which pools are candidates ─────────────────────────────────────────────

def test_only_pools_ahead_of_a_long_are_candidates():
    a = _pools((103.5, 9), (101.2, 4), (97.0, 9))
    assert sorted(_tp_pool_levels(a, 100.0, is_long=True)) == [101.2, 103.5]


def test_only_pools_ahead_of_a_short_are_candidates():
    a = _pools((103.5, 9), (98.5, 4), (97.0, 9))
    assert sorted(_tp_pool_levels(a, 100.0, is_long=False)) == [97.0, 98.5]


def test_thin_pools_are_excluded():
    a = _pools((103.5, TP_POOL_MIN_TOUCHES - 1), (104.0, TP_POOL_MIN_TOUCHES))
    assert _tp_pool_levels(a, 100.0, is_long=True) == [104.0]


def test_tp_threshold_is_looser_than_the_stop_threshold():
    # Deliberate asymmetry: anchoring to a weak pool only takes profit early,
    # while ignoring a real pool leaves the TP where it may never fill.
    from signals import SL_POOL_MIN_TOUCHES
    assert TP_POOL_MIN_TOUCHES < SL_POOL_MIN_TOUCHES


@pytest.mark.parametrize("pools", [
    [], None,
    [{"price": None, "touches": 5}],
    [{"price": "abc", "touches": 5}],
    [{"price": 103.5}],                       # no touches
    [{"price": 0, "touches": 9}],
    [{"price": -5, "touches": 9}],
    [{}],
])
def test_malformed_pools_yield_no_candidates(pools):
    assert _tp_pool_levels({"liquidity_pools": pools}, 100.0, is_long=True) == []


def test_missing_key_and_bad_entry_are_safe():
    assert _tp_pool_levels({}, 100.0, is_long=True) == []
    assert _tp_pool_levels(_pools((103.5, 9)), 0.0, is_long=True) == []
    assert _tp_pool_levels(_pools((103.5, 9)), -1.0, is_long=True) == []


# ── A pool becoming the anchor ─────────────────────────────────────────────

def test_a_pool_can_become_the_tp2_wall():
    entry, sl = 100.0, 98.0                  # risk 2.0; 2H needs >= 1.4R
    a = _pools((103.5, 9), (101.2, 4), (97.0, 9))
    levels = _tp_pool_levels(a, entry, is_long=True)
    tps, wall, rm = _snap_tp_to_structure("LONG", entry, sl, "2H", levels, entry * 0.10)
    assert wall == 103.5
    assert rm >= 1.4
    assert _matching_pool(a, wall)["touches"] == 9


def test_the_tp_front_runs_the_pool_rather_than_sitting_in_it():
    # Filling just before the level beats queueing inside the fight over it.
    entry, sl = 100.0, 98.0
    a = _pools((103.5, 9))
    tps, wall, _ = _snap_tp_to_structure("LONG", entry, sl, "2H",
                                         _tp_pool_levels(a, entry, True), entry * 0.10)
    assert tps[1] < wall, "a LONG TP2 must fill below the pool"


def test_short_front_runs_upward():
    entry, sl = 100.0, 102.0
    a = _pools((96.0, 9))
    tps, wall, _ = _snap_tp_to_structure("SHORT", entry, sl, "2H",
                                         _tp_pool_levels(a, entry, False), entry * 0.10)
    assert tps[1] > wall, "a SHORT TP2 must fill above the pool"


def test_adding_pools_never_moves_a_tp_to_the_wrong_side_of_entry():
    for is_long, sl in ((True, 98.0), (False, 102.0)):
        d = "LONG" if is_long else "SHORT"
        a = _pools((103.5, 9), (101.2, 4), (96.0, 9), (98.5, 3))
        levels = _tp_pool_levels(a, 100.0, is_long)
        snap = _snap_tp_to_structure(d, 100.0, sl, "2H", levels, 100.0 * 0.10)
        if snap:
            for tp in snap[0]:
                assert (tp > 100.0) if is_long else (tp < 100.0), f"{d} tp={tp}"


# ── The live BTC case: TP3 lands on a real pool ────────────────────────────

def test_live_btc_short_tp3_lands_on_a_pool_instead_of_an_atr_projection():
    entry, sl = 64278.44601311, 65038.04
    a = _pools((64941.625, 8), (64377.12857143, 7), (64368.425, 4),
               (65706.36, 5), (63782.675, 8), (62593.66666667, 3))
    zones = [63055.8, 63431.1, 63806.4]          # what the engine already had

    before = _snap_tp_to_structure("SHORT", entry, sl, "2H", zones, entry * 0.05)
    after = _snap_tp_to_structure("SHORT", entry, sl, "2H",
                                  zones + _tp_pool_levels(a, entry, False),
                                  entry * 0.05)
    assert before and after
    assert after[0][2] == pytest.approx(62593.66666667), \
        "TP3 should sit on the 3-touch pool, not an ATR extension"
    assert after[0][2] != before[0][2], "the pool must have changed TP3"
    # TP1/TP2 unchanged here — an existing level was already nearer.
    assert after[0][:2] == before[0][:2]


# ── Anchor labelling ──────────────────────────────────────────────────────

def test_matching_pool_identifies_the_wall():
    a = _pools((103.5, 9))
    assert _matching_pool(a, 103.5)["touches"] == 9
    assert _matching_pool(a, 103.5 * 1.01) is None, "1% away is a different level"


def test_matching_pool_tolerance_is_relative_not_absolute():
    # Must behave the same on BTC and on a sub-cent alt.
    tiny = _pools((0.000012345, 5))
    assert _matching_pool(tiny, 0.000012345) is not None
    assert _matching_pool(tiny, 0.000020000) is None
    big = _pools((64941.625, 8))
    assert _matching_pool(big, 64941.625) is not None
    assert _matching_pool(big, 65706.36) is None


def test_matching_pool_prefers_the_closest_candidate():
    a = _pools((100.00, 4), (100.01, 9))
    assert _matching_pool(a, 100.0102)["touches"] == 9


def test_matching_pool_ignores_thin_and_malformed_entries():
    assert _matching_pool(_pools((103.5, TP_POOL_MIN_TOUCHES - 1)), 103.5) is None
    assert _matching_pool({"liquidity_pools": [{"price": "x", "touches": 9}]}, 103.5) is None
    assert _matching_pool({}, 103.5) is None


@pytest.mark.parametrize("wall", [0, None, -1.0])
def test_matching_pool_rejects_a_bad_wall(wall):
    assert _matching_pool(_pools((103.5, 9)), wall) is None


# ── Integration ───────────────────────────────────────────────────────────

def test_generate_signal_exposes_the_tp_anchor():
    from signals import generate_signal
    from test_flag_pattern_correctness import _make_candles

    candles = _make_candles(60, up=True)
    sig = generate_signal({"symbol": "BTC", "timeframe": "2H", "candles": candles})
    assert "tp_anchor" in sig, "the card needs to know what TP2 is trading to"


def test_tp_anchor_reports_its_kind_when_snapped():
    from signals import generate_signal
    from test_flag_pattern_correctness import _make_candles

    candles = _make_candles(60, up=True)
    px = candles[-1]["close"]
    sig = generate_signal({
        "symbol": "BTC", "timeframe": "2H", "candles": candles,
        "liquidity_pools": [{"price": px * 1.05, "touches": 9},
                            {"price": px * 0.95, "touches": 9}],
    })
    anchor = sig.get("tp_anchor")
    if anchor:                                   # only when a wall cleared the gate
        assert anchor["kind"] in ("liquidity_pool", "zone_or_line")
        assert anchor["wall"] > 0
        assert anchor["r_multiple"] > 0
        if anchor["kind"] == "liquidity_pool":
            assert anchor["touches"] >= TP_POOL_MIN_TOUCHES


def test_neutral_signal_has_no_tp_anchor():
    from signals import generate_signal
    assert generate_signal({"symbol": "BTC", "timeframe": "2H", "candles": []})["tp_anchor"] is None
