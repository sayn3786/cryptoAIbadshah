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
    r = app.app.test_client().get("/api/hl/strength-diag?n=5",
                                  headers={"x-cron-secret": "s3cret-s3cret-s3cret"})
    body = r.get_json()
    assert r.status_code == 200 and seen["dry_run"] is True and body["dry_run"] is True
    assert body["strength_diag"] == ["L0", "L1", "L2", "L3", "L4"]
    assert body["published_top3"] == ["ADA LONG 66.5"]
    assert body["hl_extra_preview"] == ["SOL LONG 76.0 (+10 bottom/top read)"]
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
