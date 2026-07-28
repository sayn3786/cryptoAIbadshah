"""
Breakout retest tracking: after a confirmed break, classify whether price ran
away ('extended'), is back AT the broken level ('retesting'), or came back and
pushed away again ('held' — the valid, high-quality retest).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import _retest_state, RETEST_BAND_PCT                      # noqa: E402

LVL = 100.0


def _bars(closes, pad=0.3):
    return [{"timestamp": 1_000_000 + i * 3_600_000, "open": c,
             "high": c + pad, "low": c - pad, "close": c} for i, c in enumerate(closes)]


def test_extended_when_price_runs_away():
    st = _retest_state(_bars([100, 104, 108, 112]), 0, LVL, True)
    assert st["status"] == "extended" and st["distance_pct"] > 0


def test_retesting_when_price_sits_at_the_level():
    st = _retest_state(_bars([100, 106, 102, 100.2]), 0, LVL, True)
    assert st["status"] == "retesting"
    assert abs(st["distance_pct"]) <= RETEST_BAND_PCT * 100 + 0.01
    assert "holding it keeps the breakout valid" in st["note"]


def test_held_when_retest_bounces_away():
    st = _retest_state(_bars([100, 106, 100.5, 105]), 0, LVL, True)
    assert st["status"] == "held"
    assert "resistance now support" in st["note"]


def test_bearish_mirror():
    st = _retest_state(_bars([100, 94, 99.6, 95]), 0, LVL, False)
    assert st["status"] == "held" and st["distance_pct"] < 0
    assert "support now resistance" in st["note"]


def test_failure_side_returns_none():
    # Price beyond the level on the WRONG side is the failure path (handled by
    # the invalidation rules), not a retest state.
    assert _retest_state(_bars([100, 104, 90]), 0, LVL, True) is None


def test_no_bars_after_breakout_returns_none():
    assert _retest_state(_bars([100]), 0, LVL, True) is None
    assert _retest_state(_bars([100, 104]), None, LVL, True) is None
