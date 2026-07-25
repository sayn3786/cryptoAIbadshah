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


def test_falling_wedge_bullish():
    f = _one(_zigzag([100, 106, 98, 103, 96, 101, 95, 100], tail=[101, 104, 107]))
    assert f and f["type"] == "falling_wedge" and f["direction"] == "bullish"


def test_wrong_way_break_drops_directional_pattern():
    # An ascending triangle (bullish) that breaks DOWN through support is dropped.
    f = _one(_zigzag([90, 100, 92, 100, 94, 100, 96, 100, 98], tail=[95, 90, 86]))
    assert f is None or f["type"] != "ascending_triangle"


def test_non_converging_is_ignored():
    # Parallel rails (a channel, gap not narrowing) → not a triangle/wedge.
    ch = _zigzag([90, 100, 90, 100, 90, 100, 90, 100])
    assert detect_triangles_wedges(ch, "1D") == []


def test_too_few_pivots_empty():
    assert detect_triangles_wedges([_bar(i, 100) for i in range(20)], "1D") == []
