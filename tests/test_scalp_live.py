"""
The live 15m scalp book (HL testnet only): signal reads, the max-hold sweep,
the scheduler job, and the gates that keep it off mainnet and off the 4h book.
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


def bars(n, end_close):
    return [{"timestamp": end_close - (n - i) * M, "open": 1, "high": 1.01, "low": 0.99,
             "close": 1, "volume": 1} for i in range(n)]


def sig(d="LONG", s=80):
    return {"direction": d, "strength": s, "entry": 100, "sl": 98, "tp_targets": [102, 104, 106]}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SCALP_ENABLED", "HL_SCALP_AUTO_EXECUTE", "SCALP_MIN_STRENGTH",
              "SCALP_MAX_HOLD_HOURS", "SCALP_MAX_OPEN"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_and_switches(monkeypatch):
    assert sl.enabled() and sl.auto_enabled()
    assert sl.min_strength() == 75 and sl.max_hold_ms() == 12 * H and sl.max_open() == 2
    monkeypatch.setenv("HL_SCALP_AUTO_EXECUTE", "0")
    assert sl.enabled() and not sl.auto_enabled()
    monkeypatch.setenv("SCALP_ENABLED", "off")
    assert not sl.enabled() and not sl.auto_enabled()


def test_read_needs_a_fresh_close_the_floor_and_a_ladder():
    now = 1000 * M + 60_000
    b = bars(sl.WINDOW, 1000 * M)
    got = sl.read(b, "eth", lambda *a: sig(), now_ms=now)
    assert got["symbol"] == "ETH" and got["candle_ts"] == 1000 * M and got["strength"] == 80
    assert sl.read(b, "ETH", lambda *a: sig(), now_ms=now + M) is None        # stale
    assert sl.read(b[:-1] if False else b[1:], "ETH", lambda *a: sig(), now_ms=now) is None
    assert sl.read(b, "ETH", lambda *a: sig(s=70), now_ms=now) is None        # below 75
    assert sl.read(b, "ETH", lambda *a: {**sig(), "tp_targets": []}, now_ms=now) is None
    assert sl.read(b, "ETH", lambda *a: {"direction": "NEUTRAL"}, now_ms=now) is None
    seen = []
    sl.read(b, "ETH", lambda w, tf, s: seen.append((len(w), tf)) or sig(), now_ms=now)
    assert seen == [(sl.WINDOW, "15M")]


def test_to_hl_keeps_its_own_stop():
    s = sl.read(bars(sl.WINDOW, 10 * M + sl.WINDOW * M), "SOL", lambda *a: sig("SHORT"),
                now_ms=10 * M + sl.WINDOW * M + 1)
    h = sl.to_hl(s)
    assert h["id"] == "scalp-SOL" and h["tp1"] == 102 and h["tp2"] == 104 and "atr" not in h
    assert h["direction"] == "SHORT" and h["confidence_score"] == 80


def test_sweep_times_out_and_drops():
    recs = {"ETH": {"coin": "ETH", "direction": "LONG", "entry": 100, "opened_ms": 0},
            "SOL": {"coin": "SOL", "direction": "SHORT", "entry": 50, "opened_ms": 0},
            "BTC": {"coin": "BTC", "direction": "LONG", "entry": 100, "opened_ms": 11 * H}}
    pos = [{"coin": "ETH", "side": "long", "entry_px": 100.5},
           {"coin": "SOL", "side": "long", "entry_px": 50},            # a 4h trade now
           {"coin": "BTC", "side": "long", "entry_px": 101}]
    assert sl.sweep(recs, pos, now_ms=12 * H) == {"close": ["ETH"], "drop": ["SOL"]}
    assert sl.sweep(recs, [], now_ms=0) == {"close": [], "drop": ["ETH", "SOL", "BTC"]}
    assert not sl.held([{"coin": "ETH", "side": "long", "entry_px": 110}], recs["ETH"])


def test_room(monkeypatch):
    assert sl.room({}) == 2 and sl.room({"A": {}, "B": {}, "C": {}}) == 0
    monkeypatch.setenv("SCALP_MAX_OPEN", "4")
    assert sl.room({"A": {}}) == 3


def test_fetch_falls_back_and_keeps_closed_only():
    now = 500 * M + 30_000
    k = [[t * M, "1", "1", "1", "1", "1"] for t in range(500 - 250, 501)]   # incl. the forming one

    def get(url, params):
        if "binance" in url:
            raise RuntimeError("down")
        return {"data": list(reversed(k))[:100] if params["after"] == str(now) else []}
    got = sl.fetch_closed("ETHUSDT", now, get=get)
    assert got and all(c["timestamp"] + M <= now for c in got)
    got = sl.fetch_closed("ETHUSDT", now, get=lambda u, p: k)
    assert got[-1]["timestamp"] == 499 * M and len(got) == 250


def test_message_is_honest():
    s = {"symbol": "ETH", "direction": "LONG", "strength": 80.0, "entry": 100.0, "sl": 98.0,
         "tp_targets": [102.0, 104.0]}
    m = sl.message([s], auto=True)
    assert "testnet" in m and "lost in both periods" in m and "ETH LONG 80" in m


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


def test_run_alerts_each_candle_once(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_scalp_hl", lambda sigs, now: {"ran": False, "reason": "X"})
    now = 1000 * M + 60_000
    b = bars(sl.WINDOW, 1000 * M)
    read = lambda w, tf, s: sig() if s in ("ETH", "SOL") else {}        # noqa: E731
    out = app_mod._scalp_run(now, fetch_fn=lambda s: b, read_fn=read)
    assert out["signals"] == ["ETH LONG 80", "SOL LONG 80"] and out["new"] == 2
    assert len(app_mod._sent) == 1 and "ETH LONG 80" in app_mod._sent[0]
    again = app_mod._scalp_run(now, fetch_fn=lambda s: b, read_fn=read)
    assert again["new"] == 0 and len(app_mod._sent) == 1


def test_disabled_does_nothing(app_mod, monkeypatch):
    monkeypatch.setenv("SCALP_ENABLED", "0")
    assert app_mod._scalp_run(0)["reason"] == "SCALP_DISABLED"


def test_hl_refuses_mainnet_and_respects_the_switch(app_mod, monkeypatch):
    import hl_account
    monkeypatch.setattr(hl_account, "_env", lambda env=None: "mainnet")
    assert app_mod._scalp_hl([], 0) == {"ran": False, "reason": "TESTNET_ONLY"}
    monkeypatch.setenv("HL_SCALP_AUTO_EXECUTE", "off")
    assert app_mod._scalp_hl([], 0)["reason"] == "HL_SCALP_AUTO_EXECUTE_OFF"


def test_hl_times_out_then_opens_within_room(app_mod, monkeypatch):
    import hl_account
    import hl_autoexec
    import hl_exchange
    import hl_execution
    import hl_meta
    monkeypatch.setattr(hl_account, "_env", lambda env=None: "testnet")
    monkeypatch.setattr(hl_execution, "arm_status", lambda: {"armed": True})
    monkeypatch.setattr(hl_account, "account_state", lambda: {"open_positions": [
        {"coin": "ETH", "side": "long", "entry_px": 100}]})
    monkeypatch.setattr(hl_account, "_post_info", lambda *a, **k: [])
    monkeypatch.setattr(hl_account, "account_address", lambda: "0xabc")
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {})
    monkeypatch.setattr(hl_meta, "resolve_coin", lambda sym, table: sym)
    closes = []
    monkeypatch.setattr(hl_exchange, "send_market_close", lambda coin, cloid, env=None:
                        closes.append(coin) or {"status": "ok"})
    monkeypatch.setattr(hl_exchange, "order_accepted", lambda resp: (True, None))
    calls = []

    def execute(signals, **kw):
        calls.append([s["symbol"] for s in signals])
        return {"results": [{"ok": True, "coin": s["symbol"], "mark_px": 10.0} for s in signals]}
    monkeypatch.setattr(hl_autoexec, "execute", execute)
    app_mod._scalp_save({"ETH": {"coin": "ETH", "symbol": "ETH", "direction": "LONG",
                                 "entry": 100, "opened_ms": 0}})
    mk = lambda s: {"symbol": s, "direction": "LONG", "strength": 80.0, "entry": 10.0,   # noqa: E731
                    "sl": 9.0, "tp_targets": [11.0, 12.0], "candle_ts": 1}
    out = app_mod._scalp_hl([mk("SOL"), mk("XRP"), mk("ADA")], 12 * H)
    assert closes == ["ETH"] and out["timed_out"] == ["ETH"]
    assert calls == [["SOL", "XRP"]]                         # room for 2
    assert sorted(app_mod._scalp_records()) == ["SOL", "XRP"]
    assert any("opened SOL LONG 80" in t for t in app_mod._sent)


def test_scalp_never_touches_the_4h_book_or_the_signals_table():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "scalp_live.py")).read()
    assert "signal_store" not in src and "persist_recommendations" not in src


def test_route_and_scheduler(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_scalp_run", lambda: {"ok": True})
    c = app_mod.app.test_client()
    monkeypatch.setenv("CRON_SECRET", "s3cret-value-123")
    assert c.post("/api/cron/scalp").status_code == 401
    assert c.post("/api/cron/scalp", headers={"x-cron-secret": "s3cret-value-123"}).status_code == 200
    assert "/api/cron/scalp" in app_mod._SCHEDULER_PATHS


def test_worker_runs_it_every_15_minutes():
    s = worker_schedule()
    assert len(s["scalp"]) == 96 and all(m % 15 == 1 for _h, m in s["scalp"])
