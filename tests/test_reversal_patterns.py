"""
Reversal-pattern detection: Double Top/Bottom (+triple) and Head & Shoulders /
Inverse H&S. Confirmation is a CLOSE beyond the neckline; the lifecycle mirrors
flags (forming / confirmed / invalidated). Synthetic OHLC; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import detect_reversals                                    # noqa: E402

T0, STEP = 1_000_000, 3_600_000


def _series(points, pad=0.15):
    return [{"timestamp": T0 + i * STEP, "open": p, "high": p + pad,
             "low": p - pad, "close": p} for i, p in enumerate(points)]


def _of(rev, *types):
    return [p for p in rev if p["type"] in types]


# ── Double Top ──────────────────────────────────────────────────────────────
DT_BASE = [80, 84, 88, 92, 96, 100, 97, 94, 91, 90, 92, 95, 98, 100.4]


def test_double_top_confirmed():
    cs = _series(DT_BASE + [97, 93, 89, 87])          # closes below the neckline
    dt = _of(detect_reversals(cs, "1D"), "double_top")
    assert dt, "expected a double top"
    f = dt[0]
    assert f["direction"] == "bearish"
    assert f["status"] == "confirmed" and f["confirmed"] is True
    assert f["target"] < f["neckline"] < f["peak_level"]


def test_double_top_forming():
    cs = _series(DT_BASE + [99, 98.5, 99])            # holds above the neckline
    f = _of(detect_reversals(cs, "1D"), "double_top")[0]
    assert f["status"] == "forming" and f["confirmed"] is False


def test_double_top_failed_breakout_is_recorded_not_confirmed():
    # Confirms a breakdown below the neckline, then RECLAIMS it (closes back
    # above) → FAILED. Kept as a traceable record, but never as a confirmed
    # signal (scoring/alerts gate on `confirmed`).
    cs = _series(DT_BASE + [97, 93, 89, 95])
    tops = _of(detect_reversals(cs, "1D"), "double_top", "triple_top")
    assert tops, "the failure should be recorded, not dropped"
    f = tops[0]
    assert f["status"] == "failed" and f["confirmed"] is False
    assert f["failed_ts"] is not None and f["failure_reason"]


def test_double_top_invalidated_is_dropped():
    cs = _series(DT_BASE + [101, 102, 103])           # breaks above the tops
    assert not _of(detect_reversals(cs, "1D"), "double_top", "triple_top")


# ── Double Bottom ───────────────────────────────────────────────────────────
def test_double_bottom_confirmed():
    cs = _series([110, 106, 102, 98, 94, 90, 93, 96, 99, 100,
                  97, 94, 92, 89.7, 93, 97, 101, 103])
    f = _of(detect_reversals(cs, "1D"), "double_bottom")[0]
    assert f["direction"] == "bullish"
    assert f["status"] == "confirmed"
    assert f["target"] > f["neckline"] > f["peak_level"]   # peak_level = trough level here


# ── Head & Shoulders ────────────────────────────────────────────────────────
def test_head_shoulders_confirmed():
    cs = _series([80, 86, 92, 96, 100, 97, 94, 92, 96, 102, 108, 110,
                  106, 100, 94, 92.5, 96, 99, 100.5, 97, 93, 90, 88])
    f = _of(detect_reversals(cs, "1D"), "head_shoulders")[0]
    assert f["direction"] == "bearish"
    assert f["status"] == "confirmed"
    assert f["head_level"] > f["neckline"] > f["target"]
    roles = [p["role"] for p in f["points"]]
    assert roles == ["left_shoulder", "head", "right_shoulder"]


def test_inverse_head_shoulders_confirmed():
    cs = _series([120, 114, 108, 104, 100, 103, 106, 108, 104, 98, 92, 90,
                  94, 100, 106, 107.5, 104, 101, 99.5, 103, 107, 110, 112])
    f = _of(detect_reversals(cs, "1D"), "inverse_head_shoulders")[0]
    assert f["direction"] == "bullish"
    assert f["status"] == "confirmed"
    assert f["target"] > f["neckline"] > f["head_level"]   # head is the low here


# ── Robustness ──────────────────────────────────────────────────────────────
def test_too_few_candles_is_empty():
    assert detect_reversals(_series([1, 2, 3, 4]), "1D") == []


def test_unequal_tops_not_a_double_top():
    # second top 20% higher than the first → not "equal" → no double top
    cs = _series([80, 88, 96, 100, 96, 92, 90, 94, 100, 108, 120, 110, 100])
    assert not _of(detect_reversals(cs, "1D"), "double_top", "triple_top")
