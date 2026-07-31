"""
`/api/recommendations` serves the RECORDED set, read back from the database.

It used to serve a cached recomputation. Three caches sat in front of the cards
— browser localStorage on a 30-minute key, a server-side JSON blob, and the
compute itself — and none of them was the database. The signals table was
write-only in that path: nothing ever read it back. So the cards and the Signal
Tracker could legitimately disagree about what had been published, and under the
4H cadence that became the normal case for three hours out of every four.

Now the route is a pure read. It does not compute and it does not publish; when
the slot holds nothing it says so rather than inventing a set.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import signal_snapshot as snap                                       # noqa: E402


SGT = timezone(timedelta(hours=8))


def _row(**over):
    """A signal row shaped the way signal_store._row_to_dict returns one."""
    row = {
        "id": "3f2b1c00-0000-4000-8000-000000000001",
        "symbol": "TAO",
        "direction": "LONG",
        "timeframe": "2H",
        # numeric columns come back as plain-notation STRINGS, never floats
        "entry_price": "332.500000000000",
        "stop_loss": "318.000000000000",
        "confidence_score": "71.00000000",
        "generated_at": "2026-07-31T08:05:00+00:00",
        "candle_close_time": "2026-07-31T08:00:00+00:00",
        "status": "PENDING",
        "targets": [
            {"target_number": 2, "target_price": "357.500000000000"},
            {"target_number": 1, "target_price": "345.000000000000"},
            {"target_number": 3, "target_price": "372.000000000000"},
        ],
        "published_card": {"h1_strength": 68, "h2_strength": 74,
                           "avg_tf_strength": 71.0, "display_strength": 71,
                           "reasons": ["▲ MACD bullish cross"]},
    }
    row.update(over)
    return row


# ── The stored card is an allow-list, like the snapshot ────────────────────

def test_the_card_only_keeps_named_fields():
    card = snap.build_card({"h1_strength": 68, "rr_ratio": 1.7,
                            "api_key": "sk-should-never-be-stored",
                            "candles": [{"o": 1}] * 500})
    assert card == {"h1_strength": 68, "rr_ratio": 1.7}
    assert "api_key" not in card
    assert "candles" not in card, "raw series must never reach the card"


def test_the_card_never_carries_prices():
    # entry / sl / targets are COLUMNS. A second copy could drift from the
    # record of the decision, so the reader fills them in from the row.
    for priced in ("entry", "sl", "tp_targets", "symbol", "direction"):
        assert priced not in snap.CARD_KEYS


def test_the_card_drops_missing_keys_rather_than_nulling_them():
    # A sparse card is honest; a card full of nulls looks like the strategy
    # reported nothing when it simply was not asked.
    card = snap.build_card({"h1_strength": 68, "rr_ratio": None})
    assert card == {"h1_strength": 68}


def test_the_card_survives_an_empty_recommendation():
    assert snap.build_card(None) == {}
    assert snap.build_card({}) == {}


def test_every_card_key_is_something_the_renderer_reads():
    # The card exists to feed dashboard.js _buildRecCard. A key nothing reads is
    # storage spent for nothing.
    js = os.path.join(os.path.dirname(__file__), "..", "dashboard", "js", "dashboard.js")
    src = open(js, encoding="utf-8").read()
    unread = [k for k in snap.CARD_KEYS if f"r.{k}" not in src]
    assert unread == [], f"stored but never rendered: {unread}"


# ── Rebuilding a card from a stored row ────────────────────────────────────

def test_prices_come_from_the_columns_and_stay_exact():
    rec = appmod._rec_from_row(_row())
    assert rec["entry"] == "332.500000000000"
    assert rec["sl"] == "318.000000000000"
    assert isinstance(rec["entry"], str), "never float a price"


def test_the_ladder_is_ordered_by_target_number():
    rec = appmod._rec_from_row(_row())
    assert rec["tp_targets"] == ["345.000000000000",
                                 "357.500000000000",
                                 "372.000000000000"]


def test_the_card_supplies_the_display_scalars():
    rec = appmod._rec_from_row(_row())
    assert rec["h1_strength"] == 68 and rec["h2_strength"] == 74
    assert rec["avg_tf_strength"] == 71.0
    assert rec["reasons"] == ["▲ MACD bullish cross"]


def test_a_row_from_before_cards_were_stored_still_renders():
    # Deploy-then-migrate: rows published by the previous version have no card.
    # They must render from their columns, not blow up and not invent fields.
    rec = appmod._rec_from_row(_row(published_card={}))
    assert rec["symbol"] == "TAO" and rec["entry"] == "332.500000000000"
    assert rec["display_strength"] == 71.0, "falls back to confidence_score"
    assert "reasons" not in rec, "a missing reason is not an empty reason"


def test_a_row_read_back_is_persisted_and_actionable():
    # It came out of the database. Reporting anything else would contradict the
    # row that was just read.
    rec = appmod._rec_from_row(_row())
    assert rec["persisted"] is True and rec["actionable"] is True
    assert rec["signal_id"] == "3f2b1c00-0000-4000-8000-000000000001"


def test_the_publication_provenance_travels_with_the_card():
    rec = appmod._rec_from_row(_row())
    assert rec["published_at"] == "2026-07-31T08:05:00+00:00"
    assert rec["candle_close_time"] == "2026-07-31T08:00:00+00:00"
    assert rec["status"] == "PENDING"


# ── The envelope ───────────────────────────────────────────────────────────

def _slot(published, reason, recs=(), hour=16):
    start = datetime(2026, 7, 31, hour, tzinfo=SGT)
    return {"recommendations": list(recs), "published": published,
            "reason": reason, "slot_start": start.isoformat(),
            "slot_end": (start + timedelta(hours=4)).isoformat()}


def test_the_envelope_labels_the_slot_and_its_expiry():
    env = appmod._slot_envelope(_slot(True, None, [{"symbol": "TAO"}]))
    assert env["slot"] == "4:00 PM"
    assert env["valid_until_fmt"].startswith("8:00 PM SGT, Jul 31")
    assert env["date_label"] == "Jul 31, 2026 (SGT)"


def test_the_envelope_says_where_the_set_came_from():
    env = appmod._slot_envelope(_slot(True, None, [{"symbol": "TAO"}]))
    assert env["source"] == "database"
    assert env["publication_interval_hours"] == 4


def test_a_served_set_is_always_actionable():
    # Everything in it came out of the database, so it is by definition
    # recorded — the old actionable gate has nothing left to guard.
    assert appmod._slot_envelope(_slot(True, None, [{"symbol": "X"}]))["actionable"] is True


@pytest.mark.parametrize("reason", ["NOT_PUBLISHED_YET", "DB_NOT_CONFIGURED",
                                    "DB_READ_FAILED"])
def test_an_empty_slot_reports_why(reason):
    env = appmod._slot_envelope(_slot(False, reason))
    assert env["published"] is False
    assert env["reason"] == reason
    assert env["recommendations"] == []


def test_the_envelope_needs_no_network():
    # Serving a slot must cost the database read and nothing else. If this ever
    # starts calling build_analysis, the route stops being a cheap read.
    import inspect
    src = inspect.getsource(appmod._slot_envelope)
    for expensive in ("build_analysis", "requests", "_compute_recommendations"):
        assert expensive not in src


# ── The route ──────────────────────────────────────────────────────────────

def test_the_route_never_computes():
    # The whole point: publication happens on the 4H close, driven by the cron.
    # If this route computes, it can serve a set that was never recorded.
    import inspect
    src = inspect.getsource(appmod.api_recommendations)
    assert "_compute_recommendations" not in src
    assert "_rec_cache_load" not in src, "no cache in front of the database"


def test_an_empty_slot_is_200_not_an_error(monkeypatch):
    monkeypatch.setattr(appmod, "_published_slot",
                        lambda *a, **k: _slot(False, "NOT_PUBLISHED_YET"))
    res = appmod.app.test_client().get("/api/recommendations")
    assert res.status_code == 200, "'nothing published yet' is an answer, not a fault"
    body = res.get_json()
    assert body["published"] is False and body["recommendations"] == []


def test_a_published_slot_is_served_verbatim(monkeypatch):
    rec = appmod._rec_from_row(_row())
    monkeypatch.setattr(appmod, "_published_slot",
                        lambda *a, **k: _slot(True, None, [rec]))
    body = appmod.app.test_client().get("/api/recommendations").get_json()
    assert body["published"] is True
    assert body["recommendations"][0]["entry"] == "332.500000000000"


# ── Publication is driven separately, at all six boundaries ────────────────

def test_the_publish_cron_exists_and_is_authorized():
    import os as _os
    _os.environ["CRON_SECRET"] = "test-secret"
    try:
        c = appmod.app.test_client()
        assert c.post("/api/cron/publish").status_code == 401
        assert c.post("/api/cron/publish",
                      headers={"x-cron-secret": "wrong"}).status_code == 401
    finally:
        _os.environ.pop("CRON_SECRET", None)


def test_the_publish_workflow_covers_all_six_boundaries():
    # The Telegram cron only covers three. If publication rode on that, three
    # slots a day would silently never publish.
    path = os.path.join(os.path.dirname(__file__), "..", ".github",
                        "workflows", "signal-publish.yml")
    text = open(path, encoding="utf-8").read()
    assert "/api/cron/publish" in text
    import re
    m = re.search(r"cron:\s*'(\S+)\s+(\S+)\s+\*\s+\*\s+\*'", text)
    assert m, "the publish workflow must have a schedule"
    minute, hours = m.group(1), m.group(2)
    assert sorted(int(h) for h in hours.split(",")) == [0, 4, 8, 12, 16, 20]
    assert 0 < int(minute) <= 15, "must fire AFTER the boundary, never on or before it"


def test_the_publish_cron_sends_nothing():
    # Six Telegram blasts a day to cover the missing boundaries would be spam;
    # that is exactly why this endpoint is separate from /api/cron/daily.
    import inspect
    src = inspect.getsource(appmod.api_cron_publish)
    assert "_send_telegram" not in src and "_post_twitter" not in src


# ── The published strength must survive contact with the live score ───────
# The live-score refresh used to overwrite .rec-strength with a freshly computed
# number — directly under a tooltip promising "a snapshot from this exact
# moment". The headline figure silently stopped being the one the trade was
# recorded at, which is the same defect as serving a cached recomputation, just
# one layer further down.

def _js():
    path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "js",
                        "dashboard.js")
    return open(path, encoding="utf-8").read()


def test_the_live_score_never_overwrites_the_published_one():
    src = _js()
    assert "strEl.textContent = `${live.strength}/100`" not in src, \
        "the recorded strength must not be replaced by a live recomputation"


def test_the_live_score_is_shown_additively_and_labelled():
    src = _js()
    assert "rec-strength-now" in src
    assert "now ${now}" in src, "the drift must be visible, not silent"


def test_drift_direction_is_distinguishable():
    src = _js()
    assert "weaker" in src and "stronger" in src, \
        "a decayed setup and a strengthened one must not look the same"


def test_the_drift_badge_hides_when_there_is_no_drift():
    css = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "css",
                            "dashboard.css"), encoding="utf-8").read()
    assert ".rec-strength-now:empty" in css and "display: none" in css


def test_the_published_strength_is_labelled_as_recorded():
    assert "Strength recorded when this set was published" in _js()


# ── The browser cache must expire with the slot, not on its own clock ──────

def test_the_browser_cache_is_keyed_to_the_publication_slot():
    js = os.path.join(os.path.dirname(__file__), "..", "dashboard", "js", "dashboard.js")
    src = open(js, encoding="utf-8").read()
    assert "REC_SLOT_HOURS = 4" in src
    # The old key bucketed every 30 minutes, which does not divide into the
    # slots: a browser could show the previous set for half an hour after a new
    # one published.
    assert "getUTCMinutes() / 30" not in src
