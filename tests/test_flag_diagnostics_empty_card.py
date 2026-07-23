"""
app._flag_diagnostics_for: the "why is the flag card empty" explanation must
fire whenever NO ACTIVE flag exists — not only when zero flags were detected.
This is the TAO-1W case: a flag was found but is stale/inactive, so the frontend
hides it and the card must still explain itself.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

pytest.importorskip("flask")
import app                                                               # noqa: E402


def _flag(is_active=True, confirmed=False, direction="bullish"):
    return {"direction": direction, "is_active": is_active,
            "confirmed": confirmed, "consolidation_bars": 6}


def test_active_flag_yields_no_diagnostics():
    assert app._flag_diagnostics_for([_flag(is_active=True)], []) == []


def test_rejection_reasons_used_when_no_active_flag():
    raw = [{"pole_start_ts": 1, "direction": "bullish", "stage": 3,
            "reason": "retrace_too_deep", "retrace_pct": 80.0, "max_pct": 62,
            "consolidation_bars": 12, "capped_at_max": True}]
    out = app._flag_diagnostics_for([], raw)
    assert out and out[0]["reason"] == "retrace_too_deep"
    assert "80.0%" in out[0]["message"]


def test_stale_flag_without_rejections_is_explained():
    # Flags exist but none active and nothing was rejected → describe the stale
    # state rather than leaving the card blank.
    out = app._flag_diagnostics_for([_flag(is_active=False, direction="bearish")], [])
    assert len(out) == 1
    assert out[0]["reason"] == "inactive"
    assert "bearish" in out[0]["message"]
    assert "no longer active" in out[0]["message"]


def test_confirmed_stale_flag_says_played_out():
    out = app._flag_diagnostics_for(
        [_flag(is_active=False, confirmed=True, direction="bullish")], [])
    assert "played out" in out[0]["message"]
