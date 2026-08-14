"""
The v48 ATR/RR fallback ladder pull-in.

The v45 analytics showed the peak favourable excursion reached TP2 only 5.6% of
the time and TP3 only 2.2% at the old 3.5R / 5.5R — half the position aimed past
where price goes. v48 pulls TP2 to 2.6R and TP3 to 3.6R (TP1 stays 2.0R). These
tests hold the new multiples, that the ladder stays strictly increasing under
every cap, and that TP2 clears the 1.5 R/R publication floor.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import atr_rr_tp_ladder, TP1_RR, TP2_RR, TP3_RR       # noqa: E402


def test_the_pulled_in_multiples_are_the_new_ladder():
    assert (TP1_RR, TP2_RR, TP3_RR) == (2.0, 2.6, 3.6)


def test_default_factor_uncapped_gives_the_multiples():
    # A large cap does not bite; sl_dist 1.0 → distances read straight as R.
    assert atr_rr_tp_ladder(1.0, 1.0, 100.0) == (2.0, 2.6, 3.6)


def test_tp3_is_much_nearer_than_the_old_5_5R():
    _, _, tp3 = atr_rr_tp_ladder(1.0, 1.0, 100.0)
    assert tp3 <= 4.0                       # was 5.5R and almost never reached


def test_the_ladder_is_strictly_increasing_under_every_cap():
    for f in (0.5, 1.0, 1.5, 2.0):
        for cap in (0.5, 1.5, 3.0, 8.0, 100.0):
            tp1, tp2, tp3 = atr_rr_tp_ladder(1.0, f, cap)
            assert tp1 < tp2 < tp3, f"not increasing at factor={f} cap={cap}"


def test_tp2_clears_the_rr_floor_in_the_normal_regime():
    # With a non-restrictive cap the TP2 floor (2.2R) holds, well above MIN_RR 1.5.
    for f in (0.5, 1.0, 2.0):
        _, tp2, _ = atr_rr_tp_ladder(1.0, f, 100.0)
        assert tp2 >= 2.2


def test_a_tiny_cap_collapses_the_ladder_but_keeps_it_ordered():
    tp1, tp2, tp3 = atr_rr_tp_ladder(1.0, 2.0, 0.5)
    assert tp1 < tp2 < tp3
    assert tp3 <= 0.5                        # hard cap respected
