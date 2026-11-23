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


def _w(ret, d="SHORT", tf="1W"):
    return {"timeframe": tf, "status": "TP_HIT" if ret > 0 else "SL_HIT", "direction": d, "realized_return_pct": ret}


def test_verdict_waits_for_30_and_5_longs():
    assert wp.verdict([_w(5.0)] * 40)["state"] == "waiting"                  # no longs: no verdict
    assert wp.verdict([_w(5.0)] * 20 + [_w(1.0, "LONG")] * 5)["state"] == "waiting"


def test_verdict_proven_and_fails():
    v = wp.verdict([_w(3.0)] * 15 + [_w(-2.0, tf="BW")] * 10 + [_w(1.0, "LONG")] * 5)
    assert v["state"] == "proven" and v["n"] == 30
    v = wp.verdict([_w(3.0)] * 15 + [_w(-2.0, tf="BW")] * 10 + [_w(-1.0, "LONG")] * 5)
    assert v["state"] == "fails"                                              # longs losing
    assert "paper-only" in "\n".join(wp.render_verdict(v))


def test_render_no_losses_reads_cleanly():
    s = wp.summarize([_w(5.0)])
    assert "None" not in wp.render(s, 30, wp.verdict([_w(5.0)]))


def test_close_message_marks_weekly_picks():
    import close_publish as cp
    assert cp.WEEKLY_NOTE.startswith("🧪") and "30" in cp.WEEKLY_NOTE
