"""
Bull Market Support Band — the 20-week SMA and 21-week EMA.

A weekly macro read: price above the band is bull structure, a weekly close below
it marks the shift out of a bull phase. These tests hold the two lines, the band
they bound, and the above/inside/below status.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import bull_market_support_band as bmsb            # noqa: E402


def test_none_until_21_weekly_closes():
    assert bmsb([100] * 20) is None
    assert bmsb([]) is None
    assert bmsb(None) is None


def test_flat_series_puts_price_on_the_band():
    # 21 identical closes → SMA20 == EMA21 == price → price sits ON the band edge,
    # which counts as holding above (>=).
    b = bmsb([100.0] * 21)
    assert b["sma_20w"] == 100.0 and b["ema_21w"] == 100.0
    assert b["band_low"] == 100.0 and b["band_high"] == 100.0
    assert b["status"] == "above" and b["distance_pct"] == 0.0


def test_price_above_the_band_reads_bullish():
    # A long flat base then a jump — last close well above both averages.
    b = bmsb([100.0] * 25 + [130.0])
    assert b["price"] == 130.0
    assert b["status"] == "above"
    assert b["band_high"] < 130.0
    assert b["distance_pct"] > 0
    assert "holding above" in b["note"]


def test_price_below_the_band_reads_bear_support_lost():
    b = bmsb([100.0] * 25 + [70.0])
    assert b["status"] == "below"
    assert b["price"] == 70.0
    assert b["distance_pct"] < 0
    assert "below the band" in b["note"]


def test_the_two_lines_are_the_20w_sma_and_21w_ema():
    closes = [float(x) for x in range(1, 40)]     # rising ramp
    b = bmsb(closes)
    assert b["sma_20w"] == round(sum(closes[-20:]) / 20.0, 2)
    # on a monotonic ramp the faster 20W SMA sits above the 21W EMA
    assert b["ema_21w"] < b["sma_20w"]
    assert b["band_low"] == b["ema_21w"] and b["band_high"] == b["sma_20w"]
