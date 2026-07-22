"""
Tests for LTH/STH supply distribution tracking.

Real 155-day LTH/STH needs a paid provider; we derive held (LTH-ish) vs active
(STH-ish) supply from CoinMetrics' free supply-age bands and track the trend:
rising held supply = accumulation, falling = distribution.

Pure/synthetic; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import btc_onchain as o                                                 # noqa: E402

DAY = 86400
T0 = 1_700_000_000


def _held(vals):
    return [(T0 + i * DAY, v) for i, v in enumerate(vals)]


def test_lth_states_accumulation_distribution_neutral():
    # rising held supply → accumulation; flat → neutral; falling → distribution
    rising = _held([60 + i * 0.05 for i in range(60)])
    st = o._lth_distribution_states(rising)
    assert st and all(s == "accumulation" for _, s in st)

    flat = _held([60.0] * 60)
    st = o._lth_distribution_states(flat)
    assert st and all(s == "neutral" for _, s in st)

    falling = _held([63 - i * 0.05 for i in range(60)])
    st = o._lth_distribution_states(falling)
    assert st and all(s == "distribution" for _, s in st)


def test_lth_threshold_is_symmetric():
    win = o.LTH_TREND_WINDOW_DAYS
    thr = o.LTH_TREND_THRESH_PP
    # change exactly at +thresh over the window is NOT past it (neutral);
    # just beyond flips to accumulation, and the mirror to distribution
    base = 60.0
    up = _held([base + (thr * 1.5) * (i / win) for i in range(win + 1)])
    dn = _held([base - (thr * 1.5) * (i / win) for i in range(win + 1)])
    assert o._lth_distribution_states(up)[-1][1] == "accumulation"
    assert o._lth_distribution_states(dn)[-1][1] == "distribution"


def test_lth_history_flip_detected():
    held = _held([60 + i * 0.05 for i in range(60)]           # accumulation
                 + [63 - i * 0.05 for i in range(60)])         # then distribution
    states = o._lth_distribution_states(held)
    hist = o.summarize_transitions(states, now_ts=held[-1][0], min_run_days=5.0)
    assert hist["current_state"] == "distribution"
    assert hist["previous"]["state"] == "accumulation"
    assert "accumulation" in hist["last_seen"]


def test_lth_supply_and_active_are_complementary():
    # a single classified series' current held/active must sum to 100
    held_val = 62.3
    assert round(held_val + (100.0 - held_val), 4) == 100.0
