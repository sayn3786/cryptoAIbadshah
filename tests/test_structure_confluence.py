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
    BOS_MAX_BONUS, BOS_OPPOSED_PENALTY, CHASE_MAX_PENALTY,
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

def _with_bos(direction, count=2, held=True):
    return {"candles": _candles(),
            "bos_streak": {"direction": direction, "count": count, "held": held,
                           "last_level": 100.0, "bars_ago": 2}}


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
