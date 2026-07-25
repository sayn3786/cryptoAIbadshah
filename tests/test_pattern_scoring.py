"""
Confirmed reversal / triangle / wedge patterns contribute directional points to
the signal (into the 'pattern' confluence group) — but ONLY when confirmed AND
the breakout is fresh. Forming or stale patterns score zero.

Synthetic analysis; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import generate_signal                                      # noqa: E402
from test_flag_pattern_correctness import _neutral_analysis, _make_candles, STEP, T0  # noqa: E402


def _analysis_with(reversals=None, triangles=None):
    a = _neutral_analysis()
    a["timeframe"] = "1D"
    a["reversal_patterns"] = reversals or []
    a["triangle_patterns"] = triangles or []
    return a


def _last_ts(a):
    return a["candles"][-1]["timestamp"]


def _stale_ts(a):
    return a["candles"][0]["timestamp"]     # far outside PATTERN_FRESH_BARS


def _rev(direction="bearish", confirmed=True, break_ts=None, type_="double_top",
         label="Double Top", target=90.0):
    return {"type": type_, "label": label, "direction": direction,
            "timeframe": "1D", "confirmed": confirmed, "break_ts": break_ts,
            "target": target}


def test_confirmed_fresh_reversal_scores():
    base = generate_signal(_analysis_with())
    a = _analysis_with(reversals=[_rev(direction="bearish")])
    a["reversal_patterns"][0]["break_ts"] = _last_ts(a)
    sig = generate_signal(a)
    assert sig["score"] < base["score"], "confirmed fresh bearish reversal must subtract"
    assert any("double top" in r.lower() for r in sig["bearish_reasons"])


def test_forming_reversal_scores_nothing():
    base = generate_signal(_analysis_with())
    a = _analysis_with(reversals=[_rev(confirmed=False)])
    a["reversal_patterns"][0]["break_ts"] = _last_ts(a)
    assert generate_signal(a)["score"] == base["score"]


def test_stale_confirmation_scores_nothing():
    base = generate_signal(_analysis_with())
    a = _analysis_with(reversals=[_rev(direction="bearish")])
    a["reversal_patterns"][0]["break_ts"] = _stale_ts(a)     # break long ago
    assert generate_signal(a)["score"] == base["score"]


def _hs_only():
    a = _analysis_with(reversals=[_rev(direction="bullish", type_="inverse_head_shoulders",
                                       label="Inverse Head & Shoulders", target=120.0)])
    a["reversal_patterns"][0]["break_ts"] = _last_ts(a)
    return a


def test_reversal_and_triangle_same_direction_dedupe():
    # H&S + a same-direction triangle both confirmed & fresh → only ONE bullish
    # pattern bucket applies (dedup by direction); the triangle does not stack.
    both = _hs_only()
    both["triangle_patterns"] = [{"type": "ascending_triangle", "label": "Ascending Triangle",
                                  "direction": "bullish", "timeframe": "1D", "confirmed": True,
                                  "target": 115.0, "break_ts": _last_ts(both)}]
    base    = generate_signal(_analysis_with())
    hs_sig  = generate_signal(_hs_only())
    both_sig = generate_signal(both)
    assert both_sig["score"] > base["score"]
    assert both_sig["score"] == hs_sig["score"], "triangle must not stack on the H&S (dedup)"
    hits = [r for r in both_sig["bullish_reasons"] if "confirmed on" in r]
    assert len(hits) == 1 and "inverse head" in hits[0].lower()


def test_triangle_breakout_scores_when_no_reversal():
    a = _analysis_with(triangles=[{
        "type": "falling_wedge", "label": "Falling Wedge", "direction": "bullish",
        "timeframe": "1D", "confirmed": True, "target": 130.0, "break_ts": None}])
    a["triangle_patterns"][0]["break_ts"] = _last_ts(a)
    base = generate_signal(_analysis_with())
    sig = generate_signal(a)
    assert sig["score"] > base["score"]
    assert any("falling wedge" in r.lower() for r in sig["bullish_reasons"])


def test_reversal_outweighs_triangle():
    # A confirmed reversal (18) should move the score more than a confirmed
    # triangle (12), all else equal.
    base = generate_signal(_analysis_with())
    hs   = generate_signal(_hs_only())
    tri_a = _analysis_with(triangles=[{"type": "falling_wedge", "label": "Falling Wedge",
                                       "direction": "bullish", "timeframe": "1D",
                                       "confirmed": True, "target": 130.0, "break_ts": None}])
    tri_a["triangle_patterns"][0]["break_ts"] = _last_ts(tri_a)
    tri = generate_signal(tri_a)
    assert (hs["score"] - base["score"]) > (tri["score"] - base["score"]) > 0
