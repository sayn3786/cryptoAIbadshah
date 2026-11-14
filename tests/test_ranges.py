"""Range boxes and deviations: sideways stretches found from closed candles,
where price is now, and what the latest close means."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import ranges as rg                                                    # noqa: E402

D = 86_400_000


def bar(i, o, c, h=None, lo=None):
    return {"timestamp": i * D, "open": o, "close": c,
            "high": h if h is not None else max(o, c) + 1, "low": lo if lo is not None else min(o, c) - 1}


def trend_then_range(after):
    """30 candles rising 5 a day (ATR ~ 6), 20 sideways between 200 and 210,
    then `after` = list of closes."""
    bars, p = [], 50.0
    for i in range(30):
        bars.append(bar(i, p, p + 5)); p += 5
    for k in range(20):
        o, c = (202, 208) if k % 2 == 0 else (208, 202)
        bars.append(bar(30 + k, o, c))
    prev = bars[-1]["close"]
    for k, c in enumerate(after):
        bars.append(bar(50 + k, prev, c)); prev = c
    return bars


def test_finds_the_sideways_stretch():
    found = rg.find_ranges(trend_then_range([]))
    assert found and found[-1]["top"] == 208 and found[-1]["bottom"] == 202
    assert found[-1]["i1"] - found[-1]["i0"] + 1 >= 12


def test_a_deviation_below_is_confirmed_on_the_close_back_inside():
    b = rg.boxes(trend_then_range([200, 196, 195, 204]))[-1]
    assert b["event"] == "deviation_below" and b["event_ago"] == 0 and b["outside_candles"] == 3
    assert b["position"] == "inside" and "deviation confirmed" in b["text"]
    r = rg.reads(trend_then_range([200, 196, 195, 204]), "1D")
    assert r and r[0]["direction"] == "bullish" and r[0]["context"]
    assert r[0]["label"].startswith("Deviation below the 202–208 range confirmed")


def test_a_break_and_a_long_absence_are_not_deviations():
    brk = rg.boxes(trend_then_range([215]))[-1]
    assert brk["event"] == "broke_above" and brk["position"] == "above"
    away = rg.boxes(trend_then_range([215, 220, 225, 230, 228, 226, 224, 220, 207]))[-1]
    assert away["event"] == "back_inside" and away["outside_candles"] == 8
    up = rg.boxes(trend_then_range([214, 205]))[-1]
    assert up["event"] == "deviation_above"
    assert rg.reads(trend_then_range([214, 205]), "1W")[0]["direction"] == "bearish"
    assert rg.reads(trend_then_range([215]), "1D") == []            # a plain break: chart only


def test_a_range_still_going_has_no_event_and_says_so():
    b = rg.boxes(trend_then_range([205]))[-1]
    assert b["current"] and b["event"] is None and "still in this range" in b["text"]
    assert rg.reads(trend_then_range([205]), "1D") == []


def test_every_analysis_carries_its_boxes():
    import candle_analysis
    assert "range_boxes" in candle_analysis.CANDLE_DERIVED_KEYS
    assert candle_analysis._range_boxes(trend_then_range([200, 196, 204]))[-1]["event"] == "deviation_below"


def test_the_dashboard_draws_and_explains_them():
    root = os.path.join(os.path.dirname(__file__), "..", "dashboard")
    js = open(os.path.join(root, "js", "dashboard.js")).read()
    html = open(os.path.join(root, "index.html")).read()
    assert "a.range_boxes" in js and "_renderRangeNotes(rbx)" in js and "'Deviation ✓'" in js
    assert 'id="rangeNotes"' in html and "What do these lines and boxes mean?" in html
    for term in ("Range box", "Deviation ✓", "Liquidity pools", "Structure High / Low",
                 "Support trendline", "SuperTrend", "CHoCH / BOS"):
        assert term in html, term
