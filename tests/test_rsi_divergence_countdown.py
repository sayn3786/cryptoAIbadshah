"""
A FORMING divergence counts down as candles close.

A provisional pivot needs `pivot_window` closed candles after it before it is a
real pivot. The card used to quote that window flat — "unconfirmed until 3 more
closes hold" — so a divergence spotted yesterday still claimed three closes to go
today. It read as a live countdown while being a constant, which is worse than
showing nothing: it invites you to wait for a confirmation that already happened.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import detect_rsi_divergence                         # noqa: E402


def _forming_bullish(n=30, pivot_window=3, closes_since=0):
    """
    Candles with one clear swing low, then a LOWER low `closes_since` candles
    before the end — a bullish divergence that is still provisional.
    """
    lows, highs, closes, rsi = [], [], [], []
    for i in range(n):
        base = 100 - i * 0.05
        lows.append(base - 0.4)
        highs.append(base + 0.4)
        closes.append(base)
        rsi.append(50.0)
    lows[10], rsi[10] = lows[10] - 4.0, 30.0        # confirmed pivot low
    ti = n - 1 - closes_since                        # provisional second low
    lows[ti], rsi[ti] = lows[10] - 2.0, 42.0
    candles = [{"low": lows[i], "high": highs[i], "close": closes[i],
                "open": closes[i], "volume": 1} for i in range(n)]
    return candles, rsi, pivot_window


def _forming_bearish(n=30, pivot_window=3, closes_since=0):
    lows, highs, closes, rsi = [], [], [], []
    for i in range(n):
        base = 100 + i * 0.05
        lows.append(base - 0.4)
        highs.append(base + 0.4)
        closes.append(base)
        rsi.append(50.0)
    highs[10], rsi[10] = highs[10] + 4.0, 70.0       # confirmed pivot high
    ti = n - 1 - closes_since
    highs[ti], rsi[ti] = highs[10] + 2.0, 58.0
    candles = [{"low": lows[i], "high": highs[i], "close": closes[i],
                "open": closes[i], "volume": 1} for i in range(n)]
    return candles, rsi, pivot_window


# ── The countdown actually counts ───────────────────────────────────────────

@pytest.mark.parametrize("closes_since,expected", [(0, 3), (1, 2), (2, 1)])
def test_the_wait_shrinks_as_candles_close(closes_since, expected):
    candles, rsi, pw = _forming_bullish(closes_since=closes_since)
    div = detect_rsi_divergence(candles, rsi, pivot_window=pw)
    assert div["forming"] is True
    assert div["closes_to_confirm"] == expected
    assert f"{expected} more close" in div["description"]


def test_the_same_setup_a_day_later_does_not_repeat_yesterdays_number():
    # THE bug: two scans a candle apart quoted the identical wait.
    today = detect_rsi_divergence(*_forming_bullish(closes_since=0)[:2], pivot_window=3)
    tomorrow = detect_rsi_divergence(*_forming_bullish(closes_since=1)[:2], pivot_window=3)
    assert today["closes_to_confirm"] != tomorrow["closes_to_confirm"]
    assert today["description"] != tomorrow["description"]


def test_one_remaining_close_reads_as_singular():
    div = detect_rsi_divergence(*_forming_bullish(closes_since=2)[:2], pivot_window=3)
    assert "1 more close holds" in div["description"]
    assert "1 more closes" not in div["description"]


def test_a_bearish_forming_divergence_counts_down_too():
    for closes_since, expected in ((0, 3), (2, 1)):
        div = detect_rsi_divergence(*_forming_bearish(closes_since=closes_since)[:2],
                                    pivot_window=3)
        assert div["type"] == "bearish" and div["forming"] is True
        assert div["closes_to_confirm"] == expected


def test_the_window_size_sets_the_starting_count():
    # Weekly and above use a 2-candle pivot window, so a fresh divergence there
    # starts at 2, not 3.
    div = detect_rsi_divergence(*_forming_bullish(pivot_window=2, closes_since=0)[:2],
                                pivot_window=2)
    assert div["closes_to_confirm"] == 2


def test_the_count_is_never_zero_or_negative():
    # At zero the pivot would already be confirmed and reported as such, so a
    # forming divergence must never claim "0 more closes".
    for closes_since in range(0, 3):
        div = detect_rsi_divergence(*_forming_bullish(closes_since=closes_since)[:2],
                                    pivot_window=3)
        assert div["closes_to_confirm"] >= 1


# ── A confirmed divergence is not a countdown ───────────────────────────────

def test_a_confirmed_divergence_carries_no_countdown():
    # Same shape, but the second low is old enough to be a real pivot.
    lows, highs, closes, rsi = [], [], [], []
    n = 30
    for i in range(n):
        base = 100 - i * 0.05
        lows.append(base - 0.4); highs.append(base + 0.4)
        closes.append(base); rsi.append(50.0)
    lows[8],  rsi[8]  = lows[8] - 4.0, 30.0
    lows[20], rsi[20] = lows[8] - 2.0, 42.0          # 9 closes after it
    candles = [{"low": lows[i], "high": highs[i], "close": closes[i],
                "open": closes[i], "volume": 1} for i in range(n)]

    div = detect_rsi_divergence(candles, rsi, pivot_window=3)
    assert div["type"] == "bullish"
    assert not div.get("forming")
    assert "closes_to_confirm" not in div
    assert "unconfirmed" not in (div["description"] or "")


# ── Played out: the turn already happened before the pivot confirmed ─────────

def _played_out_bullish(closes_since=2):
    """The forming-bullish setup, but the bounce ALREADY happened: after the
    provisional low, price CLOSES sharply higher and RSI reclaims 50."""
    candles, rsi, pw = _forming_bullish(closes_since=closes_since)
    n = len(candles)
    ti = n - 1 - closes_since
    for j in range(ti + 1, n):                    # recovery closes after the low
        candles[j]["close"] = 108.0
        candles[j]["high"] = 108.4
        rsi[j] = 56.0
    return candles, rsi, pw


def test_a_played_out_bullish_divergence_is_labelled_not_forming():
    div = detect_rsi_divergence(*_played_out_bullish()[:2], pivot_window=3)
    assert div["type"] == "bullish"
    assert div["played_out"] is True
    assert div["status"] == "played_out"
    assert "closes_to_confirm" not in div          # no countdown on a spent read
    assert "played out" in div["description"]


def test_a_still_forming_divergence_is_not_marked_played_out():
    # The countdown fixture (price has NOT recovered) must stay forming.
    div = detect_rsi_divergence(*_forming_bullish(closes_since=1)[:2], pivot_window=3)
    assert div["forming"] is True
    assert div.get("played_out") is False
    assert div["status"] == "forming"
