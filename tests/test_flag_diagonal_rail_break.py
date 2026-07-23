"""
Diagonal-rail breakout tests.

A SLOPED flag resolves its breakout against the PROJECTED trendline rail, not
the flat oldest-bar high/low:

  * A bearish ASCENDING flag breaks DOWN when a candle closes below its RISING
    lower rail — earlier (and higher) than the flat flag_low.
  * A bullish DESCENDING flag breaks UP when a candle closes above its FALLING
    upper rail — earlier (and lower) than the flat flag_high.
  * A NEUTRAL flag keeps the flat high/low boundaries (break_level == flag_low /
    flag_high), preserving prior behaviour.

All candles are synthetic OHLC; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import detect_flags, NEUTRAL_SLOPE_PCT                     # noqa: E402
from test_flag_pattern_correctness import build_flag, _bear_flags, _bull_flags  # noqa: E402


# ── ascending bear flag: break level rises above the flat low ───────────────────
def test_bear_ascending_break_level_above_flat_low():
    cs = build_flag(direction="down", flag_bars=5, flag_drift=+0.4, flag_half=0.4)
    bears = _bear_flags(detect_flags(cs, "1D", 1.0, 4.0))
    assert bears, "expected a bearish ascending flag"
    f = bears[0]
    assert f["flag_slope"] == "ascending"
    assert f["rail_break"] is True
    # the rising lower rail sits ABOVE the flat flag_low, so the breakdown
    # trigger is closer to price (earlier) than the oldest-bar low
    assert f["break_low"] > f["flag_low"]
    assert f["break_level"] == f["break_low"]


# ── descending bull flag: break level below the flat high ───────────────────────
def test_bull_descending_break_level_below_flat_high():
    cs = build_flag(direction="up", flag_bars=5, flag_drift=-0.4, flag_half=0.4)
    bulls = _bull_flags(detect_flags(cs, "1D", 1.0, 4.0))
    assert bulls, "expected a bullish descending flag"
    f = bulls[0]
    assert f["flag_slope"] == "descending"
    assert f["rail_break"] is True
    assert f["break_high"] < f["flag_high"]
    assert f["break_level"] == f["break_high"]


# ── neutral flag: rails collapse to the flat boundaries ─────────────────────────
def test_neutral_flag_break_levels_equal_flat():
    # a genuine retrace held FLAT across many bars → neutral slope (the single
    # elevated first-bar high is averaged out by the regression)
    cs = build_flag(direction="up", pole_step=2.0, pole_bars=4,
                    flag_closes=[106.0] * 8, flag_half=0.3)
    bulls = _bull_flags(detect_flags(cs, "1D", 1.0, 4.0))
    assert bulls, "expected a neutral bullish flag"
    f = bulls[0]
    assert f["flag_slope"] == "neutral"
    assert f["rail_break"] is False
    assert f["break_low"] == f["flag_low"]
    assert f["break_high"] == f["flag_high"]
    assert f["break_level"] == f["flag_high"]


# ── behavioural: a close below the rising rail (but above the flat low) confirms ─
def test_bear_ascending_confirms_on_rail_not_flat_low():
    # Ascending bear flag, then a post candle that stalls: it closes BELOW the
    # rising lower rail yet remains well ABOVE the flat flag_low. Diagonal logic
    # must confirm the breakdown; flat-low logic never would.
    bears0 = _bear_flags(detect_flags(build_flag(direction="down", flag_bars=5,
                                                 flag_drift=+0.6, flag_half=0.4),
                                      "1D", 1.0, 4.0))
    assert bears0, "expected a forming ascending bear flag before the stall"
    f0 = bears0[0]
    # a stall value strictly between the flat low and the (higher) rail break
    stall = round((f0["flag_low"] + f0["break_low"]) / 2.0, 4)
    assert f0["flag_low"] < stall < f0["break_low"]

    cs = build_flag(direction="down", flag_bars=5, flag_drift=+0.6, flag_half=0.4,
                    post_closes=[stall])
    bears = _bear_flags(detect_flags(cs, "1D", 1.0, 4.0))
    confirmed = [f for f in bears if f["confirmed"] and f["breakout_dir"] == "down"]
    assert confirmed, "close below the rising rail must confirm the breakdown"
