"""
Breakout volume confirmation: the breakout candle's volume is graded against the
preceding average (strong / normal / weak). It never gates the pattern — price
still decides confirmation — it only reports whether volume backed the move.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import (                                                   # noqa: E402
    _breakout_volume, detect_flags, VOL_CONFIRM_MULT, VOL_WEAK_MULT,
)
from test_flag_pattern_correctness import build_flag, _bull_flags         # noqa: E402


def _bars(vols):
    return [{"timestamp": 1_000_000 + i * 3_600_000, "open": 100, "high": 101,
             "low": 99, "close": 100, "volume": v} for i, v in enumerate(vols)]


def test_strong_normal_weak_grading():
    base = [100.0] * 20
    assert _breakout_volume(_bars(base + [100 * VOL_CONFIRM_MULT + 1]), 20)["level"] == "strong"
    assert _breakout_volume(_bars(base + [100.0]), 20)["level"] == "normal"
    assert _breakout_volume(_bars(base + [100 * VOL_WEAK_MULT - 1]), 20)["level"] == "weak"


def test_ratio_is_vs_preceding_average():
    out = _breakout_volume(_bars([100.0] * 20 + [300.0]), 20)
    assert out["avg_volume"] == 100.0 and out["ratio"] == 3.0


def test_missing_or_zero_volume_returns_none():
    # Several data sources carry no real volume — report nothing rather than
    # implying the breakout was weak.
    assert _breakout_volume(_bars([0.0] * 20 + [0.0]), 20) is None
    bars = _bars([100.0] * 20 + [50.0])
    for b in bars:
        b.pop("volume")
    assert _breakout_volume(bars, 20) is None


def test_out_of_range_index_is_safe():
    bars = _bars([100.0] * 5)
    assert _breakout_volume(bars, None) is None
    assert _breakout_volume(bars, 0) is None
    assert _breakout_volume(bars, 99) is None


def test_flag_reports_volume_on_the_breakout_candle():
    cs = build_flag(direction="up", flag_bars=5, flag_drift=-0.4, post_closes=[114.0, 115.0])
    for c in cs:
        c["volume"] = 100.0
    f = [x for x in _bull_flags(detect_flags(cs, "1D", 1.0, 4.0)) if x["confirmed"]][0]
    idx = [i for i, c in enumerate(cs) if c["timestamp"] == f["breakout_ts"]][0]
    cs[idx]["volume"] = 900.0                     # spike ONLY the breakout candle
    f2 = [x for x in _bull_flags(detect_flags(cs, "1D", 1.0, 4.0)) if x["confirmed"]][0]
    bv = f2["breakout_volume"]
    assert bv and bv["level"] == "strong" and bv["ratio"] == 9.0


def test_forming_flag_has_no_breakout_volume():
    cs = build_flag(direction="up", flag_bars=5, flag_drift=-0.4)   # no breakout
    for f in _bull_flags(detect_flags(cs, "1D", 1.0, 4.0)):
        assert f["breakout_volume"] is None
