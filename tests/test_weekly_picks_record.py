import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import weekly_picks_record as wp                                     # noqa: E402

ROWS = [{"timeframe": "1W", "status": "TP_HIT", "direction": "SHORT", "realized_return_pct": 6.0, "closed_at": "2026-10-05"},
        {"timeframe": "1W", "status": "SL_HIT", "direction": "LONG", "realized_return_pct": -4.0, "closed_at": "2026-09-01"},
        {"timeframe": "1W", "status": "EXPIRED", "direction": "LONG", "move_pct": -1.0},
        {"timeframe": "BW", "status": "CANCELLED", "direction": "LONG"},
        {"timeframe": "1D", "status": "CLOSED", "direction": "SHORT", "realized_return_pct": 1.5}]


def test_summarize():
    s = wp.summarize(ROWS)
    w = s["1W"]
    assert w["decided"] == 2 and w["wins"] == 1 and w["win_pct"] == 50.0 and w["avg_pct"] == 1.0
    assert w["expired"] == 1 and w["expired_avg"] == -1.0 and w["first"] == "2026-09-01"
    assert w["short"]["n"] == 1 and w["long"]["win_pct"] == 0.0
    assert s["BW"]["decided"] == 0 and s["BW"]["not_filled"] == 1
    text = wp.render(s, 365)
    assert "Crypto week" in text and "no decided trades yet" in text


def test_fetch_reads_the_tracker():
    seen = {}
    rows = wp.fetch(90, get=lambda url: seen.setdefault("u", url) and {"closed": ROWS})
    assert "/api/signals/tracker?days=90" in seen["u"] and len(rows) == 5
