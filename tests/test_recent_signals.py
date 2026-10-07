"""
Recent signals: the last days' 4h publishes replayed next to what live
recorded, slot by slot.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import recent_signals as rs                                            # noqa: E402

S = 100 * rs.SLOT_MS
H = rs.HOUR_MS


def cand(sym, slot, strength, before=None, direction="LONG", chased=False, h1=None, h2=None):
    return {"symbol": sym, "slot_ms": slot, "direction": direction, "strength": strength,
            "strength_before_calibration": before if before is not None else strength,
            "chased": chased, "h1_strength": h1, "h2_strength": h2}


def iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def live_row(sym, slot, cs, name=rs.PUBLISHED, direction="LONG"):
    return {"symbol": sym, "direction": direction, "confidence_score": cs,
            "candle_close_time": iso(slot), "strategy_name": name}


CANDS = [cand("ETH", S, 72.0),
         cand("HYPE", S, 51.0, before=85.7, chased=True, h1=23.0, h2=80.0),
         cand("ZEC", S, 68.0, before=71.0, direction="SHORT", chased=True),
         cand("ADA", S, 40.0),
         cand("SOL", S + rs.SLOT_MS, 64.0),
         cand("BTC", S + H, 90.0)]                       # off-slot: ignored


def test_live_by_slot_keeps_published_and_extras():
    rows = [live_row("ZEC", S, 68.0, direction="SHORT"), live_row("HYPE", S, 68.0),
            live_row("SOL", S, 70.0, rs.HL_EXTRA), live_row("X", S, 99.0, "alerts")]
    got = rs.live_by_slot(rows)
    assert list(got) == [S]
    assert [(x["symbol"], x["strength"], x["extra"]) for x in got[S]] == [
        ("SOL", 70.0, True), ("ZEC", 68.0, False), ("HYPE", 68.0, False)]


def test_compare_counts_and_gap():
    live = rs.live_by_slot([live_row("ZEC", S, 68.0, direction="SHORT"),
                            live_row("ETH", S, 66.0), live_row("HYPE", S, 68.0)])
    res = rs.compare(CANDS, live, start_ms=S, end_ms=S + 2 * rs.SLOT_MS)
    assert res["slots"] == 2 and res["replay_69"] == 1 and res["replay_capped"] == 2
    assert res["live_69"] == 0 and res["live_at_cap"] == 2
    # ZEC 68 vs 68, ETH 66 vs 72, HYPE 68 vs 51
    assert res["matched"] == 3 and res["avg_gap"] == pytest.approx((0 - 6 + 17) / 3, abs=0.1)
    first = res["rows"][0]
    assert [c["symbol"] for c in first["replay"]] == ["HYPE", "ETH", "ZEC"]   # by before-cap


def test_replay_only_when_live_is_unread():
    res = rs.compare(CANDS, None, start_ms=S, end_ms=S + 2 * rs.SLOT_MS)
    assert not res["live_read"] and res["avg_gap"] is None
    text = rs.render_telegram(res, days=10)
    assert "Live: not read" in text and "live:" not in text


def test_render_marks_floor_and_caps():
    live = rs.live_by_slot([live_row("HYPE", S, 68.0)])
    text = rs.render_telegram(rs.compare(CANDS, live, start_ms=S, end_ms=S + 2 * rs.SLOT_MS),
                              days=10)
    assert text.startswith("🔎")
    assert "· HYPE L 85.7→51 (chase cap + 1H/2H split 23/80)" in text
    assert "✅ ETH L 72" in text and "· ZEC S 71→68 (chase cap)" in text
    assert "live: HYPE L 68" in text


def test_fetch_live_pages_until_older_than_since():
    calls = []

    class R:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Sess:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append((url, params["offset"], headers["x-cron-secret"]))
            page = params["offset"] // 2
            items = [live_row("A", S - page * rs.SLOT_MS, 60), live_row("B", S - page * rs.SLOT_MS, 61)]
            return R({"items": items, "has_more": True})

    got = rs.fetch_live("https://app/", "sec", S - rs.SLOT_MS, session=Sess(), page=2)
    assert [c[1] for c in calls] == [0, 2, 4]                 # stops once older than since
    assert calls[0][0] == "https://app/api/signals/history" and calls[0][2] == "sec"
    assert len(got) == 4


def test_workflow_runs_it_with_the_live_read_secrets():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", recent" in wf and "python -m recent_signals --telegram" in wf
    assert "APP_URL:                 ${{ secrets.APP_URL }}" in wf
