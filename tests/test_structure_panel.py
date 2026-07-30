"""
Market-structure status panel — the dense trend/structure/liquidity table.
Built entirely from data already in `analysis`; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import build_structure_panel                               # noqa: E402
from test_flag_pattern_correctness import _make_candles                  # noqa: E402


def _analysis(up=True, **over):
    cs = _make_candles(60, up=up)
    a = {"candles": cs, "symbol": "TAO", "timeframe": "1D",
         "ema_trend": {"above": [50, 200] if up else [], "below": [] if up else [50, 200],
                       "ema50": cs[-1]["close"] * (0.98 if up else 1.02)},
         "supertrend": {"direction": "bullish" if up else "bearish"},
         "signal": {"direction": "LONG" if up else "SHORT", "strength": 62}}
    a.update(over)
    return a


def _rows(p):
    return {r["label"]: r for r in p["rows"]}


def test_panel_reports_trend_state_and_power():
    r = _rows(build_structure_panel(_analysis(up=True)))
    assert r["Trend State"]["value"] == "BULLISH" and r["Trend State"]["tone"] == "bull"
    assert r["Trend Power"]["value"].endswith("%")

    rb = _rows(build_structure_panel(_analysis(up=False)))
    assert rb["Trend State"]["value"] == "BEARISH" and rb["Trend State"]["tone"] == "bear"


def test_structure_event_and_alignment_from_choch():
    a = _analysis(up=True, choch={"signal": "bullish", "level": 101.5, "candles_ago": 4})
    r = _rows(build_structure_panel(a))
    assert r["Structure Bias"]["value"] == "BULLISH"
    assert "CHoCH" in r["Last Structure Event"]["value"] and "4 bars ago" in r["Last Structure Event"]["value"]
    assert r["Alignment"]["value"] == "ALIGNED"

    # bearish structure inside a bullish trend → conflicted
    a2 = _analysis(up=True, choch={"signal": "bearish", "level": 99.0, "candles_ago": 2})
    assert _rows(build_structure_panel(a2))["Alignment"]["value"] == "CONFLICTED"


def test_liquidity_rows_use_equal_levels():
    cs = _make_candles(60, up=True)
    px = cs[-1]["close"]
    a = _analysis(up=True, equal_levels={"eqh": {"price": px * 1.04, "touches": 3},
                                         "eql": {"price": px * 0.95, "touches": 2}})
    r = _rows(build_structure_panel(a))
    assert "touches" in r["Liquidity Above"]["detail"]
    assert r["Liquidity Above"]["tone"] == "bear"    # stops above = sell-side draw
    assert r["Liquidity Below"]["tone"] == "bull"


def test_missing_liquidity_is_blank_not_an_error():
    r = _rows(build_structure_panel(_analysis()))
    assert r["Liquidity Above"]["value"] == "—"


def test_range_position_and_midline_stretch_present():
    r = _rows(build_structure_panel(_analysis()))
    assert r["Range Position"]["value"].split()[0] in ("UPPER", "MIDDLE", "LOWER")
    assert r["Midline Stretch"]["value"].endswith("%")


def test_too_few_candles_returns_none():
    assert build_structure_panel({"candles": _make_candles(5)}) is None
    assert build_structure_panel({"candles": []}) is None


# ── BOS streak ──────────────────────────────────────────────────────────────
from patterns import detect_bos_streak, session_ranges                   # noqa: E402
from test_triangle_wedge_patterns import _zigzag                         # noqa: E402


def test_bos_streak_counts_same_direction_breaks():
    up = detect_bos_streak(_zigzag([100, 110, 106, 118, 114, 126, 122, 134]))
    assert up["direction"] == "bullish" and up["count"] >= 2
    dn = detect_bos_streak(_zigzag([140, 128, 132, 120, 124, 112, 116, 104]))
    assert dn["direction"] == "bearish" and dn["count"] >= 2


def test_bos_streak_empty_without_structure():
    assert detect_bos_streak([])["direction"] is None
    assert detect_bos_streak(_make_candles(6))["count"] == 0


def test_panel_shows_bos_streak():
    cs = _zigzag([100, 110, 106, 118, 114, 126, 122, 134])
    a = _analysis(up=True)
    a["candles"] = cs
    a["bos_streak"] = detect_bos_streak(cs)
    r = _rows(build_structure_panel(a))
    assert "BULLISH" in r["BOS Streak"]["value"] and r["BOS Streak"]["tone"] == "bull"


# ── session ranges ──────────────────────────────────────────────────────────
def _hourly(n=60, base=1_700_000_000_000):
    return [{"timestamp": base + i * 3600_000, "open": 100,
             "high": 100 + (i % 7), "low": 99 - (i % 5), "close": 100} for i in range(n)]


def test_session_ranges_on_intraday_only():
    assert session_ranges(_hourly(), "1H"), "intraday should produce session boxes"
    assert session_ranges(_hourly(), "1D") == [], "daily+ spans every session — no boxes"
    assert session_ranges(_hourly(), "1W") == []


def test_session_ranges_have_high_low_and_names():
    out = session_ranges(_hourly(), "1H")
    assert all(b["high"] >= b["low"] for b in out)
    assert all(b["session"] in ("ASIA", "LONDON", "US") for b in out)
    assert all(b["end_ts"] >= b["start_ts"] for b in out)


def test_panel_attaches_sessions_and_position():
    cs = _hourly()
    a = _analysis(up=True)
    a["candles"] = cs
    a["timeframe"] = "1H"
    a["session_ranges"] = session_ranges(cs, "1H")
    p = build_structure_panel(a)
    assert p["sessions"], "sessions should ride along for the chart overlay"
    r = _rows(p)
    assert r["Session Position"]["value"].endswith("%")


# ── BOS staleness / validity ────────────────────────────────────────────────
def test_bos_reports_held_and_bars_ago():
    cs = _zigzag([100, 110, 106, 118, 114, 126, 122, 134])
    r = detect_bos_streak(cs)
    assert r["held"] is True                      # price still above the break
    assert r["bars_ago"] is not None


def test_given_back_bos_is_flagged_and_neutral():
    # The BTC case: a bullish BOS whose level price has since slipped back under,
    # without a swing low being taken out. It must NOT read as a live bullish
    # signal against a bearish trend — it is stale context.
    from test_triangle_wedge_patterns import _bar
    up = _zigzag([100, 110, 106, 118, 114, 126, 122, 134])
    given = up + [_bar(len(up) + i, p) for i, p in enumerate([128, 126, 124.5, 124, 123.5])]
    r = detect_bos_streak(given)
    assert r["direction"] == "bullish" and r["held"] is False

    a = _analysis(up=False)
    a["candles"] = given
    a["bos_streak"] = r
    row = _rows(build_structure_panel(a))["BOS Streak"]
    assert "given back" in row["value"]
    assert row["tone"] == "neutral", "a given-back break must not read as live bullish"
    assert "bars ago" in row["detail"]


def test_structure_rows_name_their_lookback_window():
    # 1D and 1W legitimately disagree on Range Position because the window is
    # 30 BARS of that timeframe. Stating the window makes that scale difference
    # readable instead of looking like a contradiction.
    from patterns import STRUCTURE_WINDOW_BARS
    r = _rows(build_structure_panel(_analysis()))
    assert f"{STRUCTURE_WINDOW_BARS} bars" in r["Structure High"]["detail"]
    assert f"{STRUCTURE_WINDOW_BARS} bars" in r["Structure Low"]["detail"]
    assert "last" in r["Range Position"]["detail"]


# ── pool distance / filter window / fired-filtered ──────────────────────────
def _with_pools(up=True, **over):
    a = _analysis(up=up)
    px = a["candles"][-1]["close"]
    a["equal_levels"] = {"eqh": {"price": px * 1.01, "touches": 5},
                         "eql": {"price": px * 0.985, "touches": 5}}
    a.update(over)
    return a


def test_pool_distance_reported_in_atr():
    r = _rows(build_structure_panel(_with_pools()))["Pool Distance"]
    assert "ATR" in r["value"] and "up" in r["value"] and "dn" in r["value"]
    assert "nearest" in r["detail"]


def test_pool_distance_blank_without_pools():
    assert _rows(build_structure_panel(_analysis()))["Pool Distance"]["value"] == "—"


def test_filter_window_states():
    # with-trend long
    r = _rows(build_structure_panel(_with_pools(up=True, tradeable=True)))["Filter Window"]
    assert r["value"] == "BULL OPEN" and "with trend" in r["detail"]
    # counter-trend is allowed but marked
    a = _with_pools(up=False, tradeable=True)
    a["signal"] = {"direction": "LONG", "strength": 20}
    assert "counter-trend" in _rows(build_structure_panel(a))["Filter Window"]["detail"]
    # gated by data quality
    a2 = _with_pools(tradeable=False)
    assert _rows(build_structure_panel(a2))["Filter Window"]["value"] == "CLOSED"
    # no directional setup
    a3 = _with_pools(tradeable=True)
    a3["signal"] = {"direction": "NEUTRAL", "strength": 0}
    assert _rows(build_structure_panel(a3))["Filter Window"]["value"] == "FLAT"


def test_fired_filtered_counts_from_radar():
    a = _with_pools(reversal_radar={"mode": "top", "count": 2, "applicable": 13})
    r = _rows(build_structure_panel(a))["Fired / Filtered Out"]
    assert r["value"] == "2 / 13" and "11 filtered" in r["detail"]
    assert _rows(build_structure_panel(_with_pools()))["Fired / Filtered Out"]["value"] == "—"


# ── liquidity pool ladder ───────────────────────────────────────────────────
def test_liquidity_pools_cluster_and_rank():
    from patterns import detect_liquidity_pools, LIQ_MIN_TOUCHES
    cs = _zigzag([100, 110, 100, 110, 100, 110, 102, 109, 101])
    pools = detect_liquidity_pools(cs)
    assert pools, "repeated highs/lows should form pools"
    assert all(p["touches"] >= LIQ_MIN_TOUCHES for p in pools)
    assert all(p["side"] in ("above", "below") for p in pools)
    # ranked strongest first
    assert pools == sorted(pools, key=lambda p: (p["touches"], p["last_ts"]), reverse=True)


def test_liquidity_pools_empty_on_thin_data():
    from patterns import detect_liquidity_pools
    assert detect_liquidity_pools([]) == []
    assert detect_liquidity_pools(_make_candles(5)) == []


# ── structure-chart overlay coverage ────────────────────────────────────────
def test_structure_supertrend_spans_the_whole_structure_window():
    # The structure chart draws STRUCTURE_CHART_BARS candles. Its SuperTrend
    # overlay must cover the SAME span — reusing the main chart's 60-bar series
    # left the older two thirds of the pane with no line and no regime shading.
    from app import STRUCTURE_CHART_BARS
    from indicators import calculate_supertrend

    spot = _make_candles(STRUCTURE_CHART_BARS + 40, up=True)
    st = calculate_supertrend(spot)
    cutoff = spot[-STRUCTURE_CHART_BARS]["timestamp"]
    deep = [p for p in st["series"] if p["timestamp"] >= cutoff]

    candles = spot[-STRUCTURE_CHART_BARS:]
    assert deep, "structure supertrend series must not be empty"
    assert deep[0]["timestamp"] <= candles[0]["timestamp"] + (
        candles[1]["timestamp"] - candles[0]["timestamp"]), \
        "overlay must start at (or before) the first structure candle"
    assert len(deep) > 60, "must be deeper than the main chart's 60-bar window"


def test_structure_trendline_finds_the_rising_support():
    # The structure chart draws its own trendline over the deeper window. On a
    # steadily rising market that must produce a SUPPORT line with anchor/end
    # points to draw, otherwise the chart has nothing to render.
    from app import STRUCTURE_CHART_BARS
    from patterns import detect_trendline

    spot = _make_candles(STRUCTURE_CHART_BARS + 20, up=True)
    tl = detect_trendline(spot[-STRUCTURE_CHART_BARS:], window=3)
    assert tl, "a trending window must yield a trendline"

    sup = [tl[k] for k in ("macro", "local") if (tl.get(k) or {}).get("type") == "support"]
    assert sup, "a rising market must give at least one support line"
    for ln in sup:
        assert ln["anchor"]["timestamp"] < ln["end"]["timestamp"], "line must span forward in time"
        assert ln["touches"] >= 2, "a line is defined by at least its two anchors"
        assert ln["broken"] in (None, "up", "down")


def test_structure_trendline_spans_the_structure_window():
    # It must be built on the SAME window the structure chart draws — a line
    # anchored outside those candles would render as a stub at the right edge.
    from app import STRUCTURE_CHART_BARS
    from patterns import detect_trendline

    spot = _make_candles(STRUCTURE_CHART_BARS + 20, up=True)
    win = spot[-STRUCTURE_CHART_BARS:]
    tl = detect_trendline(win, window=3)
    lo, hi = win[0]["timestamp"], win[-1]["timestamp"]
    for k in ("macro", "local"):
        ln = tl.get(k)
        if not ln:
            continue
        assert lo <= ln["anchor"]["timestamp"] <= hi
        assert lo <= ln["end"]["timestamp"] <= hi


# ── Pool Distance must report the side a pool ACTUALLY sits on ───────────────
# Regression: an equal-HIGH that price had already traded above was still
# labelled "up 0.1 ATR · nearest: above", claiming resting stops overhead when
# price was already through them. It also disagreed with the confluence scorer,
# which correctly ignored the breached level.
def _pool_analysis(price, eqh=None, eql=None, spread=400.0, n=40):
    candles = []
    for i in range(n):
        mid = price
        candles.append({"timestamp": 1785000000000 + i * 7200000, "open": mid,
                        "high": mid + spread, "low": mid - spread, "close": mid,
                        "volume": 100})
    candles[-1].update(close=price, high=price + 10, low=price - 10)
    eq = {}
    if eqh is not None:
        eq["eqh"] = {"price": eqh, "touches": 10}
    if eql is not None:
        eq["eql"] = {"price": eql, "touches": 11}
    a = _analysis(up=True)
    a["candles"] = candles
    a["equal_levels"] = eq
    return a


def test_breached_equal_high_is_not_reported_as_overhead_liquidity():
    # The live BTC case: eqh 64197 sat BELOW price 64266.4.
    r = _rows(build_structure_panel(_pool_analysis(64266.4, eqh=64197.0, eql=63365.4)))
    pd = r["Pool Distance"]
    assert "up —" in pd["value"], f"nothing is above price, got {pd['value']!r}"
    assert "nearest: below" in pd["detail"]
    assert "breached" in pd["detail"], "should say the equal-high was already taken"
    assert pd["tone"] == "bull"


def test_pool_above_price_is_reported_as_above():
    r = _rows(build_structure_panel(_pool_analysis(64000.0, eqh=64400.0, eql=63000.0)))
    pd = r["Pool Distance"]
    assert "up " in pd["value"] and "up —" not in pd["value"]
    assert "dn " in pd["value"] and "dn —" not in pd["value"]
    assert "breached" not in pd["detail"], "eqh is genuinely overhead here"


def test_nearest_pool_picks_the_genuinely_closer_side():
    # eqh far above, eql just below -> nearest must be below.
    r = _rows(build_structure_panel(_pool_analysis(64000.0, eqh=68000.0, eql=63990.0)))
    assert "nearest: below" in r["Pool Distance"]["detail"]
    # Mirror.
    r2 = _rows(build_structure_panel(_pool_analysis(64000.0, eqh=64010.0, eql=60000.0)))
    assert "nearest: above" in r2["Pool Distance"]["detail"]


def test_pool_distance_blank_when_no_levels():
    assert _rows(build_structure_panel(_pool_analysis(64000.0)))["Pool Distance"]["value"] == "—"


# ── Trend State must weight structural EMAs above short-term ────────────────
# Regression: above EMA7/21 and below EMA50/200 cancelled out, so SuperTrend
# alone tipped it to "BULLISH" on a chart whose structural trend was bearish
# and whose published signal was SHORT.
def _trend_analysis(above, below, supertrend):
    a = _analysis(up=True)
    a["ema_trend"] = {"above": above, "below": below,
                      "trend": "bearish" if below else "bullish"}
    a["supertrend"] = {"direction": supertrend}
    return a


def test_structural_emas_outweigh_short_term_emas():
    # The live BTC case: above EMA7/21, below EMA50/200, SuperTrend bullish.
    r = _rows(build_structure_panel(_trend_analysis([7, 21], [50, 200], "bullish")))
    assert r["Trend State"]["value"] == "BEARISH", \
        "below EMA50/200 must outweigh above EMA7/21 plus SuperTrend"
    assert r["Trend State"]["tone"] == "bear"


def test_mirror_case_short_term_bearish_but_structurally_bullish():
    r = _rows(build_structure_panel(_trend_analysis([50, 200], [7, 21], "bearish")))
    assert r["Trend State"]["value"] == "BULLISH"


def test_full_agreement_still_reads_cleanly():
    r = _rows(build_structure_panel(_trend_analysis([7, 21, 50, 200], [], "bullish")))
    assert r["Trend State"]["value"] == "BULLISH"
    assert r["Trend Power"]["value"] == "100%"
    rb = _rows(build_structure_panel(_trend_analysis([], [7, 21, 50, 200], "bearish")))
    assert rb["Trend State"]["value"] == "BEARISH"
    assert rb["Trend Power"]["value"] == "100%"


def test_trend_state_names_its_evidence():
    d = _rows(build_structure_panel(
        _trend_analysis([7, 21], [50, 200], "bullish")))["Trend State"]["detail"]
    assert "above EMA7/EMA21" in d
    assert "below EMA50/EMA200" in d
    assert "SuperTrend bullish" in d, "a mixed read must be readable, not just a label"
