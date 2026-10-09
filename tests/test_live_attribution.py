"""
Per-indicator attribution on REAL closed signals: each recorded signal stores
its 2H score breakdown; closed ones show which sections earned their points.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import live_attribution as la                                          # noqa: E402
from signal_snapshot import build_snapshot                             # noqa: E402


def test_the_snapshot_stores_the_breakdown():
    sig = {"score": 40, "score_breakdown": {"macd": 20, "funding": -16, "cvd": 36},
           "structure_adjustment": -3, "fib_adjustment": 0}
    mc = build_snapshot({"candles": [], "signal": sig}, sig)["market_context"]
    assert mc["engine_score"] == 40.0
    assert mc["score_breakdown"] == {"macd": 20, "funding": -16, "cvd": 36}
    assert mc["strength_adjustments"]["structure_adjustment"] == -3.0
    assert mc["strength_adjustments"]["fib_adjustment"] == 0.0


def row(direction, ret, bd, status="TP_HIT", adj=None):
    return {"direction": direction, "realized_return_pct": ret, "status": status,
            "symbol": "X",
            "snapshot": {"market_context": {"score_breakdown": bd,
                                            "strength_adjustments": adj or {}}}}


def test_contributions_are_relative_to_the_trade():
    r = row("SHORT", 1.0, {"funding": -16, "macd": 20, "vwap": 0},
            adj={"structure_adjustment": -3})
    assert la.contributions(r) == {"funding": 16.0, "macd": -20.0,
                                   "brake: market structure": -3.0}
    assert la.contributions({"direction": "LONG", "snapshot": {"market_context": {}}}) is None


def test_usable_skips_cancelled_unpriced_and_old_rows():
    rows = [row("LONG", 1.0, {"macd": 5}), row("LONG", None, {"macd": 5}),
            row("LONG", 1.0, {"macd": 5}, status="CANCELLED"),
            {"direction": "LONG", "realized_return_pct": 2.0, "status": "TP_HIT"}]
    assert len(la.usable(rows)) == 1


def test_table_and_report():
    rows = ([row("LONG", 1.0, {"funding": 16, "macd": 5}) for _ in range(12)]
            + [row("LONG", -1.0, {"funding": -8}) for _ in range(6)]
            + [row("LONG", -0.5, {"macd": 5}) for _ in range(4)])
    rep = la.report(rows)
    assert rep["signals"] == 22
    f = next(x for x in rep["live_only"] if x["section"] == "funding")
    assert f["with_n"] == 12 and f["against_n"] == 6 and f["silent_n"] == 4
    assert f["edge"] == pytest.approx(1.0 - (-6 - 2) / 10)
    m = next(x for x in rep["chart"] if x["section"] == "macd")
    assert m["with_n"] == 16 and m["edge"] is not None
    small = la.report(rows[:5])
    assert all(x["edge"] is None for x in small["live_only"] + small["chart"])


def test_endpoint(monkeypatch):
    pytest.importorskip("flask")
    os.environ.setdefault("DISABLE_REC_SCHEDULER", "1")
    import app
    import db
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    c = app.app.test_client()
    assert c.get("/api/signals/indicator-attribution").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")
    monkeypatch.setattr(db, "db_configured", lambda: True)

    class Store:
        def list_closed_with_snapshots(self, **kw):
            return [row("LONG", 1.0, {"funding": 16}) for _ in range(10)]

    monkeypatch.setattr(app, "_signal_store", lambda: Store())
    r = c.get("/api/signals/indicator-attribution",
              headers={"x-cron-secret": "s3cret-s3cret-s3cret"})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["signals"] == 10
    assert body["live_only"][0]["section"] == "funding"
