"""
Triangle & wedge detection from converging swing-high / swing-low trendlines:
ascending / descending / symmetrical triangles and rising / falling wedges.
Confirmation = a close beyond a rail; lifecycle mirrors flags. Synthetic OHLC.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import detect_triangles_wedges                             # noqa: E402

T0, STEP = 1_000_000, 3_600_000


def _bar(i, p, pad=0.12):
    return {"timestamp": T0 + i * STEP, "open": p, "high": p + pad, "low": p - pad, "close": p}


def _zigzag(extremes, ramp=4, tail=None):
    cs, i, prev = [], 0, extremes[0]
    cs.append(_bar(i, prev)); i += 1
    for v in extremes[1:]:
        for s in range(1, ramp + 1):
            cs.append(_bar(i, prev + (v - prev) * s / ramp)); i += 1
        prev = v
    for p in (tail or []):
        cs.append(_bar(i, p)); i += 1
    return cs


def _one(cs):
    r = detect_triangles_wedges(cs, "1D")
    return r[0] if r else None


def test_ascending_triangle_bullish():
    f = _one(_zigzag([90, 100, 92, 100, 94, 100, 96, 100, 98], tail=[101, 103, 105]))
    assert f and f["type"] == "ascending_triangle"
    assert f["direction"] == "bullish" and f["confirmed"] and f["target"] > f["upper_now"]


def test_descending_triangle_bearish():
    f = _one(_zigzag([100, 90, 97, 90, 94, 90, 92, 90], tail=[89, 87, 85]))
    assert f and f["type"] == "descending_triangle"
    assert f["direction"] == "bearish" and f["confirmed"] and f["target"] < f["lower_now"]


def test_symmetrical_triangle_neutral_then_resolves_on_break():
    forming = _one(_zigzag([90, 100, 92, 99, 93, 98, 94, 97, 95]))
    assert forming and forming["type"] == "symmetrical_triangle"
    assert forming["direction"] == "neutral" and forming["status"] == "forming"
    assert forming["target"] is None
    broke = _one(_zigzag([90, 100, 92, 99, 93, 98, 94, 97, 95], tail=[99, 101, 103]))
    assert broke["confirmed"] and broke["direction"] == "bullish" and broke["target"] is not None


def test_rising_wedge_bearish():
    f = _one(_zigzag([92, 100, 96, 102, 100, 104, 103, 106], tail=[101, 98, 95]))
    assert f and f["type"] == "rising_wedge" and f["direction"] == "bearish"


# A tight, clean falling wedge (highs fall fast, lows fall slowly → converging),
# whose swing-touch rails clear the convergence threshold. mid ≈ 96.1.
FALLING_WEDGE = [100, 112, 98, 106, 96, 101, 95, 97]


def test_falling_wedge_bullish():
    f = _one(_zigzag(FALLING_WEDGE, tail=[100, 104, 108]))
    assert f and f["type"] == "falling_wedge" and f["direction"] == "bullish"


def test_wrong_way_break_drops_directional_pattern():
    # An ascending triangle (bullish) that breaks DOWN through support is dropped.
    f = _one(_zigzag([90, 100, 92, 100, 94, 100, 96, 100, 98], tail=[95, 90, 86]))
    assert f is None or f["type"] != "ascending_triangle"


def test_wedge_breakout_retest_stays_confirmed():
    # Falling wedge breaks up, then pulls back to retest but HOLDS in the upper
    # half → still a valid confirmed breakout (the TAO 4H case).
    f = _one(_zigzag(FALLING_WEDGE, tail=[101, 104, 103]))
    assert f and f["type"] == "falling_wedge" and f["confirmed"]


def test_wedge_failed_breakout_dropped():
    # Breaks up, then collapses well BELOW the wedge → failed → dropped.
    f = _one(_zigzag(FALLING_WEDGE, tail=[101, 104, 90]))
    assert f is None or f["type"] != "falling_wedge"


def test_wedge_breakout_giveback_dropped_but_retest_survives():
    # Invalidation is PROPORTIONAL to the breakout move (plus a retest buffer),
    # not the wedge's geometric midline — a tall wedge shouldn't die to a routine
    # 2-3% retest while its target is far away (the TAO 1D case).
    # Giving back the whole move → dropped.
    dropped = _one(_zigzag(FALLING_WEDGE, tail=[101, 104, 90]))
    assert dropped is None or dropped["type"] != "falling_wedge"
    # A retest that holds within the move → still confirmed.
    for end in (103, 97, 95):
        held = _one(_zigzag(FALLING_WEDGE, tail=[101, 104, end]))
        assert held and held["type"] == "falling_wedge" and held["confirmed"], \
            f"a retest to {end} should survive"


def test_tall_wedge_survives_small_retest():
    # Regression for the TAO 1D wedge (upper ~203, lower ~187, target ~331): the
    # old midline rule sat ~2% under the breakout and killed it on a normal dip.
    # A tall structure must tolerate a modest retest.
    held = _one(_zigzag(FALLING_WEDGE, tail=[101, 104, 100]))
    assert held and held["type"] == "falling_wedge" and held["confirmed"]


def test_non_converging_is_ignored():
    # Parallel rails (a channel, gap not narrowing) → not a triangle/wedge.
    ch = _zigzag([90, 100, 90, 100, 90, 100, 90, 100])
    assert detect_triangles_wedges(ch, "1D") == []


def test_too_few_pivots_empty():
    assert detect_triangles_wedges([_bar(i, 100) for i in range(20)], "1D") == []


def _line_at(pts, ts):
    (t0, p0), (t1, p1) = (pts[0]["timestamp"], pts[0]["price"]), (pts[1]["timestamp"], pts[1]["price"])
    return p0 if t1 == t0 else p0 + (p1 - p0) * (ts - t0) / (t1 - t0)


def test_rails_are_envelopes_bounding_the_candles():
    # The drawn rails must actually CONTAIN the price — every candle in the wedge
    # window sits at/below the upper rail and at/above the lower rail (envelope),
    # not a mean line that leaves half the candles outside.
    cs = _zigzag(FALLING_WEDGE, tail=[101, 104, 103])
    f = _one(cs)
    assert f and f["upper_line"] and f["lower_line"]
    ul, ll = f["upper_line"], f["lower_line"]
    t0, t1 = ul[0]["timestamp"], ul[1]["timestamp"]
    window = [c for c in cs if t0 <= c["timestamp"] <= t1]
    assert len(window) >= 5
    assert all(c["high"] <= _line_at(ul, c["timestamp"]) + 1e-6 for c in window), "a high pokes above the upper rail"
    assert all(c["low"]  >= _line_at(ll, c["timestamp"]) - 1e-6 for c in window), "a low pokes below the lower rail"


def test_drawable_rail_geometry_present():
    # The chart overlay needs two rail endpoints (start pivot → end of structure).
    f = _one(_zigzag([90, 100, 92, 100, 94, 100, 96, 100, 98], tail=[101, 103, 105]))
    for key in ("upper_line", "lower_line"):
        assert key in f and len(f[key]) == 2
        for pt in f[key]:
            assert "timestamp" in pt and "price" in pt
    # rails are ordered in time (start before end)
    assert f["upper_line"][0]["timestamp"] < f["upper_line"][1]["timestamp"]
