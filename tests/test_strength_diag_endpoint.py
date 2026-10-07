"""
GET /api/hl/strength-diag: the publish scan as a DRY RUN (nothing written),
returning why candidates do or don't reach the auto-exec floor. Admin only.
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

pytest.importorskip("flask")
import app  # noqa: E402


def test_requires_admin(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    assert app.app.test_client().get("/api/hl/strength-diag").status_code == 401


def test_returns_the_dry_run_diagnostic(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")
    seen = {}

    def fake(dry_run=False):
        seen["dry_run"] = dry_run
        return {"generated_at": "2026-10-04T15:00:00+08:00", "slot": "12:00 PM",
                "recommendations": [{"symbol": "ADA", "direction": "LONG", "strength": 66.5}],
                "strength_diag_full": [f"L{i}" for i in range(30)],
                "hl_extra_preview": ["SOL LONG 76.0 (+10 bottom/top read)"]}

    monkeypatch.setattr(app, "_compute_recommendations", fake)
    monkeypatch.setattr(app, "_hl_slot_status", lambda: {"signals": [
        {"symbol": "BLUR", "direction": "LONG", "strength": 64.0, "row": "published",
         "hl": "below the floor (64.0 < 69)"}]})
    r = app.app.test_client().get("/api/hl/strength-diag?n=5",
                                  headers={"x-cron-secret": "s3cret-s3cret-s3cret"})
    body = r.get_json()
    assert r.status_code == 200 and seen["dry_run"] is True and body["dry_run"] is True
    assert body["strength_diag_now"] == ["L0", "L1", "L2", "L3", "L4"]
    assert body["top3_if_published_now"] == ["ADA LONG 66.5"]
    assert body["hl_extra_if_published_now"] == ["SOL LONG 76.0 (+10 bottom/top read)"]
    assert body["recorded_this_slot"][0]["hl"].startswith("below the floor")
    assert "published_top3" not in body                         # the misleading name is gone
    assert "floor" in body and "coins_with_bottom_reads" in body


def test_a_failure_is_a_clean_500(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")

    def boom(dry_run=False):
        raise RuntimeError("exchange down")

    monkeypatch.setattr(app, "_compute_recommendations", boom)
    r = app.app.test_client().get("/api/hl/strength-diag",
                                  headers={"x-cron-secret": "s3cret-s3cret-s3cret"})
    assert r.status_code == 500 and r.get_json()["error_code"] == "STRENGTH_DIAG_FAILED"


def test_a_dry_run_skips_every_write():
    src = inspect.getsource(app._compute_recommendations)
    persist = src.index("_sp.persist_recommendations(intraday_recs, _analyses)")
    dry = src.index("if dry_run:")
    assert dry < persist                                   # bails out before persisting
    assert '"skipped_reason": "DRY_RUN"' in src[dry:persist]
    assert "raise _SkipPersistence" in src[dry:persist]
    audit = src.index("decision_audit.persist(")
    assert "if dry_run:\n        pass" in src[:audit]       # and before the audit write


# ── HL's decision for a recorded signal ──────────────────────────────────────

SIG = {"symbol": "BLUR", "direction": "LONG", "id": "s1", "candle_ts": 123,
       "confidence_score": 80.5}
TABLE = {"BLUR": {"sz_decimals": 0}}


@pytest.mark.parametrize("sig, table, held, known, phrase", [
    ({**SIG, "confidence_score": 64.0}, TABLE, set(), False, "below the floor (64.0 < 69)"),
    (SIG, {"ETH": {}}, set(), False, "not listed on Hyperliquid"),
    (SIG, TABLE, set(), True, "opened on HL"),
    (SIG, TABLE, {"BLUR"}, False, "position on this coin was already open"),
    (SIG, TABLE, set(), None, "order lookup failed"),
    (SIG, TABLE, set(), False, "stale entry, a quiet market (low-volatility dock), against a strong weekly lean, a cap"),
])
def test_hl_decision(sig, table, held, known, phrase):
    got = app._hl_decision(sig, floor=69.0, table=table, held=held, known_fn=lambda c: known)
    assert phrase in got


def test_the_decision_looks_up_the_same_cloid_auto_exec_uses():
    import hl_execution
    seen = []
    app._hl_decision(SIG, floor=69.0, table=TABLE, held=set(),
                     known_fn=lambda c: seen.append(c) or False)
    assert seen == [hl_execution.client_order_id("s1", "open", 123)]


def test_slot_status_without_a_database(monkeypatch):
    import db
    monkeypatch.setattr(db, "db_enabled", lambda: False)
    assert app._hl_slot_status() == {"error": "DB_NOT_CONFIGURED", "signals": []}
