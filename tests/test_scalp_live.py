"""
The live 15m scalp book (HL testnet only), sweep -> FVG: setups on the latest
close with the 1H read agreeing, resting limit entries with the stop and target
attached, the cancel / promote / time-out sweep, and the gates that keep it off
mainnet and out of the 4h book.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_live as sl                                                # noqa: E402
from _worker_schedule import worker_schedule                           # noqa: E402

M = sl.M15_MS
H = 3_600_000


def b(i, o, h, lo, c):
    return {"timestamp": i * M, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def setup_bars():
    """Swing high 110 at 5, swing low 100 at 12, sweep at 16, gap completed at 18."""
    bars = [b(i, 102, 102.2, 101.8, 102) for i in range(19)]
    bars[5] = b(5, 107, 110, 106, 108)
    bars[12] = b(12, 102, 102.1, 100, 101)
    bars[16] = b(16, 101, 101.5, 99.5, 101)
    bars[17] = b(17, 101, 105, 101, 104.5)
    bars[18] = b(18, 104.5, 106, 103, 105.5)
    return bars


NOW = 19 * M + 60_000           # one minute after candle 18 closed


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SCALP_ENABLED", "HL_SCALP_AUTO_EXECUTE", "SCALP_MAX_HOLD_HOURS", "SCALP_MAX_OPEN"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_and_switches(monkeypatch):
    assert sl.enabled() and sl.auto_enabled()
    assert sl.max_hold_ms() == 12 * H and sl.max_open() == 2 and sl.ENTRY_MS == 2 * H
    monkeypatch.setenv("HL_SCALP_AUTO_EXECUTE", "0")
    assert sl.enabled() and not sl.auto_enabled()
    monkeypatch.setenv("SCALP_ENABLED", "off")
    assert not sl.enabled() and not sl.auto_enabled()


def test_new_setup_only_on_the_latest_close():
    s = sl.new_setup(setup_bars(), "eth", now_ms=NOW)
    assert s == {"symbol": "ETH", "direction": "LONG", "limit": 103, "sl": s["sl"],
                 "target": 110, "rr": s["rr"], "slot_ms": 19 * M}
    assert s["sl"] < 99.5 and s["rr"] >= 1.5
    assert sl.new_setup(setup_bars(), "ETH", now_ms=NOW + M) is None          # stale
    more = setup_bars() + [b(19, 105, 105.5, 104.5, 105)]
    assert sl.new_setup(more, "ETH", now_ms=20 * M + 60_000) is None          # gap was earlier
    assert sl.new_setup([], "ETH", now_ms=NOW) is None


def test_h1_agreement():
    s = {"symbol": "ETH", "direction": "LONG"}
    h1 = [b(i, 1, 1, 1, 1) for i in range(sl.H1_WINDOW)]
    assert sl.h1_agrees(s, h1, lambda w, tf, sym: {"direction": "LONG"})
    assert not sl.h1_agrees(s, h1, lambda w, tf, sym: {"direction": "SHORT"})
    assert not sl.h1_agrees(s, h1[:-1], lambda w, tf, sym: {"direction": "LONG"})
    seen = []
    sl.h1_agrees(s, h1, lambda w, tf, sym: seen.append((len(w), tf)) or {})
    assert seen == [(sl.H1_WINDOW, "1H")]


def rec(state="pending", **kw):
    r = {"coin": "ETH", "symbol": "ETH", "direction": "LONG", "limit": 100.0, "sl": 98.0,
         "target": 104.0, "oid": 7, "placed_ms": 0, "state": state}
    r.update(kw)
    return r


def test_sweep_pending_entries():
    orders = [{"oid": 7, "coin": "ETH"}]
    pos = [{"coin": "ETH", "side": "long", "entry_px": 100.2}]
    assert sl.sweep({"ETH": rec()}, pos, orders, {}, now_ms=H)["promote"] == ["ETH"]
    assert sl.sweep({"ETH": rec()}, [], orders, {"ETH": "99"}, now_ms=H)["cancel"] == []
    assert sl.sweep({"ETH": rec()}, [], orders, {"ETH": "99"}, now_ms=2 * H)["cancel"] == ["ETH"]
    assert sl.sweep({"ETH": rec()}, [], orders, {"ETH": "97.9"}, now_ms=H)["cancel"] == ["ETH"]
    assert sl.sweep({"ETH": rec()}, [], [], {}, now_ms=H)["drop"] == ["ETH"]       # gone
    assert sl.sweep({"ETH": rec()}, [], None, {}, now_ms=5 * H) == \
        {"promote": [], "cancel": [], "close": [], "drop": []}                    # unknown: wait


def test_sweep_open_scalps():
    pos = [{"coin": "ETH", "side": "long", "entry_px": 100.5}]
    r = rec(state="open", opened_ms=0)
    assert sl.sweep({"ETH": r}, pos, [], {}, now_ms=12 * H)["close"] == ["ETH"]
    assert sl.sweep({"ETH": r}, pos, [], {}, now_ms=11 * H)["close"] == []
    assert sl.sweep({"ETH": r}, [{"coin": "ETH", "side": "short", "entry_px": 100}], [], {},
                    now_ms=H)["drop"] == ["ETH"]                                  # a 4h trade now
    assert sl.sweep({"ETH": r}, [], [], {}, now_ms=H)["drop"] == ["ETH"]


def test_room(monkeypatch):
    assert sl.room({}) == 2 and sl.room({"A": {}, "B": {}, "C": {}}) == 0
    monkeypatch.setenv("SCALP_MAX_OPEN", "4")
    assert sl.room({"A": {}}) == 3


def test_fetch_falls_back_and_keeps_closed_only():
    now = 500 * M + 30_000
    k = [[t * M, "1", "1", "1", "1", "1"] for t in range(500 - 250, 501)]

    def get(url, params):
        if "binance" in url:
            raise RuntimeError("down")
        return {"data": list(reversed(k))[:100] if params["after"] == str(now) else []}
    got = sl.fetch_closed("ETHUSDT", now, get=get)
    assert got and all(c["timestamp"] + M <= now for c in got)
    got = sl.fetch_closed("ETHUSDT", now, get=lambda u, p: k)
    assert got[-1]["timestamp"] == 499 * M and len(got) == 245


def test_message_is_honest():
    s = {"symbol": "ETH", "direction": "LONG", "limit": 100.0, "sl": 98.0, "target": 104.0,
         "rr": 2.0}
    m = sl.message([s], auto=True)
    assert "testnet" in m and "break-even" in m and "ETH LONG" in m and "limit 100" in m


def test_entry_status_parsing():
    import hl_exchange as he
    ok = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 5}}, "waitingForFill"]}}}
    assert he.entry_status(ok) == {"state": "resting", "oid": 5}
    filled = {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"oid": 6, "avgPx": "101.5", "totalSz": "1"}}]}}}
    assert he.entry_status(filled) == {"state": "filled", "oid": 6, "avg_px": 101.5}
    assert he.entry_status({"status": "ok", "response": {"data": {"statuses": [
        {"error": "Insufficient margin"}]}}})["state"] == "error"
    assert he.entry_status("boom")["state"] == "error"


def test_manager_keeps_exits_attached_to_a_resting_entry():
    import hl_manage
    orders = [{"coin": "ETH", "oid": 1, "reduceOnly": False, "isTrigger": False, "timestamp": 0},
              {"coin": "ETH", "oid": 2, "reduceOnly": True, "isTrigger": True, "timestamp": 0},
              {"coin": "SOL", "oid": 3, "reduceOnly": True, "isTrigger": True, "timestamp": 0}]
    acts = hl_manage.plan([], orders, {}, {}, now_ms=10 ** 9)
    assert acts == [{"coin": "SOL", "action": "cancel_orphans", "cancel_oids": [3]}]


# ── the app side ────────────────────────────────────────────────────────────

@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    import kv
    mem = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "get_value", lambda k: mem.get(k))
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=0: (k not in mem)
                        and (mem.__setitem__(k, "1") or True))
    sent = []
    monkeypatch.setattr(app, "_scalp_private", lambda t: sent.append(t))
    app._sent = sent
    return app


def test_run_alerts_each_setup_once_and_needs_1h(app_mod, monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod, "_scalp_hl", lambda s, now: calls.append(s) or {"ran": False})
    h1 = [b(i, 1, 1, 1, 1) for i in range(sl.H1_WINDOW)]
    fetch = lambda sym, iv: setup_bars() if iv == "15m" else h1                  # noqa: E731
    read = lambda w, tf, s: {"direction": "LONG" if s == "ETH" else "SHORT"}      # noqa: E731
    out = app_mod._scalp_run(NOW, fetch_fn=fetch, read_fn=read)
    assert out["signals"] == ["ETH LONG R:R " + f"{calls[0][0]['rr']:g}"] and out["new"] == 1
    assert len(app_mod._sent) == 1 and "ETH LONG" in app_mod._sent[0]
    again = app_mod._scalp_run(NOW, fetch_fn=fetch, read_fn=read)
    assert again["new"] == 0 and len(app_mod._sent) == 1 and calls[1] == []


def test_disabled_does_nothing(app_mod, monkeypatch):
    monkeypatch.setenv("SCALP_ENABLED", "0")
    assert app_mod._scalp_run(0)["reason"] == "SCALP_DISABLED"


def test_hl_refuses_mainnet_and_respects_the_switch(app_mod, monkeypatch):
    import hl_account
    monkeypatch.setattr(hl_account, "_env", lambda env=None: "mainnet")
    assert app_mod._scalp_hl([], 0) == {"ran": False, "reason": "TESTNET_ONLY"}
    monkeypatch.setenv("HL_SCALP_AUTO_EXECUTE", "off")
    assert app_mod._scalp_hl([], 0)["reason"] == "HL_SCALP_AUTO_EXECUTE_OFF"


@pytest.fixture
def hl_fake(app_mod, monkeypatch):
    import hl_account
    import hl_exchange
    import hl_execution
    import hl_meta
    st = {"positions": [], "orders": [], "sent": [], "cancelled": [], "closed": [],
          "known": False}
    monkeypatch.setattr(hl_account, "_env", lambda env=None: "testnet")
    monkeypatch.setattr(hl_execution, "arm_status", lambda: {"armed": True})
    monkeypatch.setattr(hl_account, "account_state", lambda: {
        "open_positions": st["positions"], "account_value_usd": 1000.0})
    monkeypatch.setattr(hl_account, "spot_usdc", lambda: 0.0)
    monkeypatch.setattr(hl_account, "_post_info", lambda *a, **k: st["orders"])
    monkeypatch.setattr(hl_account, "account_address", lambda: "0xabc")
    monkeypatch.setattr(hl_account, "all_mids", lambda env=None: {"ETH": "100"})
    monkeypatch.setattr(hl_account, "order_known", lambda cloid, env=None: st["known"])
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {
        s: {"sz_decimals": 2, "max_leverage": 10, "asset_id": i}
        for i, s in enumerate(("ETH", "SOL", "XRP", "ADA"))})
    import app as _app
    monkeypatch.setattr(_app.client, "get_market_cap", lambda pair: 2e12)
    monkeypatch.setattr(hl_exchange, "send_limit_open_with_tpsl",
                        lambda coin, is_buy, size, px, sl_px, tp_px, **kw:
                        st["sent"].append((coin, is_buy, px, sl_px, tp_px)) or
                        {"status": "ok", "response": {"data": {"statuses": [
                            {"resting": {"oid": 100 + len(st["sent"])}}]}}})
    monkeypatch.setattr(hl_exchange, "cancel_order",
                        lambda coin, oid, env=None: st["cancelled"].append((coin, oid)))
    monkeypatch.setattr(hl_exchange, "send_market_close",
                        lambda coin, cloid, env=None: st["closed"].append(coin) or {"status": "ok"})
    monkeypatch.setattr(hl_exchange, "order_accepted", lambda resp: (True, None))
    return st


def _setup(sym, d="LONG"):
    return {"symbol": sym, "direction": d, "limit": 10.0, "sl": 9.5, "target": 11.0, "rr": 2.0,
            "slot_ms": 1}


def test_places_limit_entries_within_room(app_mod, hl_fake):
    out = app_mod._scalp_hl([_setup("ETH"), _setup("SOL"), _setup("XRP")], H)
    assert [x[0] for x in hl_fake["sent"]] == ["ETH", "SOL"]                 # room for 2
    assert hl_fake["sent"][0] == ("ETH", True, 10.0, 9.5, 11.0)
    recs = app_mod._scalp_records()
    assert recs["ETH"]["state"] == "pending" and recs["ETH"]["oid"] == 101
    assert app_mod._scalp_busy_symbols() == {"ETH", "SOL"}
    assert any("placed ETH LONG limit 10" in t for t in app_mod._sent)
    # $1 risk / 5% stop = $20 notional: under the $25 margin, so 1x
    assert out["results"][0] == {"symbol": "ETH", "ok": True, "state": "pending",
                                 "notional_usd": 20.0, "leverage": 1.0}


def test_already_placed_and_held_coins_are_skipped(app_mod, hl_fake):
    hl_fake["positions"] = [{"coin": "ETH", "side": "long", "entry_px": 50}]
    hl_fake["known"] = True
    out = app_mod._scalp_hl([_setup("ETH"), _setup("SOL")], H)
    assert out["results"] == [{"symbol": "ETH", "ok": False, "reason": "POSITION_EXISTS"},
                              {"symbol": "SOL", "ok": False, "reason": "ALREADY_PLACED"}]
    assert hl_fake["sent"] == []


def test_cancels_a_stale_entry_with_its_exits(app_mod, hl_fake):
    app_mod._scalp_save({"ETH": rec(placed_ms=0)})
    hl_fake["orders"] = [{"oid": 7, "coin": "ETH", "reduceOnly": False},
                         {"oid": 8, "coin": "ETH", "reduceOnly": True}]
    app_mod._scalp_hl([], 2 * H)
    assert hl_fake["cancelled"] == [("ETH", 7), ("ETH", 8)]
    assert app_mod._scalp_records() == {}


def test_promotes_a_fill_then_times_it_out(app_mod, hl_fake):
    app_mod._scalp_save({"ETH": rec(placed_ms=0)})
    hl_fake["positions"] = [{"coin": "ETH", "side": "long", "entry_px": 100.1}]
    app_mod._scalp_hl([], H)
    assert app_mod._scalp_records()["ETH"]["state"] == "open"
    app_mod._scalp_hl([], H + 12 * H)
    assert hl_fake["closed"] == ["ETH"] and app_mod._scalp_records() == {}


def test_4h_book_skips_scalp_coins():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py")).read()
    assert "busy = _scalp_busy_symbols()" in src


def test_scalp_never_touches_the_signals_table():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "scalp_live.py")).read()
    assert "signal_store" not in src and "persist_recommendations" not in src


def test_route_and_scheduler(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_scalp_run", lambda: {"ok": True})
    c = app_mod.app.test_client()
    monkeypatch.setenv("CRON_SECRET", "s3cret-value-123")
    assert c.post("/api/cron/scalp").status_code == 401
    assert c.post("/api/cron/scalp", headers={"x-cron-secret": "s3cret-value-123"}).status_code == 200
    assert "/api/cron/scalp" in app_mod._SCHEDULER_PATHS
    _ = json


def test_worker_runs_it_every_15_minutes():
    s = worker_schedule()
    assert len(s["scalp"]) == 96 and all(m % 15 == 1 for _h, m in s["scalp"])


def test_confirm_modes(monkeypatch):
    assert sl.confirm_mode() == "1h"
    s = {"symbol": "ETH", "direction": "LONG"}
    m15 = [b(i, 1, 1, 1, 1) for i in range(sl.H1_WINDOW)]
    h1 = lambda: m15                                                       # noqa: E731
    read = lambda w, tf, sym: {"direction": "LONG" if tf == "1H" else "SHORT"}  # noqa: E731
    assert sl.confirmed(s, m15, h1, read)
    for mode, want in (("15m", False), ("1h+15m", False), ("none", True), ("bogus", True)):
        monkeypatch.setenv("SCALP_CONFIRM", mode)
        assert sl.confirmed(s, m15, h1, read) is want
    monkeypatch.setenv("SCALP_CONFIRM", "15m")
    calls = []
    sl.confirmed(s, m15, lambda: calls.append(1) or m15, read)
    assert calls == []                                    # 1H not fetched when unused


def _fill(t, d, px, sz, pnl=0.0, fee=0.01, coin="ETH"):
    return {"coin": coin, "time": t, "dir": d, "px": str(px), "sz": str(sz),
            "closedPnl": str(pnl), "fee": str(fee)}


def test_settle_from_fills():
    r = rec(state="open", placed_ms=100)                  # limit 100, sl 98, target 104
    fills = [_fill(50, "Open Long", 99, 1), _fill(150, "Open Long", 100, 1),
             _fill(300, "Close Long", 104, 1, pnl=4.0), _fill(310, "Open Long", 1, 1, coin="SOL")]
    got = sl.settle(fills, r)
    assert got == {"fill_price": 100.0, "filled_ms": 150, "exit_price": 104.0, "closed_ms": 300,
                   "pnl_usd": 3.98, "fees_usd": 0.02, "exit_reason": "target"}
    be = sl.settle([_fill(150, "Open Long", 100, 1), _fill(300, "Close Long", 100.1, 1, 0.1)], r)
    assert be["exit_reason"] == "breakeven"
    stop = sl.settle([_fill(150, "Open Long", 100, 1), _fill(300, "Close Long", 97.9, 1, -2.1)], r)
    assert stop["exit_reason"] == "stop" and stop["pnl_usd"] == -2.12
    assert sl.settle([_fill(150, "Open Long", 100, 1)], r, timed_out=True)["exit_reason"] == "timeout"
    assert sl.settle([], r) is None


def test_store_summary_and_quiet_without_db(monkeypatch):
    import scalp_store
    rows = [{"status": "CLOSED", "pnl_usd": 2.0, "exit_reason": "target", "filled_at": "x"},
            {"status": "CLOSED", "pnl_usd": -1.0, "exit_reason": "stop", "filled_at": "x"},
            {"status": "CANCELLED"}, {"status": "PENDING"}]
    sm = scalp_store.summary(rows)
    assert sm["placed"] == 4 and sm["closed"] == 2 and sm["win_rate_pct"] == 50.0
    assert sm["pnl_usd"] == 1.0 and sm["pf"] == 2.0 and sm["cancelled"] == 1 and sm["open"] == 1
    assert sm["by_reason"]["target"] == 1 and sm["by_reason"]["stop"] == 1
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert scalp_store.recent("test") == [] and scalp_store.placed("test", {}) is False


def test_drop_records_the_close(app_mod, hl_fake, monkeypatch):
    import hl_account
    import scalp_store
    seen = []
    monkeypatch.setattr(scalp_store, "closed", lambda env, r, **kw: seen.append(kw["result"]))
    app_mod._scalp_save({"ETH": rec(state="open", placed_ms=0, opened_ms=0, slot_ms=1)})
    fills = [_fill(10, "Open Long", 100, 1), _fill(20, "Close Long", 104, 1, pnl=4.0)]
    monkeypatch.setattr(hl_account, "_post_info", lambda body, **k:
                        fills if body.get("type") == "userFills" else [])
    app_mod._scalp_hl([], H)
    assert seen and seen[0]["exit_reason"] == "target"
    assert any("ETH closed at target" in t for t in app_mod._sent)


def test_size_plan_risk_and_market_cap_leverage(monkeypatch):
    p = sl.size_plan(100, 99.6, 2e12)                     # BTC-like: 0.4% stop
    assert p == {"notional_usd": 250.0, "leverage": 10, "stop_pct": 0.4, "cap": 20}
    assert sl.size_plan(100, 99, 2e10)["notional_usd"] == 100.0            # 1% stop
    assert sl.size_plan(100, 99.9, 2e10) == {"notional_usd": 250.0, "leverage": 10,
                                             "stop_pct": 0.2, "cap": 10}   # floor 0.2%, cap
    assert sl.size_plan(1, 0.99, 5e8, hl_max=3)["leverage"] == 3
    assert [sl.lev_cap(x) for x in (2e11, 5e10, 5e9, 5e8, 1e8, None)] == [20, 10, 7, 5, 3, 7]
    monkeypatch.setenv("SCALP_RISK_USD", "2")
    assert sl.size_plan(100, 99, 2e10)["notional_usd"] == 200.0
    assert sl.max_exposure_usd() == 600.0


def test_scalp_exposure_cap(app_mod, hl_fake, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_EXPOSURE_USD", "30")
    app_mod._scalp_save({"SOL": rec(coin="SOL", symbol="SOL", notional_usd=20.0, oid=9)})
    hl_fake["orders"] = [{"oid": 9, "coin": "SOL"}]
    out = app_mod._scalp_hl([_setup("ETH")], H)
    assert out["results"][0]["reason"] == "MAX_EXPOSURE"
