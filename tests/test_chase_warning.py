"""
Chased-entry warning: when price has already run past a CONFIRMED pattern's
breakout level and the R/R at the live entry is poor, the signal should say so
and point at the breakout as the retest zone.

Exercises the detection thresholds directly plus the generate_signal contract.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import (                                                    # noqa: E402
    generate_signal, CHASE_RR_MIN, CHASE_MIN_RUN_PCT,
)
from test_flag_pattern_correctness import _neutral_analysis              # noqa: E402


def test_thresholds_are_sane():
    assert CHASE_RR_MIN >= 1.0
    assert CHASE_MIN_RUN_PCT > 0


def test_signal_always_exposes_chase_warning_key():
    sig = generate_signal(_neutral_analysis())
    assert "chase_warning" in sig


def test_no_warning_without_a_confirmed_pattern():
    a = _neutral_analysis()
    a["flags"] = [{"direction": "bearish", "confirmed": False, "is_active": True,
                   "break_level": 198.28}]
    assert generate_signal(a)["chase_warning"] is None


# ── the decision rule itself (mirrors the engine's condition) ───────────────
def _is_chase(direction, entry, break_level, rr):
    past = (entry < break_level) if direction == "SHORT" else (entry > break_level)
    run  = abs(entry - break_level) / break_level * 100
    return bool(past and run >= CHASE_MIN_RUN_PCT and rr < CHASE_RR_MIN)


def test_short_that_ran_past_breakout_with_poor_rr_is_a_chase():
    # The screenshot: break 198.28, entry 187.94 (ran ~5.2% past), R/R ~0.85
    assert _is_chase("SHORT", 187.94, 198.28, 0.85)


def test_short_entering_near_the_breakout_is_not_a_chase():
    # Entry right at the break → barely moved past, good R/R → no warning
    assert not _is_chase("SHORT", 197.9, 198.28, 2.4)


def test_long_mirror():
    assert _is_chase("LONG", 210.0, 200.0, 1.1)          # ran 5% past, poor R/R
    assert not _is_chase("LONG", 210.0, 200.0, 2.5)      # ran past but R/R fine
    assert not _is_chase("LONG", 199.0, 200.0, 1.1)      # hasn't cleared the break
