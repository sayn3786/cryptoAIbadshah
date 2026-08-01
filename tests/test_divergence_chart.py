"""
Showing the divergence instead of only asserting it.

The card said *"Bullish RSI divergence — price lower low but RSI rising"* and
showed nothing. Whether that was true had to be taken on trust, or checked on
someone else's chart — the one thing a dashboard should save you.

The detector already located the two pivots in order to reach its verdict; it
just discarded the coordinates. Carrying them lets the card draw the claim:
price pivots on one axis, RSI pivots on another, joined by lines that slope
opposite ways.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import calculate_rsi_series, detect_rsi_divergence      # noqa: E402


def _candles(seq, with_ts=True):
    out = []
    for i, c in enumerate(map(float, seq)):
        d = {"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": 100}
        if with_ts:
            d["timestamp"] = 1785000000000 + i * 86400000
        out.append(d)
    return out


# A sharp drop to low 1 (deep RSI), a bounce, then a GENTLE drift to a lower
# low 2 — the gentle decline is what leaves RSI higher against a lower price.
BULLISH = ([100.0] * 20 + [96, 90, 83, 76, 70] + [74, 79, 83, 85]
           + [84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68]
           + [70, 73, 76])


def _detect(seq, with_ts=True):
    candles = _candles(seq, with_ts)
    rsi = calculate_rsi_series([c["close"] for c in candles])
    return detect_rsi_divergence(candles, rsi)


def test_the_fixture_really_is_a_bullish_divergence():
    # Guard the guard: if this stops detecting, every test below is vacuous.
    d = _detect(BULLISH)
    assert d["type"] == "bullish"
    assert d["strength"] > 0


def test_the_pivots_come_back_with_the_verdict():
    p = _detect(BULLISH)["points"]
    assert p["kind"] == "low"
    for end in ("prev", "curr"):
        assert set(p[end]) == {"timestamp", "price", "rsi"}
        assert all(v is not None for v in p[end].values())


def test_the_pivots_show_what_the_words_claim():
    # "price lower low but RSI rising" — the drawn points must agree with the
    # sentence, or the picture would contradict the card above it.
    p = _detect(BULLISH)["points"]
    assert p["curr"]["price"] < p["prev"]["price"], "price must make a LOWER low"
    assert p["curr"]["rsi"] > p["prev"]["rsi"], "RSI must make a HIGHER low"


def test_the_pivots_are_in_chronological_order():
    p = _detect(BULLISH)["points"]
    assert p["prev"]["timestamp"] < p["curr"]["timestamp"]


def test_the_strength_is_the_rsi_gap_between_the_two_pivots():
    d = _detect(BULLISH)
    p = d["points"]
    assert abs(d["strength"] - round(p["curr"]["rsi"] - p["prev"]["rsi"], 1)) < 0.05


def test_no_divergence_carries_no_points():
    d = _detect([100.0] * 60)          # flat: nothing to diverge
    assert d["type"] is None
    assert d.get("points") is None


# ── Detection must not start depending on a drawing concern ────────────────

def test_a_feed_without_timestamps_still_detects():
    # Timestamps are needed to DRAW, never to detect. Requiring them would turn
    # a cosmetic addition into a detection regression — and it did, until the
    # extraction was made tolerant.
    d = _detect(BULLISH, with_ts=False)
    assert d["type"] == "bullish", "detection must not depend on timestamps"
    assert d.get("points") is None, "but there is nothing to draw"


# ── The renderer ───────────────────────────────────────────────────────────

def _js():
    path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "js",
                        "dashboard.js")
    return open(path, encoding="utf-8").read()


def test_the_card_draws_only_when_it_has_real_pivots():
    # A card with no picture is honest; a picture drawn from guessed points
    # would not be.
    src = _js()
    fn = src.split("function buildDivergenceSVG", 1)[1].split("\n}", 1)[0]
    assert "if (!p?.prev || !p?.curr" in fn
    assert "return ''" in fn


def test_a_stale_chart_is_cleared_when_the_divergence_goes_away():
    # Same failure mode as the RSI chart: leaving the previous render up makes a
    # vanished signal look like a live one.
    src = _js()
    fn = src.split("function renderRsiDivCard", 1)[1].split("\n}\n", 1)[0]
    assert "chartEl.innerHTML = ''" in fn


def test_the_pivot_prices_are_inside_the_drawn_range():
    # A pivot is a low or a high, so it sits OUTSIDE the close line. If the
    # y-domain came only from closes, the marker would fall outside the box.
    src = _js()
    assert "p.prev.price, p.curr.price" in src
    assert "p.prev.rsi, p.curr.rsi" in src


def test_a_flat_series_does_not_divide_by_zero():
    src = _js()
    fn = src.split("function _divScales", 1)[1].split("\n}", 1)[0]
    assert "if (!(hi > lo))" in fn, "a flat series must still produce a scale"


def test_forming_and_confirmed_are_drawn_differently():
    # A forming divergence is not yet a fact; it must not look like one.
    src = _js()
    assert "div.forming ? '3 3' : '4 2'" in src


def test_the_chart_needs_no_charting_library():
    # Inline SVG on purpose: the CDN-hosted chart library is exactly what makes
    # the other panels unverifiable here.
    src = _js()
    fn = src.split("function buildDivergenceSVG", 1)[1].split("\n}", 1)[0]
    assert "LightweightCharts" not in fn
    assert "<svg" in fn
