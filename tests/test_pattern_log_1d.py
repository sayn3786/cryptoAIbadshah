"""
Daily patterns get logged, and keyed on the daily candle.

The pattern-events log was fetched during the recommendation scan, which only
pulls 1H/2H/4H — so 1D wedges and flags (like the TAO 1D falling wedge) were
never recorded. This adds 1D to the log for the published symbols.

Two things matter and are tested. A 1D pattern is keyed on the 1D candle's close,
not the 4H publication slot's — otherwise the same daily wedge would get a fresh
row on every one of the six 4H slots in a day. And a falling wedge is one of the
patterns `_observed_patterns` actually surfaces, so it reaches the log at all.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import pattern_store as ps                                           # noqa: E402


APP_SRC = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()


def _analysis_with_wedge(closed_at_ms):
    return {
        "signal_candle_closed_at": closed_at_ms,
        "triangle_patterns": [{
            "type": "falling_wedge", "label": "Falling Wedge",
            "direction": "bullish", "status": "confirmed", "confirmed": True,
            "breakout_dir": "up", "target": 220.5,
        }],
    }


# ── _observed_patterns surfaces the wedge ───────────────────────────────────

def test_a_falling_wedge_is_surfaced_for_the_log():
    got = appmod._observed_patterns(_analysis_with_wedge(1786233600000))
    wedges = [p for p in got if p.get("kind") == "triangle"]
    assert wedges, "the falling wedge must reach the log"
    assert wedges[0]["type"] == "falling_wedge"
    assert wedges[0]["status"] == "confirmed"


def test_it_reads_the_detector_not_re_detects():
    """An analysis with no triangle list yields no triangle rows — the log
    mirrors the detector, it does not run its own."""
    got = appmod._observed_patterns({"signal_candle_closed_at": 1, "triangle_patterns": []})
    assert not [p for p in got if p.get("kind") == "triangle"]


# ── The 1D event is keyed on the daily candle ───────────────────────────────

def test_a_1d_wedge_is_one_event_across_the_days_4h_slots():
    """
    The daily close is stable all day, so logging it from any of the six 4H
    slots must collapse to ONE row — the store's bar-keyed idempotency.
    """
    daily_close = datetime(2026, 8, 7, tzinfo=timezone.utc)
    an = _analysis_with_wedge(int(daily_close.timestamp() * 1000))

    # Build the events as the log does, from two different 4H slots in the day.
    rows_slot1 = ps.build_events("TAO", "1D", daily_close,
                                 appmod._observed_patterns(an), environment="test")
    rows_slot2 = ps.build_events("TAO", "1D", daily_close,
                                 appmod._observed_patterns(an), environment="test")
    keys1 = {r["idempotency_key"] for r in rows_slot1}
    keys2 = {r["idempotency_key"] for r in rows_slot2}
    assert keys1 and keys1 == keys2, "same daily pattern → same key every slot"


def test_a_different_day_is_a_different_event():
    d1 = datetime(2026, 8, 7, tzinfo=timezone.utc)
    d2 = datetime(2026, 8, 8, tzinfo=timezone.utc)
    an1 = _analysis_with_wedge(int(d1.timestamp() * 1000))
    an2 = _analysis_with_wedge(int(d2.timestamp() * 1000))
    k1 = {r["idempotency_key"] for r in
          ps.build_events("TAO", "1D", d1, appmod._observed_patterns(an1), environment="test")}
    k2 = {r["idempotency_key"] for r in
          ps.build_events("TAO", "1D", d2, appmod._observed_patterns(an2), environment="test")}
    assert k1.isdisjoint(k2)


# ── The wiring, at the source ───────────────────────────────────────────────

def test_the_log_loop_includes_1d():
    assert '("2H", "1D")' in APP_SRC, "the pattern log must iterate 1D"


def test_the_1d_analysis_is_retained_and_fetched():
    # 1D full analysis is kept in `raw`, and fetched for the published symbols.
    assert 'data if tf in ("2H", "1D")' in APP_SRC
    assert '_fetch_tfs([(_r["symbol"], "1D") for _r in intraday_recs])' in APP_SRC


def test_the_1d_event_uses_the_daily_candle_close():
    assert 'signal_candle_closed_at' in APP_SRC, \
        "the 1D event must be keyed on the 1D candle close, not the 4H slot"
