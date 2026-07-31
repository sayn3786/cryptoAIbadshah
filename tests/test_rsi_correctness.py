"""
RSI: the maths, and the chart that displays it.

Reported as "our RSI doesn't match TradingView" — ours read 52.50 on BTC 1D
against TradingView's 44.42. The maths turned out to be right; the CHART was
showing the previous timeframe's series, because `renderRSIChart` returned early
on empty data without clearing. lightweight-charts labels the price scale with
each series' last value, so a stale series puts a stale NUMBER on screen too.

A number from a different timeframe, presented as the current one, is worse on a
trading dashboard than no number at all.
"""
import os
import random
import sys
from decimal import Decimal, getcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import calculate_rsi_series                          # noqa: E402


def _js():
    path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "js",
                        "dashboard.js")
    return open(path, encoding="utf-8").read()


# ── The maths is Wilder's, which is what TradingView uses ──────────────────

def _exact_wilder(closes, period=14):
    """Reference RSI in exact decimal arithmetic — no float drift to hide in."""
    getcontext().prec = 60
    c = [Decimal(str(x)) for x in closes]
    d = [c[i] - c[i - 1] for i in range(1, len(c))]
    gains = [x if x > 0 else Decimal(0) for x in d]
    losses = [-x if x < 0 else Decimal(0) for x in d]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out = [Decimal(100) - Decimal(100) / (1 + ag / al)]
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out.append(Decimal(100) - Decimal(100) / (1 + ag / al))
    return [float(x) for x in out]


STOCKCHARTS = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
               46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
               46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03,
               44.18, 44.22, 44.57, 43.42, 42.66, 43.13]


def test_rsi_matches_exact_wilder_smoothing():
    # Wilder's RMA is what TradingView's RSI uses, so matching it here is what
    # "agrees with TradingView" actually means.
    ours = [v for v in calculate_rsi_series(STOCKCHARTS, 14) if v is not None]
    ref = _exact_wilder(STOCKCHARTS, 14)
    assert len(ours) == len(ref)
    worst = max(abs(a - b) for a, b in zip(ours, ref))
    assert worst <= 0.01, f"diverges from Wilder by {worst}"


def test_rsi_is_not_a_simple_moving_average():
    # The classic wrong implementation: re-averaging the last N gains each bar
    # instead of smoothing. It agrees at the seed and drifts after, so a test
    # that only checks the first value would not catch it.
    closes = STOCKCHARTS
    period = 14
    d = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    g = [x if x > 0 else 0.0 for x in d]
    l = [-x if x < 0 else 0.0 for x in d]
    sma_rsi = []
    for i in range(period, len(d) + 1):
        ag = sum(g[i - period:i]) / period
        al = sum(l[i - period:i]) / period
        sma_rsi.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    ours = [v for v in calculate_rsi_series(closes, period) if v is not None]
    assert abs(ours[-1] - sma_rsi[-1]) > 0.5, \
        "our RSI is indistinguishable from the SMA variant — smoothing is wrong"


def test_the_warmup_window_is_long_enough_to_have_converged():
    # Wilder's smoothing has unbounded memory, so a short seed gives a slightly
    # different answer than a long one. At the 240 candles the app fetches, that
    # difference must be nil — otherwise every value is quietly a bit off.
    random.seed(11)
    s = [100.0]
    for _ in range(3000):
        s.append(max(1.0, s[-1] * (1 + random.gauss(0, 0.02))))
    full = calculate_rsi_series(s, 14)[-1]
    seeded = calculate_rsi_series(s[-240:], 14)[-1]
    assert abs(full - seeded) < 0.01, f"240-bar seed drifts {abs(full-seeded)}"


@pytest.mark.parametrize("closes,expected", [
    ([1.0] * 40, 50.0),                       # flat feed is not 100 or 0
    ([float(i) for i in range(1, 41)], 100.0),  # only gains
])
def test_degenerate_feeds(closes, expected):
    assert calculate_rsi_series(closes, 14)[-1] == expected


def test_only_losses_reads_zero():
    assert calculate_rsi_series([float(i) for i in range(40, 0, -1)], 14)[-1] == 0.0


def test_too_short_a_series_yields_no_values_rather_than_a_guess():
    assert calculate_rsi_series([1.0, 2.0, 3.0], 14) == [None, None, None]


def test_values_stay_in_range():
    random.seed(3)
    s = [100.0]
    for _ in range(500):
        s.append(max(0.01, s[-1] * (1 + random.gauss(0, 0.05))))
    for v in calculate_rsi_series(s, 14):
        if v is not None:
            assert 0.0 <= v <= 100.0


# ── The chart must never show another timeframe's RSI ──────────────────────

def test_no_data_clears_the_chart_instead_of_leaving_the_old_one():
    # THE BUG: `if (!rsiSeries?.length ...) return;` left the previous
    # timeframe's series rendered, and with it the price-scale label showing a
    # stale RSI value over a stale date axis.
    src = _js()
    fn = src.split("function renderRSIChart", 1)[1].split("\n}", 1)[0]
    assert "clearRSIChart()" in fn, "empty data must clear, not return"
    assert "if (!rsiSeries?.length" not in fn, "the early return is the bug"


def test_the_chart_is_cleared_when_the_selection_changes():
    # A slow or failed request must show an empty panel, not the previous
    # symbol's or timeframe's RSI.
    src = _js()
    assert src.count("clearRSIChart();") >= 2
    for handler in ("S.symbol = btn.dataset.sym;", "S.timeframe = btn.dataset.tf;"):
        after = src.split(handler, 1)[1][:200]
        assert "clearRSIChart()" in after, f"no clear after {handler}"


def test_clearing_is_defined_and_safe_before_the_chart_exists():
    src = _js()
    fn = src.split("function clearRSIChart", 1)[1].split("\n}", 1)[0]
    assert "S.rsiSeries?.setData([])" in fn, "must tolerate a missing chart"
    assert "try" in fn


def test_rows_without_a_timestamp_are_dropped_not_plotted_at_epoch():
    # A null timestamp became Math.floor(null/1000) === 0 — a point in 1970 that
    # drags the whole time axis with it.
    src = _js()
    fn = src.split("function renderRSIChart", 1)[1].split("\n}", 1)[0]
    assert "d.timestamp != null" in fn


# ── Threshold lines must not drag the time axis ────────────────────────────
# The overbought/oversold lines were two-point line SERIES anchored at
# `Date.now()/1000 - 9e7`. Ninety million seconds is 2.85 years, so every RSI
# panel carried a hidden data point in 2023: the chart's time domain stretched
# back to it, and fitContent() framed three years to show thirty days. The stray
# "2023" on the axis was this, not the RSI data.

def test_the_thresholds_are_price_lines_not_data_series():
    src = _js()
    init = src.split("S.rsiSeries = S.rsiChart.addLineSeries", 1)[1].split("// CVD mini charts", 1)[0]
    assert init.count("createPriceLine") == 2, "70 and 30 must be price lines"
    assert "addLineSeries" not in init, \
        "a threshold drawn as a series puts a point on the time axis"


def _strip_comments(src):
    """Real code only — the comment explaining this bug names the old constant."""
    import re
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)     # block comments
    return "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())


def test_no_threshold_is_anchored_to_a_past_timestamp():
    code = _strip_comments(_js())
    assert "9e7" not in code, "the 2.85-year anchor must be gone from the code"
    assert "Date.now() / 1000 -" not in code and "Date.now()/1000 -" not in code


def test_the_stripper_actually_strips():
    # Otherwise the assertion above passes for the wrong reason.
    assert "9e7" in _js(), "the explanatory comment should still mention it"
    assert "keepme" in _strip_comments("var a = 'keepme'; /* 9e7 */ // 9e7")
    assert "9e7" not in _strip_comments("var a = 'keepme'; /* 9e7 */ // 9e7")


def test_the_thresholds_still_label_the_scale():
    # The 70 / 30 badges on the price scale came from those series' last values.
    # Price lines have to be asked for their label explicitly.
    src = _js()
    init = src.split("S.rsiSeries = S.rsiChart.addLineSeries", 1)[1].split("// CVD mini charts", 1)[0]
    assert init.count("axisLabelVisible: true") == 2
    assert "price: 70" in init and "price: 30" in init
