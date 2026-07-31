"""
A pattern must not un-break itself.

Reported from the dashboard: a falling wedge broke out on 26 Jul, retested the
next day, and days later the card was back to "Forming — awaiting a break above
the rail". The wedge had not un-broken. The breakout candle became a swing pivot,
the upper rail was refitted THROUGH it, and the breakout scan — which starts
after the last pivot — no longer covered the bar that broke.

A candle cannot be part of the boundary it broke. These tests pin that.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns as P                                                 # noqa: E402


DAY = 86_400_000


def _c(ts, high, low, close):
    return {"timestamp": ts, "open": close, "high": high, "low": low,
            "close": close, "volume": 100}


def _falling_wedge(bars=44):
    """Both rails falling and converging, with real swings on each rail."""
    out, hi, lo = [], 300.0, 250.0
    for i in range(bars):
        hi -= 2.2
        lo -= 1.2
        if i % 4 == 0:
            h, l, cl = hi, hi - 6, hi - 3          # tags the upper rail
        elif i % 4 == 2:
            h, l, cl = lo + 6, lo, lo + 3          # tags the lower rail
        else:
            mid = (hi + lo) / 2
            h, l, cl = mid + 3, mid - 3, mid
        out.append(_c(1_700_000_000_000 + i * DAY, h, l, cl))
    return out


def _rails(candles):
    ph, pl = P.find_pivots(candles, window=P.TW_PIVOT_WINDOW)
    h_s, h_i, l_s, l_i = P._fit_rails(ph[-4:], pl[-4:], candles)
    return (lambda i: h_s * i + h_i), (lambda i: l_s * i + l_i)


def _break_then_return(extra_bars):
    """
    Wedge → decisive close above the upper rail → price back inside, continuing
    to oscillate between the rails for `extra_bars` more candles.
    """
    candles = _falling_wedge()
    up, low = _rails(candles)
    brk_i = len(candles)

    def add(h, l, cl):
        candles.append(_c(candles[-1]["timestamp"] + DAY, h, l, cl))

    add(up(brk_i) * 1.06, up(brk_i) * 0.99, up(brk_i) * 1.04)   # the breakout
    for i in range(brk_i + 1, brk_i + 1 + extra_bars):
        u, d = up(i), low(i)
        mid = (u + d) / 2
        if i % 2 == 0:
            add(u * 0.999, mid, (u + mid) / 2)      # back inside, tags the rail
        else:
            add(mid, d * 1.001, (d + mid) / 2)
    return candles, brk_i


# ── The reported regression ─────────────────────────────────────────────────

def test_a_broken_wedge_never_reverts_to_forming():
    """
    THE bug. Two candles after the breakout the card read 'failed'; two candles
    later it read 'forming' again, inviting a trade on a break that had already
    happened and failed.
    """
    seen = []
    for extra in range(1, 8):
        candles, _ = _break_then_return(extra)
        for p in P.detect_triangles_wedges(candles, "1D"):
            seen.append((extra, p["status"]))

    assert seen, "the scenario produced no pattern at all — the test is not exercising it"
    assert not any(status == "forming" for _, status in seen), (
        f"a wedge that broke out reverted to 'forming': {seen}")


def test_the_breakout_is_still_reported_after_it_happens():
    # The opposite failure mode: suppressing the pattern entirely would also
    # remove the 'forming' status, and would be just as wrong.
    candles, _ = _break_then_return(1)
    got = P.detect_triangles_wedges(candles, "1D")
    assert got, "the pattern disappeared instead of reporting its breakout"
    assert got[0]["breakout_dir"] == "up"
    assert got[0]["status"] in ("confirmed", "failed")


# ── The mechanism ───────────────────────────────────────────────────────────

def test_the_breakout_candle_is_dropped_from_the_pivot_set():
    candles, brk_i = _break_then_return(4)
    ph, pl = P.find_pivots(candles, window=P.TW_PIVOT_WINDOW)
    hs, ls = ph[-4:], pl[-4:]
    assert brk_i in [p["index"] for p in hs], \
        "the breakout bar is not a pivot here — the test no longer covers the bug"

    kept_h, kept_l = P._peel_breakout_pivots(hs, ls, candles)
    assert brk_i not in [p["index"] for p in kept_h], \
        "a candle cannot be part of the boundary it broke"


def test_a_clean_wedge_keeps_every_pivot():
    candles = _falling_wedge()
    ph, pl = P.find_pivots(candles, window=P.TW_PIVOT_WINDOW)
    hs, ls = ph[-4:], pl[-4:]
    kept_h, kept_l = P._peel_breakout_pivots(hs, ls, candles)
    assert [p["index"] for p in kept_h] == [p["index"] for p in hs]
    assert [p["index"] for p in kept_l] == [p["index"] for p in ls]


def test_peeling_never_destroys_the_structure():
    # Rails need pivots. Peeling must never take a set below the minimum, or a
    # noisy stretch would dissolve a valid pattern instead of reporting it.
    candles, _ = _break_then_return(6)
    ph, pl = P.find_pivots(candles, window=P.TW_PIVOT_WINDOW)
    kept_h, kept_l = P._peel_breakout_pivots(ph[-4:], pl[-4:], candles)
    assert len(kept_h) >= P.TW_MIN_PIVOTS
    assert len(kept_l) >= P.TW_MIN_PIVOTS


def test_a_still_forming_wedge_is_reported_as_forming():
    # The fix must not make everything look broken.
    got = P.detect_triangles_wedges(_falling_wedge(), "1D")
    assert [p["status"] for p in got] == ["forming"]
    assert got[0]["type"] == "falling_wedge"


# ── Refactor guard ──────────────────────────────────────────────────────────

def test_rail_fitting_is_unchanged_for_a_clean_structure():
    """
    _fit_rails was extracted from the detector body. On a structure with nothing
    to peel, the rails it produces must be exactly what the pattern reports.
    """
    candles = _falling_wedge()
    pattern = P.detect_triangles_wedges(candles, "1D")[0]
    up, low = _rails(candles)
    ph, pl = P.find_pivots(candles, window=P.TW_PIVOT_WINDOW)
    last_i = max(ph[-4:][-1]["index"], pl[-4:][-1]["index"])
    assert pattern["upper_now"] == pytest.approx(up(last_i), abs=1e-6)
    assert pattern["lower_now"] == pytest.approx(low(last_i), abs=1e-6)
