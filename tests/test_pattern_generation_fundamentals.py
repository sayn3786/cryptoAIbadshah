"""Adversarial regressions for fundamental pattern-generation invariants."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns as patterns_module  # noqa: E402
from patterns import (  # noqa: E402
    _pattern_from_pivots,
    analyze_elliott_wave,
    detect_accumulation_range,
    detect_choch,
    detect_equal_levels,
    detect_flags,
    pick_dominant_flags,
)
from test_flag_pattern_correctness import build_flag  # noqa: E402


def _c(i, close=100.0, high=None, low=None):
    return {
        "timestamp": 1_000_000 + i * 3_600_000,
        "open": close,
        "high": close + 0.5 if high is None else high,
        "low": close - 0.5 if low is None else low,
        "close": close,
        "volume": 100.0,
    }


def test_elliott_does_not_turn_pivot_count_into_a_trade_bias():
    candles = [_c(i, 100 + i * 0.2) for i in range(14)]
    highs = [{"index": i, "price": 102 + i, "timestamp": candles[i]["timestamp"]}
             for i in (1, 3, 5, 7, 9, 11)]
    lows = [{"index": i, "price": 98 + i, "timestamp": candles[i]["timestamp"]}
            for i in (2, 4, 6, 8, 10, 12)]
    out = analyze_elliott_wave(candles, highs, lows)
    assert out["bias"] == "neutral"
    assert out["current_wave"] is None
    assert out["targets"] == []
    assert "Unconfirmed" in out["wave_count"]


def test_choch_requires_both_sides_of_the_prior_trend(monkeypatch):
    candles = [_c(i) for i in range(20)]
    candles[-1]["close"] = 94.0
    monkeypatch.setattr(
        patterns_module, "find_pivots", lambda _candles, window=3: (
            [{"index": 5, "price": 110.0, "timestamp": 5},
             {"index": 12, "price": 108.0, "timestamp": 12}],
            [{"index": 4, "price": 90.0, "timestamp": 4},
             {"index": 13, "price": 95.0, "timestamp": 13}],
        ))
    assert detect_choch(candles)["signal"] == "none"


def test_choch_freshness_belongs_to_break_candle_not_old_level(monkeypatch):
    candles = [_c(i) for i in range(20)]
    candles[-1]["close"] = 94.0
    monkeypatch.setattr(
        patterns_module, "find_pivots", lambda _candles, window=3: (
            [{"index": 5, "price": 105.0, "timestamp": 5},
             {"index": 12, "price": 110.0, "timestamp": 12}],
            [{"index": 4, "price": 90.0, "timestamp": 4},
             {"index": 13, "price": 95.0, "timestamp": 13}],
        ))
    out = detect_choch(candles)
    assert out["signal"] == "bearish"
    assert out["candles_ago"] == 0
    assert out["level_age_candles"] == 6


def test_triangle_cannot_confirm_after_its_apex():
    candles = [_c(i, 100.0, high=111 - i, low=89 + i) for i in range(13)]
    hs = [{"index": i, "price": 110 - i, "timestamp": candles[i]["timestamp"]}
          for i in (0, 2, 4)]
    ls = [{"index": i, "price": 90 + i, "timestamp": candles[i]["timestamp"]}
          for i in (1, 3, 5)]
    assert _pattern_from_pivots(candles, hs, ls, "1D", 1.0) is None


def test_overlong_flag_is_rejected_instead_of_silently_capped():
    for bars in (16, 17):
        candles = build_flag(
            lead=30, direction="up", pole_step=1.5, pole_bars=4,
            flag_closes=[105.0] * bars, flag_half=0.3, post_closes=[108.0])
        flags = pick_dominant_flags(detect_flags(candles, "1D", 1.0, 4.0))
        assert not [f for f in flags if f.get("confirmed") and f.get("is_active")]


def test_exactly_max_length_flag_can_break_on_the_next_candle():
    candles = build_flag(
        lead=30, direction="up", pole_step=1.5, pole_bars=4,
        flag_closes=[105.0] * 15, flag_half=0.3, post_closes=[108.0])
    flags = pick_dominant_flags(detect_flags(candles, "1D", 1.0, 4.0))
    assert [f for f in flags if f.get("confirmed") and f.get("is_active")]


def test_flat_plateau_is_not_counted_as_many_equal_level_touches():
    candles = [_c(i, 100.0, high=101.0, low=99.0) for i in range(25)]
    assert detect_equal_levels(candles) == {"eqh": None, "eql": None}


def test_accumulation_requires_documented_sixty_percent_inner_closes():
    closes = [91.0] * 4 + [109.0] * 5 + [100.0] * 11
    candles = [_c(i, p, high=p + 0.2, low=p - 0.2) for i, p in enumerate(closes)]
    assert detect_accumulation_range(candles, window=20)["detected"] is False
