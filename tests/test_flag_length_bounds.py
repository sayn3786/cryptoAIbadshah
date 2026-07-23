"""
Flag consolidation-length bounds + short-flag strength bonus.

Textbook flags are SHORT — Murphy / Edwards & Magee put them at 1–3 weeks and
Bulkowski finds they degrade past ~15 bars. The detector therefore requires
5–15 consolidation candles (≥ 2 swing highs and 2 swing lows so the rails are
real), and scales strength by "tightness" so crisp, short flags outrank long
grinds.

All candles are synthetic OHLC; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import (                                                  # noqa: E402
    detect_flags, MIN_CONSOLIDATION_BARS, MAX_CONSOLIDATION_BARS, TIGHT_MIN_FACTOR,
)
from test_flag_pattern_correctness import build_flag, _bull_flags        # noqa: E402


def test_bounds_are_textbook():
    assert MIN_CONSOLIDATION_BARS == 5
    assert MAX_CONSOLIDATION_BARS == 15
    assert 0.0 < TIGHT_MIN_FACTOR < 1.0


def test_no_flag_outside_length_bounds():
    bad = []
    for nfb in range(5, 22):
        cs = build_flag(direction="up", pole_step=1.5, pole_bars=4,
                        flag_closes=[105.0] * nfb, flag_half=0.3)
        for f in detect_flags(cs, "1D", 1.0, 4.0):
            if not (MIN_CONSOLIDATION_BARS <= f["consolidation_bars"]
                    <= MAX_CONSOLIDATION_BARS):
                bad.append(f["consolidation_bars"])
    assert not bad, f"consolidation length must stay within 5–15, got {bad}"


def _strength(nfb):
    cs = build_flag(direction="up", pole_step=1.5, pole_bars=4,
                    flag_closes=[105.0] * nfb, flag_half=0.3)
    fs = _bull_flags(detect_flags(cs, "1D", 1.0, 4.0))
    return fs[0]["strength"] if fs else None


def test_short_flag_outranks_long_flag():
    # identical pole & retrace; only the consolidation length differs. The short
    # flag must score higher — Bulkowski's finding that short flags perform best.
    short = _strength(5)
    long_ = _strength(14)
    assert short is not None and long_ is not None
    assert short > long_, f"short flag ({short}) should outrank long ({long_})"
