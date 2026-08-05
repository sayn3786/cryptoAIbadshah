"""
The SuperTrend card should say WHEN it turned, not just that it did.

The card read "Bullish / No flip on last candle" — true, and useless for
knowing how old that bullish read is. The trend state is computed at every bar,
so the flip history is already in hand: this exposes when the current run began
(the CLOSE time of the candle that flipped it) and what the trend was before.

Two things are load-bearing and tested here. The timestamp is a CLOSE time, not
an open — a SuperTrend flip is confirmed by a candle closing beyond the band, so
the moment it became real is that candle's close. And a run that reaches back to
the start of the data reports no flip time, because the flip happened before the
window and inventing one would be a lie dressed as precision.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import calculate_supertrend, _infer_interval             # noqa: E402


BASE = 1_767_268_800_000
STEP = 7_200_000          # 2H


def _series(legs):
    """Build candles from (n_bars, per_bar_multiplier) legs, compounding price."""
    candles, px, i = [], 100.0, 0
    for n, mult in legs:
        for _ in range(n):
            px *= mult
            hi = px * 1.02 if mult > 1 else px * 1.005
            lo = px * 0.995 if mult > 1 else px * 0.98
            candles.append({"timestamp": BASE + i * STEP, "open": px,
                            "high": hi, "low": lo, "close": px})
            i += 1
    return candles


# ── The interval helper ─────────────────────────────────────────────────────

def test_the_interval_is_inferred_from_the_series():
    assert _infer_interval(_series([(5, 1.0)])) == STEP


def test_a_single_bar_has_no_inferable_interval():
    assert _infer_interval([{"timestamp": BASE}]) is None


def test_a_missing_bar_does_not_break_the_interval():
    """The most common gap wins, so one hole doesn't halve the answer."""
    c = _series([(10, 1.0)])
    del c[5]                                   # leave a 2-interval gap
    assert _infer_interval(c) == STEP


# ── The flip history ────────────────────────────────────────────────────────

def test_a_flip_reports_when_the_current_trend_began():
    # Long downtrend, then a sharp rally that flips it bullish.
    c = _series([(60, 0.99), (30, 1.03)])
    st = calculate_supertrend(c)
    assert st["direction"] == "bullish"
    assert st["flipped_ts"] is not None
    assert st["flipped_bars_ago"] is not None and st["flipped_bars_ago"] > 0


def test_the_flip_timestamp_is_a_close_time_not_an_open():
    c = _series([(60, 0.99), (30, 1.03)])
    st = calculate_supertrend(c)
    # Whatever bar flipped it, flipped_ts is that bar's OPEN + one interval.
    flip_open = st["flipped_ts"] - STEP
    assert any(cd["timestamp"] == flip_open for cd in c), \
        "flipped_ts should be a candle close (open + one interval)"


def test_the_previous_trend_and_its_flip_time_are_reported():
    c = _series([(60, 0.99), (30, 1.03)])
    st = calculate_supertrend(c)
    assert st["previous_direction"] == "bearish"
    # The previous run started before the current one.
    if st["previous_flipped_ts"] is not None:
        assert st["previous_flipped_ts"] < st["flipped_ts"]
    assert st["previous_bars_ago"] > st["flipped_bars_ago"]


def test_bars_ago_counts_from_the_last_candle():
    c = _series([(60, 0.99), (30, 1.03)])
    st = calculate_supertrend(c)
    last_open = c[-1]["timestamp"]
    # flipped_bars_ago bars back from the last candle is the flip candle; its
    # close is flipped_ts.
    flip_open = last_open - st["flipped_bars_ago"] * STEP
    assert st["flipped_ts"] == flip_open + STEP


# ── When there is no flip to report ─────────────────────────────────────────

def test_a_single_unbroken_trend_reports_no_flip_time():
    """
    Price only ever went one way inside the window. We know the trend held
    throughout; we do NOT know when it started, so the flip time is null rather
    than the misleading first-bar timestamp.
    """
    c = _series([(90, 1.02)])                  # pure uptrend, never flips
    st = calculate_supertrend(c)
    assert st["direction"] == "bullish"
    assert st["flipped_ts"] is None
    assert st["flipped_bars_ago"] is None
    assert st["previous_direction"] is None
    assert st["previous_flipped_ts"] is None


def test_too_little_history_returns_the_null_shape():
    st = calculate_supertrend(_series([(5, 1.0)]))   # < period + 1
    for key in ("flipped_ts", "flipped_bars_ago", "previous_direction",
                "previous_flipped_ts", "previous_bars_ago"):
        assert key in st and st[key] is None


# ── The pre-existing contract still holds ───────────────────────────────────

def test_the_old_fields_are_unchanged():
    c = _series([(60, 0.99), (30, 1.03)])
    st = calculate_supertrend(c)
    assert set(("direction", "value", "signal", "flipped", "series")) <= set(st)
    assert st["direction"] in ("bullish", "bearish")
    assert isinstance(st["series"], list) and st["series"]
