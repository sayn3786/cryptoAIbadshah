"""
Fibonacci retracement + the 0.618–0.786 golden/discount pocket.

The "sweep or deviation, then buy the discount" confluence: from the most recent
swing, whichever extreme printed later sets the leg direction, and the 0.618–0.786
band is the entry zone (discount for an up-leg → longs, premium for a down-leg →
shorts). These tests hold the leg detection, the levels, and the zone flags.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import fibonacci_retracement as fib                # noqa: E402


def _c(seq):
    return [{"high": x, "low": x, "close": x, "open": x, "volume": 1} for x in seq]


_UP = [100, 109, 118, 127, 136, 145, 155, 164, 173, 182, 191, 200]   # 100 → 200


def test_up_leg_golden_pocket_is_a_long_discount():
    f = fib(_c(_UP + [190, 170, 150, 136]))
    assert f["direction"] == "up_leg" and f["bias"] == "long"
    assert f["swing_high"] == 200 and f["swing_low"] == 100
    assert f["in_golden_pocket"] is True and f["status"] == "golden_pocket"
    assert "discount" in f["note"]


def test_up_leg_deeper_zone_is_entry_not_golden():
    # ~0.75 retracement (price 125): inside 0.618–0.786 but past the tight pocket.
    f = fib(_c(_UP + [190, 150, 125]))
    assert f["in_golden_pocket"] is False
    assert f["in_entry_zone"] is True and f["status"] == "entry_zone"


def test_up_leg_extended_when_price_is_at_the_high():
    f = fib(_c(_UP))
    assert f["status"] == "extended" and f["in_entry_zone"] is False


def test_down_leg_golden_pocket_is_a_short_premium():
    f = fib(_c(list(reversed(_UP)) + [110, 130, 164]))
    assert f["direction"] == "down_leg" and f["bias"] == "short"
    assert f["in_golden_pocket"] is True and "premium" in f["note"]


def test_levels_are_the_standard_ratios():
    f = fib(_c(_UP + [180, 160, 140]))
    assert set(f["levels"]) == {"0.236", "0.382", "0.500", "0.618",
                                "0.650", "0.705", "0.786"}
    # 0.618 retracement of a 100→200 up-leg = 200 − 0.618·100 = 138.2
    assert abs(f["levels"]["0.618"] - 138.2) < 0.01
    assert f["golden_pocket"][0] < f["golden_pocket"][1]        # ordered


def test_too_few_candles_or_no_range_returns_none():
    assert fib(_c([1, 2, 3])) is None
    assert fib([]) is None
    assert fib(_c([50] * 20)) is None                          # flat → no range
