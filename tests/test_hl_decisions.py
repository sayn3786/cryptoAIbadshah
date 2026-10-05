"""
GET /api/hl/decisions: what HL did with every recorded 69+ signal over the
last days, from the exchange's order lookup, the coin listing, the ⏭ stale
alert and the new per-slot / per-signal run log.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

pytest.importorskip("flask")
import app  # noqa: E402

SGT = timezone(timedelta(hours=8))


@pytest.fixture
def mem_kv(monkeypatch):
    import kv
    vals, keys = {}, set()
    monkeypatch.setattr(kv, "get_value", lambda k: vals.get(k))
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: vals.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "exists", lambda k: k in keys)
    return vals, keys


# ── the verdict ──────────────────────────────────────────────────────────────

def V(**kw):
    base = dict(listed=True, placed=False, runs=[], stale_alert=False, slot_runs=[])
    return app._hl_history_verdict(**{**base, **kw})[0]


def test_verdicts():
    assert V(placed=True, listed=False) == "opened"
    assert V(listed=False) == "not_listed"
    assert V(runs=[{"reason": "STALE_ENTRY", "drift_pct": 2.1, "allowed_pct": 1.2}]) == "stale"
    assert V(runs=[{"reason": "ALREADY_PLACED"}, {"reason": "MAX_EXPOSURE"}]) == "rejected"
    assert V(runs=[{"reason": "LOW_VOLATILITY", "atr_ratio": 0.6}]) == "low_vol"
    assert V(stale_alert=True) == "stale"
    assert V(slot_runs=[{"ran": False, "reason": "NOT_READY"}]) == "not_run"
    assert V(slot_runs=[{"ran": False}, {"ran": True, "attempted": 0}]) == "not_attempted"
    assert V(placed=None) == "unknown"
    assert V() == "no_record"


def test_not_run_text_names_the_reason():
    code, text = app._hl_history_verdict(listed=True, placed=False, runs=[], stale_alert=None,
                                         slot_runs=[{"ran": False, "reason": "NOT_READY"}])
    assert code == "not_run" and "NOT_READY" in text


# ── the run log ──────────────────────────────────────────────────────────────

def test_signal_runs_are_appended_and_capped(mem_kv):
    vals, _ = mem_kv
    sig = {"id": "s1", "candle_ts": "2026-10-05T04:00:00+00:00"}
    for i in range(app.HL_RUN_LOG_MAX + 2):
        app._record_hl_signal_runs([sig], [{"ok": False, "reason": f"R{i}"}], [])
    got = app._kv_json_list(app._hl_signal_run_key(sig))
    assert len(got) == app.HL_RUN_LOG_MAX and got[-1]["reason"] == f"R{app.HL_RUN_LOG_MAX + 1}"
    app._record_hl_signal_runs([], [], [{**sig, "id": "s2", "atr_ratio": 0.55}])
    low = app._kv_json_list(app._hl_signal_run_key({**sig, "id": "s2"}))
    assert low[0]["reason"] == "LOW_VOLATILITY" and low[0]["atr_ratio"] == 0.55


def test_stale_details_are_kept(mem_kv):
    sig = {"id": "s1", "candle_ts": 1}
    app._record_hl_signal_runs([sig], [{"ok": False, "reason": "STALE_ENTRY", "drift_pct": 2.0,
                                        "allowed_pct": 1.1}], [])
    e = app._kv_json_list(app._hl_signal_run_key(sig))[0]
    assert e["drift_pct"] == 2.0 and e["allowed_pct"] == 1.1


def test_every_run_logs_its_slot(mem_kv, monkeypatch):
    monkeypatch.setattr(app, "_hl_auto_execute_pass",
                        lambda: {"ok": True, "ran": False, "reason": "NOT_READY"})
    out = app._hl_auto_execute_run()
    assert out["reason"] == "NOT_READY"
    slot = app._slot_start(datetime.now(app._SGT))
    got = app._kv_json_list(app._hl_slot_run_key(slot))
    assert got[-1]["ran"] is False and got[-1]["reason"] == "NOT_READY"


def test_a_broken_store_never_breaks_a_run(monkeypatch):
    import kv
    def boom(*a, **k):
        raise RuntimeError("kv down")
    monkeypatch.setattr(kv, "get_value", boom)
    monkeypatch.setattr(kv, "set_value", boom)
    monkeypatch.setattr(app, "_hl_auto_execute_pass", lambda: {"ok": True, "ran": True})
    assert app._hl_auto_execute_run() == {"ok": True, "ran": True}
    assert app._kv_json_list("x") == []


def test_the_pass_records_each_attempt():
    import inspect
    src = inspect.getsource(app._hl_auto_execute_pass)
    assert "_record_hl_signal_runs(signals, out.get(\"results\") or [], skipped)" in src
    assert "_record_hl_signal_runs([], [], skipped)" in src


# ── the report ───────────────────────────────────────────────────────────────

def row(rid, sym, cs, gen, name="mtf_confluence_top3"):
    return {"id": rid, "symbol": sym, "direction": "LONG", "entry_price": "1",
            "confidence_score": str(cs), "candle_close_time": "2026-09-29T12:00:00+00:00",
            "generated_at": gen, "strategy_name": name}


def test_decisions_report(mem_kv, monkeypatch):
    import db
    import hl_account
    import hl_meta
    import signal_publish as sp
    vals, keys = mem_kv
    gen = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    rows = {sp.STRATEGY_NAME: [row("a", "ENJ", 98.7, gen), row("b", "ADA", 72.8, gen),
                               row("c", "AAVE", 62.0, gen), row("d", "ICP", 87.0, gen)],
            sp.HL_EXTRA_STRATEGY_NAME: []}

    class Store:
        def list_published_between(self, since, until, strategy_name=None, limit=0):
            g = datetime.fromisoformat(gen)
            return rows[strategy_name] if since <= g < until else []

    monkeypatch.setattr(db, "db_enabled", lambda: True)
    monkeypatch.setattr(app, "_signal_store", lambda: Store())
    monkeypatch.setattr(hl_account, "configured", lambda: True)
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {"ADA": {}, "ENJ": {}, "AAVE": {}})
    monkeypatch.setattr(hl_meta, "resolve_coin",
                        lambda sym, table: sym if sym in table else None)
    import hl_execution as hx
    placed = {hx.client_order_id("b", "open", "2026-09-29T12:00:00+00:00")}
    monkeypatch.setattr(hl_account, "order_known", lambda cloid: cloid in placed)
    # ENJ: the pass logged a stale entry
    import hl_autoexec as ax
    app._record_hl_signal_runs([ax.to_signal(rows[sp.STRATEGY_NAME][0])],
                               [{"ok": False, "reason": "STALE_ENTRY", "drift_pct": 3,
                                 "allowed_pct": 1.5}], [])
    out = app._hl_decisions(days=3, min_strength=69)
    by = {s["symbol"]: s for s in out["signals"]}
    assert set(by) == {"ENJ", "ADA", "ICP"}                  # AAVE 62 is under the floor
    assert by["ENJ"]["code"] == "stale" and by["ADA"]["code"] == "opened"
    assert by["ICP"]["code"] == "not_listed"
    assert out["counts"] == {"stale": 1, "opened": 1, "not_listed": 1}
    assert by["ENJ"]["published"].endswith("SGT")


def test_decisions_without_a_db(monkeypatch):
    import db
    monkeypatch.setattr(db, "db_enabled", lambda: False)
    assert app._hl_decisions() == {"error": "DB_NOT_CONFIGURED", "signals": []}


def test_endpoint(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    c = app.app.test_client()
    assert c.get("/api/hl/decisions").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")
    seen = {}
    monkeypatch.setattr(app, "_hl_decisions",
                        lambda d, m: seen.update(d=d, m=m) or {"counts": {}, "signals": []})
    r = c.get("/api/hl/decisions?days=99&min=65",
              headers={"x-cron-secret": "s3cret-s3cret-s3cret"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert seen == {"d": 14, "m": 65.0}


def test_was_sent(monkeypatch, mem_kv):
    import ops_alerts
    _, keys = mem_kv
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    keys.add("ops:local:stale:s1:7")
    assert ops_alerts.was_sent("stale:s1:7") is True
    assert ops_alerts.was_sent("stale:s1:8") is False
