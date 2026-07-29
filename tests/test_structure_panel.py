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
