"""
Rejection diagnostics: detect_flags records WHY a would-be flag was suppressed,
and summarize_flag_diagnostics renders a short human-readable explanation.

All candles are synthetic OHLC; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from patterns import detect_flags, summarize_flag_diagnostics             # noqa: E402
from test_flag_pattern_correctness import build_flag                      # noqa: E402


def test_diag_none_by_default():
    # Without diag_out the behaviour and return type are unchanged.
    cs = build_flag(direction="up", post_closes=[114.0])
    out = detect_flags(cs, "1D", 1.0, 4.0)
    assert isinstance(out, list)


def test_deep_retrace_is_reported():
    # A bull pole that pulls back past the base → retrace > 62% → rejected, and
    # the reason must be captured with the actual retrace %.
    cs = build_flag(direction="up", pole_step=3.0, pole_bars=4,
                    flag_closes=[104.5, 103.0, 101.6, 100.3, 99.5, 99.0, 98.6],
                    flag_half=0.5)
    diag = []
    flags = detect_flags(cs, "1W", 1.0, 4.0, diag_out=diag)
    assert not flags, "a >62% retrace must not produce an accepted flag"
    summary = summarize_flag_diagnostics(diag)
    assert summary, "a rejection reason should be surfaced"
    assert summary[0]["reason"] == "retrace_too_deep"
    assert "retraced" in summary[0]["message"]
    assert "%" in summary[0]["message"]


def test_summary_dedupes_identical_messages():
    cs = build_flag(direction="up", pole_step=3.0, pole_bars=4,
                    flag_closes=[104.5, 103.0, 101.6, 100.3, 99.5, 99.0, 98.6],
                    flag_half=0.5)
    diag = []
    detect_flags(cs, "1W", 1.0, 4.0, diag_out=diag)
    summary = summarize_flag_diagnostics(diag)
    msgs = [d["message"] for d in summary]
    assert len(msgs) == len(set(msgs)), "identical sentences must be collapsed"
    assert len(summary) <= 3


def test_summary_empty_is_safe():
    assert summarize_flag_diagnostics([]) == []
    assert summarize_flag_diagnostics(None) == []
