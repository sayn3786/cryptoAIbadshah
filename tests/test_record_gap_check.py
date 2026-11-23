import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import record_gap_check as g                                          # noqa: E402


def test_reason_of():
    assert g.reason_of({"status": "CLOSED", "close_reason": "OPPOSITE_SIGNAL_8H"}) == "early exit (opposite signal)"
    assert g.reason_of({"status": "TP_HIT"}) == "all targets"
    assert g.reason_of({"status": "EXPIRED"}) == "7-day expiry"


def test_summarize_splits_the_gap():
    pairs = [{"reason": "early exit (opposite signal)", "tracker": -0.4, "resettled": 0.6, "re_outcome": "tp2"},
             {"reason": "all targets", "tracker": 1.0, "resettled": 1.0, "re_outcome": "tp3"},
             {"reason": "stop (incl. at entry after TP1)", "tracker": -1.0, "resettled": None, "re_outcome": None}]
    s = g.summarize(pairs)
    assert s["matched"]["n"] == 2 and s["matched"]["gap_r"] == 1.0
    assert s["by_reason"]["early exit (opposite signal)"]["share"] == 100
    assert s["only_tracker"]["n"] == 1 and s["only_tracker"]["tracker"] == -1.0
    assert "re-settled" in g.render_telegram(s)
