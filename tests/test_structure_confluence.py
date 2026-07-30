"""
Market-structure confluence: turning the three display-only panel reads
(pool distance, range position, BOS persistence) into a conviction adjustment.

The invariant these tests protect: structure moves STRENGTH, never DIRECTION.
Resting stops below a LONG are a reason to size down or wait — not a reason to
go short.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import (                                                 # noqa: E402
    BOS_DECAY_BARS, BOS_MAX_BONUS, BOS_OPPOSED_PENALTY, CHASE_MAX_PENALTY,
    CHASE_LOWER_PCT, CHASE_UPPER_PCT, STOP_RUN_ATR, STOP_RUN_MAX_PENALTY,
    STOP_RUN_MIN_TOUCHES, STRUCT_ADJ_CEILING, STRUCT_ADJ_FLOOR,
    structure_confluence,
)
from patterns import average_true_range, structure_range              # noqa: E402


def _candles(n=40, base=100.0, step=0.0, spread=1.0, last_close=None):
    """Flat-ish series with a controllable true range and final close."""
    out = []
    for i in range(n):
        mid = base + step * i
        out.append({"timestamp": 1_700_000_000_000 + i * 7_200_000,
                    "open": mid, "high": mid + spread, "low": mid - spread,
                    "close": mid})
    if last_close is not None:
        out[-1]["close"] = last_close
        out[-1]["high"] = max(out[-1]["high"], last_close)
        out[-1]["low"] = min(out[-1]["low"], last_close)
    return out


def _analysis(**over):
    a = {"candles": _candles()}
    a.update(over)
    return a


# ── Shared measurements ─────────────────────────────────────────────────────

def test_atr_is_measurable_and_matches_the_bar_range():
    # Flat series, high-low = 2*spread every bar, no gaps -> ATR == 2*spread.
    assert average_true_range(_candles(spread=1.5)) == pytest.approx(3.0)


def test_atr_degrades_safely_on_thin_data():
    assert average_true_range([]) == 0.0
    assert average_true_range(_candles(1)) == 0.0


def test_structure_range_reports_position_within_the_window():
    rng = structure_range(_candles(40, base=100, step=1.0))
    assert rng["high"] > rng["low"]
    assert 0.0 <= rng["position_pct"] <= 100.0
    assert rng["bars"] == 30, "must use the panel's 30-bar window"


def test_structure_range_handles_a_zero_width_range():
    flat = [{"timestamp": i, "open": 5, "high": 5, "low": 5, "close": 5} for i in range(10)]
    assert structure_range(flat)["position_pct"] is None


# ── Neutral / guard rails ───────────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["NEUTRAL", "", None, "long"])
def test_no_adjustment_without_a_directional_signal(direction):
    out = structure_confluence(_analysis(), direction)
    assert out["delta"] == 0 and out["factors"] == []


def test_no_adjustment_on_thin_data():
    assert structure_confluence({"candles": _candles(5)}, "LONG")["delta"] == 0
    assert structure_confluence({"candles": []}, "LONG")["delta"] == 0


def test_no_adjustment_when_nothing_structural_is_present():
    assert structure_confluence(_analysis(), "LONG")["delta"] == 0


# ── Stop-run risk ───────────────────────────────────────────────────────────

def _with_pool(direction, dist_atr, touches=5, **over):
    """Place a pool `dist_atr` ATR on the threatening side of price."""
    candles = _candles(spread=1.0)                 # ATR == 2.0
    price = candles[-1]["close"]
    atr = average_true_range(candles)
    offset = atr * dist_atr
    pool = price - offset if direction == "LONG" else price + offset
    key = "eql" if direction == "LONG" else "eqh"
    a = {"candles": candles,
         "equal_levels": {key: {"price": pool, "touches": touches}}}
    a.update(over)
    return a


def test_pool_just_below_a_long_is_penalized():
    out = structure_confluence(_with_pool("LONG", 0.05), "LONG")
    assert out["delta"] < 0
    f = [x for x in out["factors"] if x["factor"] == "stop_run_risk"]
    assert f and f[0]["points"] < 0
    # A sweep below price is a bearish-side risk for a LONG.
    assert out["bear_reasons"] and not out["bull_reasons"]


def test_pool_just_above_a_short_is_penalized():
    out = structure_confluence(_with_pool("SHORT", 0.05), "SHORT")
    assert out["delta"] < 0
    assert out["bull_reasons"] and not out["bear_reasons"]


def test_a_distant_pool_does_not_penalize():
    out = structure_confluence(_with_pool("LONG", STOP_RUN_ATR * 3), "LONG")
    assert out["delta"] == 0
    assert not [x for x in out["factors"] if x["factor"] == "stop_run_risk"]


def test_penalty_grows_as_the_pool_gets_closer():
    near = structure_confluence(_with_pool("LONG", 0.02), "LONG")["delta"]
    far = structure_confluence(_with_pool("LONG", STOP_RUN_ATR * 0.9), "LONG")["delta"]
    assert near < far <= 0, "a closer pool must cost more conviction"


def test_penalty_grows_with_touch_count():
    weak = structure_confluence(_with_pool("LONG", 0.05, touches=2), "LONG")["delta"]
    strong = structure_confluence(_with_pool("LONG", 0.05, touches=8), "LONG")["delta"]
    assert strong < weak <= 0, "a more-defended level holds more stops"


def test_a_barely_touched_level_is_ignored():
    out = structure_confluence(_with_pool("LONG", 0.05,
                                          touches=STOP_RUN_MIN_TOUCHES - 1), "LONG")
    assert out["delta"] == 0, "one touch is not a pool of resting stops"


def test_pool_on_the_far_side_of_price_is_not_a_stop_run():
    # An equal-low ABOVE current price cannot be where a long's stops sit.
    candles = _candles()
    a = {"candles": candles,
         "equal_levels": {"eql": {"price": candles[-1]["close"] + 1.0, "touches": 5}}}
    assert structure_confluence(a, "LONG")["delta"] == 0


def test_penalty_is_capped():
    out = structure_confluence(_with_pool("LONG", 0.0, touches=99), "LONG")
    stop_run = [x for x in out["factors"] if x["factor"] == "stop_run_risk"][0]
    assert abs(stop_run["points"]) <= STOP_RUN_MAX_PENALTY


# ── Chase ───────────────────────────────────────────────────────────────────

def _at_range_position(pct):
    """Series whose final close sits `pct`% up the 30-bar range."""
    candles = _candles(40, base=100.0, step=1.0)
    rng = structure_range(candles)
    target = rng["low"] + (rng["high"] - rng["low"]) * (pct / 100.0)
    candles[-1]["close"] = target
    candles[-1]["high"] = max(candles[-1]["high"], target)
    candles[-1]["low"] = min(candles[-1]["low"], target)
    return {"candles": candles}


def test_long_at_the_top_of_the_range_is_penalized():
    out = structure_confluence(_at_range_position(98), "LONG")
    assert out["delta"] < 0
    f = [x for x in out["factors"] if x["factor"] == "range_chase"]
    assert f and f[0]["points"] < 0
    assert out["bear_reasons"]


def test_short_at_the_bottom_of_the_range_is_penalized():
    out = structure_confluence(_at_range_position(2), "SHORT")
    assert out["delta"] < 0
    assert out["bull_reasons"]


def test_mid_range_entry_is_not_a_chase():
    assert structure_confluence(_at_range_position(50), "LONG")["delta"] == 0
    assert structure_confluence(_at_range_position(50), "SHORT")["delta"] == 0


def test_long_at_the_bottom_is_not_a_chase():
    # Buying the low of the range is the opposite of chasing.
    assert structure_confluence(_at_range_position(5), "LONG")["delta"] == 0


def test_short_at_the_top_is_not_a_chase():
    assert structure_confluence(_at_range_position(95), "SHORT")["delta"] == 0


def test_chase_penalty_scales_with_extension():
    edge = structure_confluence(_at_range_position(CHASE_UPPER_PCT + 1), "LONG")["delta"]
    extreme = structure_confluence(_at_range_position(100), "LONG")["delta"]
    assert extreme < edge < 0
    assert abs(extreme) <= CHASE_MAX_PENALTY


def test_chase_threshold_boundaries_are_respected():
    just_inside = structure_confluence(_at_range_position(CHASE_UPPER_PCT - 2), "LONG")
    assert just_inside["delta"] == 0
    just_outside = structure_confluence(_at_range_position(CHASE_UPPER_PCT + 2), "LONG")
    assert just_outside["delta"] < 0


# ── BOS persistence ─────────────────────────────────────────────────────────

def _with_bos(direction, count=2, held=True, bars_ago=2):
    return {"candles": _candles(),
            "bos_streak": {"direction": direction, "count": count, "held": held,
                           "last_level": 100.0, "bars_ago": bars_ago}}


def test_aligned_holding_bos_is_a_bonus():
    out = structure_confluence(_with_bos("bullish"), "LONG")
    assert out["delta"] > 0
    assert [x for x in out["factors"] if x["factor"] == "bos_aligned"]
    assert out["bull_reasons"]


def test_opposed_holding_bos_is_a_penalty():
    out = structure_confluence(_with_bos("bearish"), "LONG")
    assert out["delta"] < 0
    assert [x for x in out["factors"] if x["factor"] == "bos_opposed"]
    assert out["bear_reasons"]


def test_a_given_back_bos_is_worth_exactly_nothing():
    # Stale context, not a live read — the whole point of tracking `held`.
    for bos_dir in ("bullish", "bearish"):
        out = structure_confluence(_with_bos(bos_dir, held=False), "LONG")
        assert out["delta"] == 0
        given = [x for x in out["factors"] if x["factor"] == "bos_given_back"]
        assert given and given[0]["points"] == 0
        assert not out["bull_reasons"] and not out["bear_reasons"]


def test_bos_bonus_and_penalty_are_capped():
    assert structure_confluence(_with_bos("bullish", count=99), "LONG")["delta"] \
        <= BOS_MAX_BONUS
    assert structure_confluence(_with_bos("bearish", count=99), "LONG")["delta"] \
        >= -BOS_OPPOSED_PENALTY


def test_bos_with_zero_count_is_ignored():
    assert structure_confluence(_with_bos("bullish", count=0), "LONG")["delta"] == 0


# ── Combination and clamping ────────────────────────────────────────────────

def test_factors_combine():
    a = _with_pool("LONG", 0.05, touches=8)
    a["bos_streak"] = {"direction": "bearish", "count": 3, "held": True,
                       "last_level": 100.0, "bars_ago": 1}
    out = structure_confluence(a, "LONG")
    kinds = {f["factor"] for f in out["factors"]}
    assert {"stop_run_risk", "bos_opposed"} <= kinds
    assert out["delta"] < 0


def test_total_adjustment_is_clamped_both_ways():
    # Everything against a LONG at once.
    worst = _with_pool("LONG", 0.0, touches=99)
    rng = structure_range(worst["candles"])
    if rng["position_pct"] is not None:
        worst["candles"][-1]["close"] = rng["high"]
    worst["bos_streak"] = {"direction": "bearish", "count": 99, "held": True,
                           "last_level": 1.0, "bars_ago": 1}
    assert structure_confluence(worst, "LONG")["delta"] >= STRUCT_ADJ_FLOOR

    best = _with_bos("bullish", count=99)
    assert structure_confluence(best, "LONG")["delta"] <= STRUCT_ADJ_CEILING


def test_risk_can_cut_harder_than_confirmation_can_inflate():
    # Deliberate asymmetry: being wrong should cost more than being right pays.
    assert abs(STRUCT_ADJ_FLOOR) > STRUCT_ADJ_CEILING


# ── Integration with generate_signal ────────────────────────────────────────

def test_generate_signal_reports_the_adjustment_and_factors():
    from test_flag_pattern_correctness import _make_candles
    from signals import generate_signal

    candles = _make_candles(60, up=True)
    analysis = {"symbol": "BTC", "timeframe": "2H", "candles": candles,
                "bos_streak": {"direction": "bullish", "count": 2, "held": True,
                               "last_level": candles[-1]["close"], "bars_ago": 1}}
    sig = generate_signal(analysis)
    assert "structure_adjustment" in sig
    assert "structure_factors" in sig
    assert isinstance(sig["structure_adjustment"], int)


def test_structure_never_flips_direction():
    """The core invariant: structure adjusts conviction, not the trade side."""
    from test_flag_pattern_correctness import _make_candles
    from signals import generate_signal

    candles = _make_candles(60, up=True)
    base = {"symbol": "BTC", "timeframe": "2H", "candles": candles}
    baseline = generate_signal(dict(base))

    hostile = dict(base)
    hostile["bos_streak"] = {"direction": "bearish", "count": 9, "held": True,
                            "last_level": candles[-1]["close"], "bars_ago": 1}
    hostile["equal_levels"] = {"eql": {"price": candles[-1]["close"] * 0.999,
                                       "touches": 9}}
    hostile_sig = generate_signal(hostile)

    assert hostile_sig["direction"] == baseline["direction"], \
        "market structure must not change which side we trade"
    if baseline["direction"] != "NEUTRAL":
        assert hostile_sig["strength"] <= baseline["strength"], \
            "hostile structure must not raise conviction"


def test_strength_stays_within_bounds():
    from test_flag_pattern_correctness import _make_candles
    from signals import generate_signal

    for up in (True, False):
        candles = _make_candles(60, up=up)
        sig = generate_signal({
            "symbol": "BTC", "timeframe": "2H", "candles": candles,
            "bos_streak": {"direction": "bullish" if up else "bearish",
                           "count": 99, "held": True,
                           "last_level": candles[-1]["close"], "bars_ago": 1},
            "equal_levels": {"eql": {"price": candles[-1]["close"] * 0.9999,
                                     "touches": 99},
                             "eqh": {"price": candles[-1]["close"] * 1.0001,
                                     "touches": 99}},
        })
        assert 0 <= sig["strength"] <= 100


# ── BOS freshness decay ─────────────────────────────────────────────────────
# Regression: a break nine bars old carried the SAME weight as one on the last
# candle. CHoCH already decays over ten bars; BOS did not decay at all, so stale
# structure kept moving conviction forever.

def test_bos_bonus_decays_with_age():
    fresh = structure_confluence(_with_bos("bullish", count=3, bars_ago=0), "LONG")["delta"]
    mid = structure_confluence(_with_bos("bullish", count=3, bars_ago=5), "LONG")["delta"]
    old = structure_confluence(_with_bos("bullish", count=3, bars_ago=9), "LONG")["delta"]
    assert fresh > mid > old >= 0, f"expected decay, got {fresh} / {mid} / {old}"


def test_bos_penalty_decays_with_age():
    fresh = structure_confluence(_with_bos("bearish", count=3, bars_ago=0), "LONG")["delta"]
    old = structure_confluence(_with_bos("bearish", count=3, bars_ago=9), "LONG")["delta"]
    assert fresh < old <= 0, f"expected decay, got {fresh} / {old}"


def test_bos_older_than_the_decay_window_is_worth_nothing():
    for age in (BOS_DECAY_BARS, BOS_DECAY_BARS + 5, 99):
        out = structure_confluence(_with_bos("bullish", count=9, bars_ago=age), "LONG")
        assert out["delta"] == 0, f"a {age}-bar-old break must not score"
        stale = [f for f in out["factors"] if f["factor"] == "bos_stale"]
        assert stale, "the stale break should still be recorded, just not scored"
        assert stale[0]["points"] == 0
        assert not out["bull_reasons"] and not out["bear_reasons"]


def test_the_live_btc_case_now_scores_zero():
    """1x bullish BOS, held, 9 bars ago, against a SHORT — was -3, should be 0."""
    a = _with_bos("bullish", count=1, held=True, bars_ago=9)
    out = structure_confluence(a, "SHORT")
    assert out["delta"] == 0
    assert [f["factor"] for f in out["factors"]] == ["bos_stale"]


def test_missing_bars_ago_is_treated_as_fresh():
    # Older payloads may omit bars_ago; assuming stale would silently drop the
    # factor, so absence means "no age information" = full weight.
    a = {"candles": _candles(),
         "bos_streak": {"direction": "bullish", "count": 2, "held": True}}
    assert structure_confluence(a, "LONG")["delta"] > 0


def test_factors_record_age_and_freshness():
    out = structure_confluence(_with_bos("bullish", count=3, bars_ago=4), "LONG")
    f = [x for x in out["factors"] if x["factor"] == "bos_aligned"][0]
    assert f["bars_ago"] == 4
    assert 0 < f["freshness"] < 1


def test_reason_text_states_the_age():
    out = structure_confluence(_with_bos("bullish", count=3, bars_ago=3), "LONG")
    assert any("3 bars ago" in r for r in out["bull_reasons"]), \
        "the reason must say how old the break is, so it can be judged"


def test_given_back_beats_freshness_check():
    # A given-back break is worth nothing even when it is brand new.
    out = structure_confluence(_with_bos("bullish", count=5, held=False, bars_ago=0), "LONG")
    assert out["delta"] == 0
    assert [f["factor"] for f in out["factors"]] == ["bos_given_back"]


# ── Pool source: the clustered ladder, not one level per side ────────────────
# Regression: the scorer read only equal_levels, which holds ONE eqh and ONE
# eql. On a live BTC 2H chart that single equal-high was a level price had
# already traded through, while liquidity_pools held a 7-touch and a 4-touch
# cluster 0.18-0.19 ATR overhead — real stop-run risk, scored as zero.
from signals import _nearest_threatening_pool                          # noqa: E402


def _live_btc():
    """The reported BTC 2H state, ATR tuned to the app's own 0.9% figure."""
    price = 64266.4
    spread = (price * 0.009) / 2.0
    candles = [{"timestamp": 1785000000000 + i * 7200000, "open": price,
                "high": price + spread, "low": price - spread, "close": price}
               for i in range(40)]
    return price, {
        "candles": candles,
        "equal_levels": {"eqh": {"price": 64197.0, "touches": 10},
                         "eql": {"price": 63365.4, "touches": 11}},
        "liquidity_pools": [
            {"price": 64941.625, "side": "above", "touches": 8},
            {"price": 63782.675, "side": "below", "touches": 8},
            {"price": 64377.12857143, "side": "above", "touches": 7},
            {"price": 65706.36, "side": "above", "touches": 5},
            {"price": 64368.425, "side": "above", "touches": 4},
            {"price": 62593.66666667, "side": "below", "touches": 3},
            {"price": 66353.7, "side": "above", "touches": 2},
            {"price": 66852.4, "side": "above", "touches": 2}],
    }


def test_the_live_btc_short_now_sees_the_overhead_pool():
    price, a = _live_btc()
    out = structure_confluence(a, "SHORT")
    sr = [f for f in out["factors"] if f["factor"] == "stop_run_risk"]
    assert sr, "a 4-touch pool 0.18 ATR overhead must register for a SHORT"
    assert sr[0]["source"] == "liquidity_pools"
    assert sr[0]["pool_price"] == 64368.425
    assert sr[0]["pool_distance_atr"] < STOP_RUN_ATR
    assert out["delta"] < 0


def test_the_live_btc_long_is_correctly_unaffected():
    # Nearest pool BELOW price is 63782.675, ~0.84 ATR away — out of range.
    price, a = _live_btc()
    out = structure_confluence(a, "LONG")
    assert not [f for f in out["factors"] if f["factor"] == "stop_run_risk"]


def test_ladder_is_preferred_over_equal_levels():
    price, a = _live_btc()
    lvl, touches, src, _pool = _nearest_threatening_pool(a, price, is_long=False)
    assert src == "liquidity_pools"
    assert lvl == 64368.425, "must take the NEAREST threatening pool"


def test_only_the_nearest_pool_scores():
    # Two clusters within range (64368.425 and 64377.13) are one zone in
    # practice; stacking a penalty per level would double-count it.
    price, a = _live_btc()
    out = structure_confluence(a, "SHORT")
    assert len([f for f in out["factors"] if f["factor"] == "stop_run_risk"]) == 1


def test_pools_on_the_safe_side_are_ignored():
    price, a = _live_btc()
    # For a SHORT, everything BELOW price is irrelevant to its stops.
    lvl, _, _, _ = _nearest_threatening_pool(a, price, is_long=False)
    assert lvl > price
    lvl_long, _, _, _ = _nearest_threatening_pool(a, price, is_long=True)
    assert lvl_long <= price


def test_thinly_touched_pools_are_skipped_in_favour_of_a_real_one():
    price, a = _live_btc()
    a["liquidity_pools"] = [
        {"price": price + 1.0, "touches": 1},                 # nearer but 1 touch
        {"price": price + 60.0, "touches": 6},                # the real pool
    ]
    lvl, touches, _, _ = _nearest_threatening_pool(a, price, is_long=False)
    assert lvl == price + 60.0 and touches == 6


def test_falls_back_to_equal_levels_without_a_ladder():
    price, a = _live_btc()
    a.pop("liquidity_pools")
    a["equal_levels"] = {"eqh": {"price": price + 50.0, "touches": 6}}
    lvl, touches, src, _pool = _nearest_threatening_pool(a, price, is_long=False)
    assert src == "equal_levels" and lvl == price + 50.0 and touches == 6


def test_fallback_still_rejects_an_already_breached_level():
    # The original bug: eqh BELOW price cannot hold a short's stops.
    price, a = _live_btc()
    a.pop("liquidity_pools")
    lvl, _, src, _ = _nearest_threatening_pool(a, price, is_long=False)
    assert lvl is None and src is None


def test_empty_and_malformed_pools_are_survivable():
    price, a = _live_btc()
    for bad in ([], None,
                [{"price": None, "touches": 5}],
                [{"price": "abc", "touches": 5}],
                [{"price": price + 10}],                 # no touches
                [{"price": 0, "touches": 9}],
                [{}]):
        a["liquidity_pools"] = bad
        # Must not raise; falls through to equal_levels (which is breached here).
        assert structure_confluence(a, "SHORT")["delta"] <= 0


def test_pool_exactly_at_price_is_the_worst_case_not_an_exemption():
    price, a = _live_btc()
    a["liquidity_pools"] = [{"price": price, "touches": 6}]
    for d in ("LONG", "SHORT"):
        out = structure_confluence(a, d)
        sr = [f for f in out["factors"] if f["factor"] == "stop_run_risk"]
        assert sr and sr[0]["pool_distance_atr"] == 0.0, f"{d} should register"


# ── Pool recency decay ──────────────────────────────────────────────────────
# Resting stops are not permanent — orders get filled, cancelled or moved. A
# cluster last touched 29 bars ago is weaker evidence that stops sit there NOW
# than one touched on the last candle. Pools previously held full weight forever,
# the same flaw BOS had before it gained a decay.
from signals import (                                                  # noqa: E402
    POOL_DECAY_BARS, POOL_STALE_FLOOR, SL_POOL_MIN_FRESHNESS,
    _candle_interval_ms, pool_bars_ago, pool_freshness,
)

_LATEST = 1785369600000
_INTERVAL = 7200000                       # 2H


def _aged_candles(n=40):
    px = 64266.4
    spread = (px * 0.009) / 2
    return [{"timestamp": _LATEST - (n - 1 - i) * _INTERVAL, "open": px,
             "high": px + spread, "low": px - spread, "close": px} for i in range(n)]


def _pool_aged(bars_ago, *, price=64368.425, touches=4):
    return {"price": price, "touches": touches,
            "last_ts": _LATEST - bars_ago * _INTERVAL}


def test_candle_interval_is_detected():
    assert _candle_interval_ms(_aged_candles()) == _INTERVAL
    assert _candle_interval_ms([]) is None
    assert _candle_interval_ms([{"timestamp": 1}]) is None


def test_bars_ago_from_last_touch():
    c = _aged_candles()
    for n in (0, 8, 16, 29, 56):
        assert pool_bars_ago(_pool_aged(n), c) == n


def test_freshness_curve():
    c = _aged_candles()
    assert pool_freshness(_pool_aged(0), c) == pytest.approx(1.0)
    # The headline: 20 bars is exactly half weight.
    assert pool_freshness(_pool_aged(20), c) == pytest.approx(0.5)
    assert pool_freshness(_pool_aged(POOL_DECAY_BARS), c) == POOL_STALE_FLOOR


def test_freshness_decays_monotonically():
    c = _aged_candles()
    vals = [pool_freshness(_pool_aged(n), c) for n in (0, 5, 10, 20, 30)]
    assert all(a >= b for a, b in zip(vals, vals[1:])), vals


def test_freshness_never_reaches_zero():
    # A level defended eight times is still a level. Staleness discounts the
    # claim "stops rest here", it does not erase the price.
    c = _aged_candles()
    for n in (POOL_DECAY_BARS, 100, 5000):
        assert pool_freshness(_pool_aged(n), c) == POOL_STALE_FLOOR
        assert POOL_STALE_FLOOR > 0


def test_missing_last_ts_counts_as_fresh_not_stale():
    # Absence of age information must not be read as staleness, or an older
    # payload would silently lose every pool.
    c = _aged_candles()
    assert pool_bars_ago({"price": 1.0, "touches": 5}, c) is None
    assert pool_freshness({"price": 1.0, "touches": 5}, c) == 1.0


@pytest.mark.parametrize("bad", [None, "abc", {}, []])
def test_malformed_last_ts_is_treated_as_unknown(bad):
    c = _aged_candles()
    assert pool_freshness({"price": 1.0, "touches": 5, "last_ts": bad}, c) == 1.0


def test_freshness_needs_candles_to_measure_against():
    assert pool_freshness(_pool_aged(20), []) == 1.0


# ── Decay applied to the stop-run penalty ──────────────────────────────────

def _aged_analysis(bars_ago, touches=4):
    c = _aged_candles()
    px = c[-1]["close"]
    atr = px * 0.009
    return {"candles": c,
            "liquidity_pools": [{"price": px + atr * 0.18, "touches": touches,
                                 "last_ts": _LATEST - bars_ago * _INTERVAL}]}


def test_stop_run_penalty_decays_with_pool_age():
    fresh = structure_confluence(_aged_analysis(0), "SHORT")["delta"]
    mid = structure_confluence(_aged_analysis(20), "SHORT")["delta"]
    old = structure_confluence(_aged_analysis(40), "SHORT")["delta"]
    assert fresh < mid < old <= 0, f"{fresh} / {mid} / {old}"


def test_the_live_29_bar_pool_is_heavily_discounted():
    """The reported case: a 4-touch pool 29 bars old was charging full price."""
    out = structure_confluence(_aged_analysis(29), "SHORT")
    sr = [f for f in out["factors"] if f["factor"] == "stop_run_risk"]
    assert sr, "it should still register, just cheaply"
    assert sr[0]["bars_ago"] == 29
    assert sr[0]["freshness"] == POOL_STALE_FLOOR
    assert abs(sr[0]["points"]) <= 2, f"expected a token charge, got {sr[0]['points']}"


def test_the_reason_states_the_pool_age():
    out = structure_confluence(_aged_analysis(29), "SHORT")
    assert any("29 bars ago" in r for r in out["bull_reasons"]), \
        "the age must be visible so the discount can be judged"


def test_a_fully_discounted_pool_is_recorded_as_stale():
    # Weak + old + far enough that the discount rounds it to nothing.
    c = _aged_candles()
    px, atr = c[-1]["close"], c[-1]["close"] * 0.009
    a = {"candles": c,
         "liquidity_pools": [{"price": px + atr * 0.34, "touches": 2,
                              "last_ts": _LATEST - 60 * _INTERVAL}]}
    out = structure_confluence(a, "SHORT")
    kinds = {f["factor"] for f in out["factors"]}
    assert out["delta"] == 0
    assert "stop_run_stale" in kinds


def test_factors_carry_age_and_freshness():
    sr = [f for f in structure_confluence(_aged_analysis(10), "SHORT")["factors"]
          if f["factor"] == "stop_run_risk"][0]
    assert sr["bars_ago"] == 10
    assert 0 < sr["freshness"] < 1


# ── Decay gates stop WIDENING ──────────────────────────────────────────────

def test_a_stale_pool_cannot_widen_a_stop():
    # Spending real risk needs a live claim that a sweep is coming.
    from signals import clear_stop_of_liquidity
    c = _aged_candles()
    entry, sl_dist, atr = 64278.446, 643.6, 64266.4 * 0.009
    stale = {"candles": c,
             "liquidity_pools": [{"price": entry + sl_dist + atr * 0.05,
                                  "touches": 8,
                                  "last_ts": _LATEST - 40 * _INTERVAL}]}
    r = clear_stop_of_liquidity(stale, entry=entry, sl_dist=sl_dist,
                                is_long=False, atr=atr, max_sl_abs=entry * 0.02)
    assert r["moved"] is False and r["blocked"] is False
    assert r["sl_dist"] == sl_dist


def test_a_fresh_pool_still_widens_a_stop():
    from signals import clear_stop_of_liquidity
    c = _aged_candles()
    entry, sl_dist, atr = 64278.446, 643.6, 64266.4 * 0.009
    fresh = {"candles": c,
             "liquidity_pools": [{"price": entry + sl_dist + atr * 0.05,
                                  "touches": 8,
                                  "last_ts": _LATEST - 2 * _INTERVAL}]}
    r = clear_stop_of_liquidity(fresh, entry=entry, sl_dist=sl_dist,
                                is_long=False, atr=atr, max_sl_abs=entry * 0.02)
    assert r["moved"] is True


def test_stop_widening_threshold_is_stricter_than_the_scoring_floor():
    # A pool may be too stale to move a stop while still worth a small penalty.
    assert SL_POOL_MIN_FRESHNESS > POOL_STALE_FLOOR
