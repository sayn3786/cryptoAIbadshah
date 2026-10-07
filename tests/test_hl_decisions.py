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

    calls = []

    class Store:
        def list_recorded_since(self, since, strategy_names=(), min_strength=None, limit=0):
            calls.append((since, tuple(strategy_names), min_strength))
            out = [r for n in strategy_names for r in rows[n]]
            return [r for r in out if float(r["confidence_score"]) >= (min_strength or 0)]

    monkeypatch.setattr(db, "db_enabled", lambda: True)
    monkeypatch.setattr(app, "_signal_store", lambda: Store())
    monkeypatch.setattr(hl_account, "configured", lambda: True)
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {"ADA": {}, "ENJ": {}, "AAVE": {}})
    monkeypatch.setattr(hl_meta, "resolve_coin",
                        lambda sym, table: sym if sym in table else None)
    import hl_execution as hx
    placed = {hx.client_order_id("b", "open", "2026-09-29T12:00:00+00:00"): 555}
    monkeypatch.setattr(hl_account, "order_status",
                        lambda cloid: {"known": True, "status": "filled", "oid": placed[cloid]}
                        if cloid in placed else {"known": False})
    monkeypatch.setattr(hl_account, "fills_since", lambda start: [
        {"oid": 555, "px": "0.80", "sz": "10", "time": 1759148000000},
        {"oid": 555, "px": "0.82", "sz": "10", "time": 1759148001000}])
    monkeypatch.setattr(hl_account, "account_address", lambda a=None: "0x1234567890abcdef1234")
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
    assert len(calls) == 1 and calls[0][2] == 69                 # one database read
    assert set(calls[0][1]) == {sp.STRATEGY_NAME, sp.HL_EXTRA_STRATEGY_NAME}
    assert "elapsed_ms" in out
    assert "filled 20 @ 0.81" in by["ADA"]["hl"]
    assert out["account"] == "0x1234…1234" and out["fills_read"] is True


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


def test_store_read_is_one_statement_with_archived_rows():
    import inspect
    import signal_store
    src = inspect.getsource(signal_store.list_recorded_since)
    assert src.count("s.execute(") == 1 and "archived_at" not in src
    assert signal_store.list_recorded_since(datetime.now(timezone.utc),
                                            strategy_names=[]) == []


# ── order status ─────────────────────────────────────────────────────────────

def test_placed_verdicts_use_the_order_status():
    def code(order):
        return app._hl_history_verdict(listed=True, placed=True, runs=[], stale_alert=False,
                                       slot_runs=[], order=order)
    fill = {"px": 0.81, "sz": 20.0, "time": 1759148000000}
    assert code({"status": "filled", "fill": fill})[0] == "opened"
    assert "filled 20 @ 0.81 at" in code({"status": "filled", "fill": fill})[1]
    assert code({"status": "filled", "fill": None})[0] == "opened"
    assert code({"status": "open", "fill": None})[1].startswith("order on HL, not filled")
    c, t = code({"status": "canceled", "fill": None})
    assert c == "order_failed" and "canceled" in t
    assert code({"status": "rejected", "fill": None})[0] == "order_failed"
    assert code(None)[0] == "opened"


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class _Sess:
    def __init__(self, body):
        self.body, self.sent = body, []

    def post(self, url, json=None, timeout=None):
        self.sent.append(json)
        return _Resp(self.body)


def test_order_status_parses_the_exchange_answer(monkeypatch):
    import hl_account as ha
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xabc0000000000000000000")
    body = {"status": "order", "order": {"status": "canceled", "statusTimestamp": 9,
            "order": {"oid": 77, "coin": "TAO", "side": "B", "origSz": "0.5",
                      "timestamp": 8}}}
    sess = _Sess(body)
    got = ha.order_status("0xcloid", session=sess)
    assert got == {"known": True, "status": "canceled", "oid": 77, "coin": "TAO", "side": "B",
                   "orig_sz": 0.5, "placed_ms": 8, "status_ms": 9}
    assert sess.sent[0] == {"type": "orderStatus", "user": "0xabc0000000000000000000",
                            "oid": "0xcloid"}
    assert ha.order_status("0xc", session=_Sess({"status": "unknownOid"})) == {"known": False}
    assert ha.order_status("0xc", session=_Sess({"status": "weird"})) is None


def test_fills_by_oid():
    import hl_account as ha
    got = ha.fills_by_oid([{"oid": 1, "px": "10", "sz": "1", "time": 5},
                           {"oid": 1, "px": "12", "sz": "3", "time": 4},
                           {"oid": 2, "px": "x", "sz": "1"}, "junk"])
    assert got == {1: {"px": 11.5, "sz": 4.0, "time": 4.0}}


def test_a_failed_fills_read_still_reports(mem_kv, monkeypatch):
    import db
    import hl_account
    import hl_meta
    monkeypatch.setattr(db, "db_enabled", lambda: True)

    class Store:
        def list_recorded_since(self, since, **kw):
            return [row("a", "TAO", 74.1, datetime.now(timezone.utc).isoformat())]

    monkeypatch.setattr(app, "_signal_store", lambda: Store())
    monkeypatch.setattr(hl_account, "configured", lambda: True)
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {"TAO": {}})
    monkeypatch.setattr(hl_meta, "resolve_coin", lambda sym, table: sym)

    def boom(start):
        raise RuntimeError("down")
    monkeypatch.setattr(hl_account, "fills_since", boom)
    monkeypatch.setattr(hl_account, "order_status",
                        lambda c: {"known": True, "status": "filled", "oid": 1})
    out = app._hl_decisions(days=2)
    assert out["fills_read"] is False and out["signals"][0]["code"] == "opened"
