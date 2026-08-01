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


def test_the_panel_draws_only_when_it_has_real_pivots():
    # A card with no picture is honest; a picture drawn from guessed points
    # would not be.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}", 1)[0]
    assert "if (!p?.prev || !p?.curr" in fn
    assert "return ''" in fn


def test_non_finite_timestamps_are_refused():
    # Math.floor(null/1000) is 0 — a point in 1970 that drags the axis with it.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}", 1)[0]
    assert "Number.isFinite(p.prev.timestamp)" in fn


def test_a_vanished_divergence_hides_the_whole_section():
    # Same failure mode as the RSI chart: leaving the last render up makes a
    # signal that has gone away look live. An empty frame is also wrong — it
    # reads as "no data" when the truth is "no divergence".
    src = _js()
    fn = src.split("function renderDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "box.innerHTML = ''" in fn
    assert "sec.style.display = 'none'" in fn


def test_the_card_clears_the_panel_when_there_is_no_divergence():
    src = _js()
    fn = src.split("function renderRsiDivCard", 1)[1].split("\n}\n", 1)[0]
    assert "renderDivergencePanel(null)" in fn


# ── It has to be readable as NUMBERS, not just a shape ─────────────────────

def test_both_pivots_are_labelled_with_their_real_price():
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "_dvValFmt(p.prev.price)" in fn and "_dvValFmt(p.curr.price)" in fn


def test_both_pivots_are_labelled_with_their_rsi_value():
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "p.prev.rsi.toFixed(1)" in fn and "p.curr.rsi.toFixed(1)" in fn


def test_the_price_axis_has_several_labelled_gridlines():
    # Two labels at the raw extremes is an extent, not an axis.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "_dvTicks(P.lo, P.hi, 4)" in fn
    assert "grid(P.y(v), f(v))" in fn


def test_the_rsi_axis_shows_the_levels_that_matter():
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "'70'" in fn and "'30'" in fn
    # 30 and 70 must be inside the domain or the gridlines fall outside the box.
    assert "30, 70" in fn


def test_each_pivot_is_dated():
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "_dvDate(p.prev.timestamp)" in fn


def test_the_panel_says_which_symbol_and_timeframe_it_is():
    # A chart that does not name its instrument invites reading it as another.
    src = _js()
    fn = src.split("function renderDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "S.symbol" in fn and "S.timeframe" in fn


def test_pivot_values_are_inside_the_drawn_range():
    # A pivot is a low or a high, so it sits OUTSIDE the close line. If the
    # y-domain came only from closes, the marker would fall outside the box.
    src = _js()
    assert "p.prev.price, p.curr.price" in src
    assert "p.prev.rsi, p.curr.rsi, 30, 70" in src


def test_a_flat_series_does_not_divide_by_zero():
    src = _js()
    fn = src.split("function _dvScale", 1)[1].split("\n}", 1)[0]
    assert "if (!(hi > lo))" in fn, "a flat series must still produce a scale"


def test_forming_and_confirmed_are_drawn_differently():
    # A forming divergence is not yet a fact; it must not look like one.
    src = _js()
    assert "div.forming ? '5 5' : '7 4'" in src


def test_the_chart_needs_no_charting_library():
    # Inline SVG on purpose: the CDN-hosted chart library is exactly what makes
    # the other panels unverifiable in this environment.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "LightweightCharts" not in fn
    assert "<svg" in fn


# ── Readable as a chart, not just an extent ────────────────────────────────

def test_tick_decimals_come_from_the_step():
    # Ticks are round numbers by construction, so a $10 step needs none —
    # "$190.0" is a decimal place spent saying nothing.
    src = _js()
    fn = src.split("function _dvTickFmt", 1)[1].split("\n}", 1)[0]
    assert "step >= 1 ? 0" in fn


def test_a_pivot_label_keeps_more_precision_than_a_tick():
    # A tick may round to $190; the pivot it marks is $188.40 and must say so,
    # or the label contradicts the point it is attached to.
    src = _js()
    fn = src.split("function _dvValFmt", 1)[1].split("\n}", 1)[0]
    assert "n >= 1 ? 2" in fn and "0.01 ? 5" in fn


def test_the_ticks_are_round_numbers():
    src = _js()
    fn = src.split("function _dvTicks", 1)[1].split("\n}", 1)[0]
    assert "[1, 2, 2.5, 5, 10]" in fn, "steps must snap to human-readable values"
    assert "Math.log10" in fn


def test_the_time_axis_is_dated():
    # Two pivot dates alone leave the rest of the axis unreadable.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "dateTicks" in fn and "_dvDateTicks(px.map(d => d.t)" in fn


def test_date_ticks_snap_to_candles_that_exist():
    # "Nice" numbers in epoch MILLISECONDS are not nice dates — a round number
    # of milliseconds lands on an arbitrary instant.
    src = _js()
    fn = src.split("function _dvDateTicks", 1)[1].split("\n}", 1)[0]
    assert "times[Math.round(" in fn, "ticks must be sampled from real candles"
    assert "new Set(out)" in fn, "a short window must not repeat a date"


def test_the_rsi_axis_shows_the_midline_too():
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "grid(R.y(50), '50'" in fn


def test_the_two_pivots_are_tied_across_both_panels():
    # A vertical through both panels is what shows the price pivot and the RSI
    # pivot are the same instant — which is the entire claim being made.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "stem(p.prev.timestamp)" in fn and "stem(p.curr.timestamp)" in fn


def test_the_move_on_each_leg_is_stated():
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "% price" in fn and "pts RSI" in fn


def test_the_gradient_id_is_unique_per_render():
    # A hard-coded id collides when two SVGs share a document, and the second
    # one silently borrows the first one's fill.
    src = _js()
    fn = src.split("function buildDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "const uid = " in fn and "url(#${uid})" in fn
