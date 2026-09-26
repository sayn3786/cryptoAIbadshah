"""
Hyperliquid auto-execute (Phase 4b): the publish-run batch over the Confirmed
tier, with stop + TP1 attached. Pure decision loop tested without SDK/DB/network
via injected open_fn/exit_fn; plus the switch defaults and endpoint gating.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_autoexec as ax                                              # noqa: E402


# ── the switch is OFF by default and separate from the arm gate ──────────────

def test_auto_execute_is_off_by_default(monkeypatch):
    monkeypatch.delenv("HL_AUTO_EXECUTE", raising=False)
    assert ax.is_auto_enabled() is False
    monkeypatch.setenv("HL_AUTO_EXECUTE", "on")
    assert ax.is_auto_enabled() is True


def test_gate_not_ready_unless_armed_and_enabled(monkeypatch):
    for n in ("LIVE_TRADING_ENABLED", "HL_KILL_SWITCH", "HYPERLIQUID_AGENT_KEY",
              "HYPERLIQUID_ACCOUNT_ADDRESS", "HL_AUTO_EXECUTE"):
        monkeypatch.delenv(n, raising=False)
    g = ax.gate_status()
    assert g["ready"] is False and g["auto_enabled"] is False
    assert "HL_AUTO_EXECUTE is off" in g["reasons"]

    # Armed but auto still off -> not ready.
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "on")
    monkeypatch.setenv("HYPERLIQUID_AGENT_KEY", "0xKEY")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    assert ax.gate_status()["ready"] is False

    # Both on -> ready.
    monkeypatch.setenv("HL_AUTO_EXECUTE", "on")
    g2 = ax.gate_status()
    assert g2["ready"] is True and g2["armed"] is True


def test_min_strength_defaults_to_the_confirmed_floor(monkeypatch):
    monkeypatch.delenv("HL_AUTO_MIN_STRENGTH", raising=False)
    assert ax.auto_min_strength() == 69.0
    monkeypatch.setenv("HL_AUTO_MIN_STRENGTH", "80")
    assert ax.auto_min_strength() == 80.0


# ── selecting the Confirmed tier from the latest slot ────────────────────────

def _row(sym, cs, direction="LONG", entry=100.0, close="2026-09-19T12:00:00Z",
         sl=98.0, tps=(103.0, 106.0)):
    return {"id": f"id-{sym}", "symbol": sym, "confidence_score": cs,
            "direction": direction, "entry_price": entry,
            "candle_close_time": close, "stop_loss": sl,
            "targets": [{"target_number": i + 1, "target_price": p}
                        for i, p in enumerate(tps)]}


def test_select_confirmed_filters_below_the_floor(monkeypatch):
    monkeypatch.delenv("HL_AUTO_MIN_STRENGTH", raising=False)
    rows = [_row("BTC", 75), _row("ETH", 60), _row("SOL", 69), _row("XRP", 68.9)]
    got = {r["symbol"] for r in ax.select_confirmed(rows)}
    assert got == {"BTC", "SOL"}                          # >=69 only


def test_select_confirmed_keeps_only_the_latest_slot():
    rows = [_row("BTC", 90, close="2026-09-19T16:00:00Z"),
            _row("ETH", 90, close="2026-09-19T12:00:00Z")]   # older slot
    got = ax.select_confirmed(rows)
    assert [r["symbol"] for r in got] == ["BTC"]


def test_select_confirmed_rejects_bad_geometry_and_direction():
    rows = [_row("BTC", 90, direction="NEUTRAL"),
            _row("ETH", 90, entry=0),
            _row("SOL", 90, entry=None)]
    assert ax.select_confirmed(rows) == []


def test_to_signal_maps_fields_and_tp1():
    sig = ax.to_signal(_row("BTC", 90, sl=97.5, tps=(103.0, 106.0)))
    assert sig["symbol"] == "BTC" and sig["direction"] == "LONG"
    assert sig["entry"] == 100.0 and sig["sl"] == 97.5
    assert sig["tp1"] == 103.0                            # first rung
    assert sig["id"] == "id-BTC"


# ── the guarded batch: caps, exit attachment, exposure reflection ────────────

def _cfg(max_orders=3):
    return {"max_orders_per_run": max_orders, "notional_usd": 12.0,
            "leverage": 3, "max_exposure_usd": 100.0}


def _table():
    return {"BTC": {"asset_id": 0, "sz_decimals": 3, "max_leverage": 50},
            "ETH": {"asset_id": 1, "sz_decimals": 2, "max_leverage": 50},
            "SOL": {"asset_id": 2, "sz_decimals": 2, "max_leverage": 50}}


def test_execute_opens_each_and_attaches_exits():
    seen_counts, exits = [], []

    def open_fn(sig, *, account_state, table, run_order_count, cfg, env):
        seen_counts.append(run_order_count)
        return {"ok": True, "coin": sig["symbol"], "size": 0.01,
                "notional_usd": 12.0, "side": "buy"}

    def exit_fn(coin, is_buy_exit, size, sl_px, tp_px, *, env, sl_cloid, tp_cloid, **split):
        exits.append({"coin": coin, "is_buy_exit": is_buy_exit,
                      "size": size, "sl": sl_px, "tp": tp_px})
        return {"sl": "ok", "tp": "ok"}

    signals = [ax.to_signal(_row("BTC", 90)), ax.to_signal(_row("ETH", 80))]
    acct = {"open_positions": []}
    out = ax.execute(signals, account_state=acct, table=_table(), cfg=_cfg(),
                     open_fn=open_fn, exit_fn=exit_fn)

    assert out["attempted"] == 2 and out["executed"] == 2
    assert seen_counts == [0, 1]                          # run_order_count threaded
    assert all(r["exits_ok"] for r in out["results"])
    # LONG exits by SELLING; prices rounded and passed through.
    assert exits[0]["is_buy_exit"] is False
    assert exits[0]["sl"] and exits[0]["tp"]
    # each fill reflected back into the snapshot for the next signal's guards
    assert len(acct["open_positions"]) == 2


def test_execute_short_exits_by_buying():
    exits = []

    def open_fn(sig, *, account_state, table, run_order_count, cfg, env):
        return {"ok": True, "coin": sig["symbol"], "size": 0.5, "notional_usd": 12.0}

    def exit_fn(coin, is_buy_exit, size, sl_px, tp_px, *, env, sl_cloid, tp_cloid, **split):
        exits.append(is_buy_exit)
        return {}

    sig = ax.to_signal(_row("ETH", 90, direction="SHORT", entry=100.0,
                            sl=103.0, tps=(97.0, 94.0)))
    ax.execute([sig], account_state={"open_positions": []}, table=_table(),
               cfg=_cfg(), open_fn=open_fn, exit_fn=exit_fn)
    assert exits == [True]                                # SHORT closes by buying


def test_execute_skips_exits_on_a_rejected_open():
    exits = []

    def open_fn(sig, *, account_state, table, run_order_count, cfg, env):
        return {"ok": False, "reason": "POSITION_EXISTS", "coin": sig["symbol"]}

    def exit_fn(*a, **k):
        exits.append(1)
        return {}

    out = ax.execute([ax.to_signal(_row("BTC", 90))],
                     account_state={"open_positions": []}, table=_table(),
                     cfg=_cfg(), open_fn=open_fn, exit_fn=exit_fn)
    assert out["executed"] == 0 and exits == []
    assert out["results"][0]["ok"] is False


def test_execute_records_an_exit_failure_without_raising():
    def open_fn(sig, *, account_state, table, run_order_count, cfg, env):
        return {"ok": True, "coin": sig["symbol"], "size": 0.01, "notional_usd": 12.0}

    def exit_fn(*a, **k):
        raise RuntimeError("exchange rejected the stop")

    out = ax.execute([ax.to_signal(_row("BTC", 90))],
                     account_state={"open_positions": []}, table=_table(),
                     cfg=_cfg(), open_fn=open_fn, exit_fn=exit_fn)
    r = out["results"][0]
    assert r["ok"] is True                                # the position still opened
    assert r["exits_ok"] is False and "rejected" in r["exits_error"]


# ── the endpoints are internal and gate on readiness ─────────────────────────

def _app():
    pytest.importorskip("flask")
    import app
    return app


def test_auto_execute_endpoint_requires_internal_auth(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    r = app.app.test_client().post("/api/hl/auto-execute")
    assert r.status_code == 401


def test_auto_execute_endpoint_reports_not_ready_when_disarmed(monkeypatch):
    app = _app()
    monkeypatch.setenv("HL_ADMIN_TOKEN", "hl-token-123")
    for n in ("LIVE_TRADING_ENABLED", "HYPERLIQUID_AGENT_KEY", "HL_AUTO_EXECUTE"):
        monkeypatch.delenv(n, raising=False)
    r = app.app.test_client().post("/api/hl/auto-execute",
                                   headers={"x-hl-token": "hl-token-123"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ran"] is False and body["reason"] == "NOT_READY"


def test_auto_status_endpoint_is_internal(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    assert app.app.test_client().get("/api/hl/auto-status").status_code == 401
