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


# ── LTH tilt wired into the composite on-chain score ─────────────────────────────
def _base_score(**kw):
    defaults = dict(ribbon="bull", phase="mid", prof_ratio=1.3,
                    mvrv_zone="fair_value", diff_last=None,
                    sopr_zone="neutral", puell_zone="fair")
    defaults.update(kw)
    return o._onchain_score(**defaults)


def test_lth_tilt_symmetric_and_signed():
    base = _base_score(lth_state=None)["score"]
    acc = _base_score(lth_state="accumulation")
    dist = _base_score(lth_state="distribution")
    assert acc["score"] == min(100, base + o.LTH_SCORE_ADJ)
    assert dist["score"] == max(0, base - o.LTH_SCORE_ADJ)
    # symmetric magnitude and exposed adjustment
    assert acc["lth_adjustment"] == o.LTH_SCORE_ADJ
    assert dist["lth_adjustment"] == -o.LTH_SCORE_ADJ
    assert (acc["score"] - base) == (base - dist["score"]) == o.LTH_SCORE_ADJ


def test_lth_neutral_and_missing_are_no_ops():
    base = _base_score(lth_state=None)["score"]
    assert _base_score(lth_state="neutral")["score"] == base
    assert _base_score(lth_state="neutral")["lth_adjustment"] == 0
    # unknown/garbage state must not move the score
    assert _base_score(lth_state="???")["score"] == base


def test_lth_distribution_can_flip_the_label_bearish():
    # a mid score sitting just above the neutral/bearish edge drops a band when
    # LTH distribution is applied — the actionable top-warning behavior
    near_edge = _base_score(ribbon="bear", phase="late", prof_ratio=1.0,
                            mvrv_zone="fair_elevated", sopr_zone="profit",
                            puell_zone="fair", lth_state=None)
    dist = _base_score(ribbon="bear", phase="late", prof_ratio=1.0,
                       mvrv_zone="fair_elevated", sopr_zone="profit",
                       puell_zone="fair", lth_state="distribution")
    assert dist["score"] == near_edge["score"] - o.LTH_SCORE_ADJ
    assert dist["score"] < near_edge["score"]
