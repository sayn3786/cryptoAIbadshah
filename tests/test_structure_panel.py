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
