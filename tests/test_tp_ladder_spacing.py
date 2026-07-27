"""
TP ladder spacing: TP3 must be the NEXT structural wall beyond TP2, not the
furthest one in reach. Regression for a LONG showing TP1 +1.6%, TP2 +2.9% and
TP3 +54% (a distant prior swing high became TP3, leaving an unreachable gap).

Pure function; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import _snap_tp_to_structure, TP3_MAX_MULT_OF_TP2   # noqa: E402


ENTRY, SL, TF = 196.1868, 191.294, "1D"
MAX_TP3_ABS = ENTRY * 0.20 * 2.0          # mid-cap multiplier


def _pcts(tp, entry=ENTRY):
    return [round((x / entry - 1) * 100, 2) for x in tp]


def test_far_swing_does_not_become_tp3():
    # Nearby walls plus a FAR prior high (~+54%). TP3 must not jump to it.
    levels = [199.36, 201.95, 210.0, 225.0, 302.3]
    tp, _wall, _r = _snap_tp_to_structure("LONG", ENTRY, SL, TF, levels, MAX_TP3_ABS)
    p1, p2, p3 = _pcts(tp)
    assert p1 < p2 < p3, "ladder must be increasing"
    assert p3 < 10, f"TP3 should stay proportional, got +{p3}%"


def test_tp3_respects_max_multiple_of_tp2():
    levels = [199.36, 201.95, 210.0, 225.0, 302.3]
    tp, _wall, _r = _snap_tp_to_structure("LONG", ENTRY, SL, TF, levels, MAX_TP3_ABS)
    d2, d3 = tp[1] - ENTRY, tp[2] - ENTRY
    assert d3 <= d2 * TP3_MAX_MULT_OF_TP2 + 1e-6


def test_ladder_increasing_for_short():
    entry, sl = 200.0, 205.0
    tp, _wall, _r = _snap_tp_to_structure("SHORT", entry, sl, TF,
                                          [196.0, 192.0, 180.0, 120.0], entry * 0.2 * 2)
    assert tp[0] > tp[1] > tp[2], "SHORT ladder must step down"
    assert (entry - tp[2]) <= (entry - tp[1]) * TP3_MAX_MULT_OF_TP2 + 1e-6


def test_no_qualifying_wall_returns_none():
    # Nothing clears the reward gate → keep the ATR/RR targets (None).
    assert _snap_tp_to_structure("LONG", ENTRY, SL, TF, [ENTRY * 1.0005], MAX_TP3_ABS) is None
