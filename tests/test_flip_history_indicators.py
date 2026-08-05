"""
MACD, EMA and Ichimoku each report when they last flipped.

Same idea the SuperTrend card got: state now is not enough, a reader wants to
know when it turned and what it was before. Each of these indicators exposes a
directional event — the MACD line crossing its signal, price crossing the 50
EMA, Tenkan crossing Kijun — so each can answer it from the per-bar state the
calculation already holds.

Two properties matter and are tested. The flip timestamp is a CLOSE time (the
cross is confirmed when the bar closes), and a run that reaches the start of the
window reports no flip time rather than a made-up first-bar one. The shared
walk `flip_history_bars` is tested directly so the per-indicator tests only have
to prove the wiring.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis as ca                                          # noqa: E402
from indicators import (calculate_macd, calculate_ema_trend,          # noqa: E402
                        calculate_ichimoku, flip_history_bars, flip_close_ts)


BASE = 1_767_268_800_000
STEP = 7_200_000          # 2H


def _oscillating(legs):
    """Candles from (n_bars, per_bar_mult) legs — up/down swings force flips."""
    c, px, i = [], 100.0, 0
    for n, mult in legs:
        for _ in range(n):
            px *= mult
            c.append({"timestamp": BASE + i * STEP, "open": px,
                      "high": px * 1.01, "low": px * 0.99, "close": px,
                      "volume": 1000.0})
            i += 1
    return c


# A long up-down-up series gives every indicator a captured mid-window flip with
# a bearish run before it.
SWINGS = _oscillating([(50, 1.02), (50, 0.985), (45, 1.02)])
CLOSES = [c["close"] for c in SWINGS]


# ── The shared walk ─────────────────────────────────────────────────────────

def test_flip_history_finds_the_last_run_boundary():
    # -1 for a while, then +1 for the last 3 bars.
    states = [-1] * 10 + [1, 1, 1]
    out = flip_history_bars(states)
    assert out["flipped_bars_ago"] == 2          # +1 started 2 bars before last
    assert out["previous_direction"] == "bearish"


def test_a_run_reaching_the_start_reports_nothing():
    """All one direction inside the window — we cannot say when it began."""
    out = flip_history_bars([1] * 20)
    assert out == {"flipped_bars_ago": None, "previous_direction": None,
                   "previous_bars_ago": None}


def test_leading_none_states_are_ignored():
    states = [None] * 5 + [-1] * 6 + [1, 1]
    out = flip_history_bars(states)
    assert out["flipped_bars_ago"] == 1
    assert out["previous_direction"] == "bearish"


def test_an_all_none_series_is_empty():
    assert flip_history_bars([None, None])["flipped_bars_ago"] is None


# ── flip_close_ts ───────────────────────────────────────────────────────────

def test_the_flip_timestamp_is_a_close_not_an_open():
    ts = flip_close_ts(SWINGS, 3)
    assert ts == SWINGS[-4]["timestamp"] + STEP


def test_flip_close_ts_is_none_without_a_bar_count():
    assert flip_close_ts(SWINGS, None) is None


# ── MACD ────────────────────────────────────────────────────────────────────

def test_macd_reports_its_cross_history():
    m = calculate_macd(CLOSES)
    assert m["flipped_bars_ago"] is not None
    assert m["previous_direction"] in ("bullish", "bearish")


def test_macd_flip_ts_is_attached_by_the_analysis_layer():
    a = ca.build_candle_analysis(SWINGS, "2H", "TEST")
    m = a["macd"]
    assert m["flipped_ts"] is not None
    # It is the close of the bar flipped_bars_ago back.
    assert m["flipped_ts"] == SWINGS[-1 - m["flipped_bars_ago"]]["timestamp"] + STEP


def test_macd_the_pre_existing_fields_survive():
    m = calculate_macd(CLOSES)
    for k in ("macd", "signal_line", "histogram", "cross", "zero_cross", "trend"):
        assert k in m


# ── EMA ─────────────────────────────────────────────────────────────────────

def test_ema_reports_when_price_last_crossed_the_50():
    e = calculate_ema_trend(CLOSES)
    assert e["flipped_bars_ago"] is not None
    assert e["previous_direction"] in ("bullish", "bearish")


def test_ema_flip_ts_is_attached_by_the_analysis_layer():
    a = ca.build_candle_analysis(SWINGS, "2H", "TEST")
    e = a["ema_trend"]
    assert e["flipped_ts"] is not None
    assert e["flipped_ts"] == SWINGS[-1 - e["flipped_bars_ago"]]["timestamp"] + STEP


def test_ema_the_pre_existing_fields_survive():
    e = calculate_ema_trend(CLOSES)
    for k in ("trend", "ema7_cross", "short_trend", "above", "below", "ema50"):
        assert k in e


# ── Ichimoku ────────────────────────────────────────────────────────────────

def test_ichimoku_reports_its_tk_cross_history_with_a_close_ts():
    ichi = calculate_ichimoku(SWINGS)
    assert ichi["tk_flipped_bars_ago"] is not None
    # Ichimoku has the candles, so it attaches its own close timestamp.
    assert ichi["tk_flipped_ts"] == \
        SWINGS[-1 - ichi["tk_flipped_bars_ago"]]["timestamp"] + STEP
    assert ichi["tk_previous_direction"] in ("bullish", "bearish")


def test_ichimoku_the_pre_existing_fields_survive():
    ichi = calculate_ichimoku(SWINGS)
    for k in ("tenkan", "kijun", "span_a", "span_b", "cloud_color",
              "price_vs_cloud", "tk_cross", "series"):
        assert k in ichi


def test_ichimoku_too_little_history_is_the_null_shape():
    ichi = calculate_ichimoku(SWINGS[:10])
    assert ichi["tk_cross"] is None
    # The new keys are simply absent on the short-circuit path; the frontend
    # reads them defensively, so absence behaves like "no flip".
    assert ichi.get("tk_flipped_ts") is None


# ── No captured flip → no timestamp ─────────────────────────────────────────

def test_a_one_way_market_reports_no_flip_time():
    up = _oscillating([(120, 1.02)])
    a = ca.build_candle_analysis(up, "2H", "TEST")
    # Price never crossed back under the 50 EMA, so EMA has no captured flip.
    assert a["ema_trend"]["flipped_ts"] is None
    assert a["ema_trend"]["flipped_bars_ago"] is None
