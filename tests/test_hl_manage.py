"""
Scale-out exits and the break-even position manager.

  * auto-exec splits the take-profit 50/50 across TP1/TP2 when both halves clear
    Hyperliquid's $10 minimum, else closes everything at TP1 (as before);
  * once TP1 has filled, the manager moves the remainder's stop to ENTRY —
    placing the new stop before cancelling the old one — or closes the
    remainder if price is already back through entry.
No network: exchange reads and writes are injected fakes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_autoexec as ax                                               # noqa: E402
import hl_manage as hm                                                 # noqa: E402

OK = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 9}}]}}}
TABLE = {"FET": {"sz_decimals": 0}, "ETH": {"sz_decimals": 4}}


# ── the TP split at entry ────────────────────────────────────────────────────

def test_split_is_50_50_when_both_halves_clear_the_minimum():
    assert ax.plan_tp_split(40, 0, 0.66, 0.72, fraction=0.5) == (20, 20)


def test_odd_lot_gives_remainder_to_tp2_and_sums_to_size():
    tp1, tp2 = ax.plan_tp_split(41, 0, 0.66, 0.72, fraction=0.5)
    assert (tp1, tp2) == (20, 21) and tp1 + tp2 == 41


def test_no_split_below_the_10_dollar_minimum():
    # $12 position → two ~$6 halves: Hyperliquid would reject them.
    assert ax.plan_tp_split(20, 0, 0.62, 0.66, fraction=0.5) is None


def test_no_split_without_a_tp2():
    assert ax.plan_tp_split(40, 0, 0.66, None) is None


def test_attach_exits_sends_the_split(monkeypatch):
    sent = {}
    def exit_fn(coin, is_buy_exit, size, sl_px, tp_px, **kw):
        sent.update(kw, size=size, sl_px=sl_px, tp_px=tp_px, is_buy_exit=is_buy_exit)
        return {}
    sig = {"symbol": "ETH", "direction": "LONG", "id": "s1", "candle_ts": 1,
           "sl": 2900.0, "tp1": 3100.0, "tp2": 3200.0}
    res = {"ok": True, "coin": "ETH", "size": 0.008}
    ax._attach_exits(sig, res, table=TABLE, exit_fn=exit_fn, env=None)
    assert res["exits_ok"] and res["tp_split"] == [0.004, 0.004]
    assert sent["size"] == 0.008                         # stop covers the FULL size
    assert sent["tp_size"] == 0.004 and sent["tp2_size"] == 0.004
    assert sent["tp2_px"] == 3200.0 and sent["tp2_cloid"]


def test_attach_exits_falls_back_to_full_tp1_when_too_small():
    sent = {}
    def exit_fn(coin, is_buy_exit, size, sl_px, tp_px, **kw):
        sent.update(kw)
        return {}
    sig = {"symbol": "ETH", "direction": "LONG", "id": "s1", "candle_ts": 1,
           "sl": 2900.0, "tp1": 3100.0, "tp2": 3200.0}
    res = {"ok": True, "coin": "ETH", "size": 0.004}      # ~$12: halves < $10
    ax._attach_exits(sig, res, table=TABLE, exit_fn=exit_fn, env=None)
    assert res["tp_split"] is None and "tp2_px" not in sent


# ── the manager's plan (pure) ────────────────────────────────────────────────

def _pos(size, entry=0.60, side="long", coin="FET"):
    return {"coin": coin, "side": side, "size": size, "entry_px": entry}


def _stop(sz, px, oid=1, coin="FET"):
    return {"coin": coin, "isTrigger": True, "reduceOnly": True,
            "orderType": "Stop Market", "sz": str(sz), "triggerPx": str(px), "oid": oid}


def _tp(sz, px, oid=2, coin="FET"):
    return {"coin": coin, "isTrigger": True, "reduceOnly": True,
            "orderType": "Take Profit Market", "sz": str(sz), "triggerPx": str(px), "oid": oid}


def test_nothing_to_do_before_tp1():
    orders = [_stop(40, 0.55), _tp(20, 0.66, 2), _tp(20, 0.72, 3)]
    assert hm.plan([_pos(40)], orders, {"FET": "0.63"}, TABLE) == []


def test_moves_stop_to_entry_after_tp1_by_stop_size():
    orders = [_stop(40, 0.55, oid=7), _tp(20, 0.72, 3)]    # TP1 gone, position halved
    [a] = hm.plan([_pos(20)], orders, {"FET": "0.67"}, TABLE)
    assert a["action"] == "move_stop" and a["stop_px"] == 0.6
    assert a["size"] == 20 and a["cancel_oids"] == [7]


def test_detects_tp1_from_fill_history_even_if_stop_was_resized():
    # If the exchange shrank the stop to the remainder, sizes can't tell — the
    # fill history can: a Close after the latest Open.
    orders = [_stop(20, 0.55, oid=7), _tp(20, 0.72, 3)]
    fills = [{"coin": "FET", "dir": "Open Long", "time": 1000},
             {"coin": "FET", "dir": "Close Long", "time": 2000}]
    [a] = hm.plan([_pos(20)], orders, {"FET": "0.67"}, TABLE, fills)
    assert a["action"] == "move_stop"


def test_old_fill_history_from_a_previous_trade_is_ignored():
    fills = [{"coin": "FET", "dir": "Close Long", "time": 500},
             {"coin": "FET", "dir": "Open Long", "time": 1000}]
    orders = [_stop(40, 0.55), _tp(40, 0.66)]
    assert hm.plan([_pos(40)], orders, {"FET": "0.62"}, TABLE, fills) == []


def test_already_at_entry_is_a_no_op():
    orders = [_stop(20, 0.60, oid=8), _tp(20, 0.72, 3)]
    assert hm.plan([_pos(20)], orders, {"FET": "0.67"}, TABLE) == []


def test_leftover_old_stop_is_cancelled_once_entry_stop_exists():
    orders = [_stop(20, 0.60, oid=8), _stop(40, 0.55, oid=7), _tp(20, 0.72, 3)]
    [a] = hm.plan([_pos(20)], orders, {"FET": "0.67"}, TABLE)
    assert a == {"coin": "FET", "action": "cancel_stale", "cancel_oids": [7]}


def test_price_back_through_entry_closes_the_remainder():
    orders = [_stop(40, 0.55, oid=7), _tp(20, 0.72, 3)]
    [a] = hm.plan([_pos(20)], orders, {"FET": "0.59"}, TABLE)   # below entry
    assert a["action"] == "close_remainder"


def test_short_position_mirrors():
    orders = [_stop(0.008, 3100, oid=7, coin="ETH"), _tp(0.004, 2800, 3, coin="ETH")]
    pos = _pos(0.004, entry=3000, side="short", coin="ETH")
    [a] = hm.plan([pos], orders, {"ETH": "2900"}, TABLE)
    assert a["action"] == "move_stop" and a["stop_px"] == 3000
    [b] = hm.plan([pos], orders, {"ETH": "3050"}, TABLE)       # back above entry
    assert b["action"] == "close_remainder"


def test_position_without_a_stop_is_left_alone():
    assert hm.plan([_pos(20)], [_tp(20, 0.72)], {"FET": "0.67"}, TABLE) == []


# ── the manager's run (I/O injected) ─────────────────────────────────────────

def _arm(monkeypatch, env="testnet"):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "on")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    monkeypatch.setenv("HYPERLIQUID_AGENT_KEY", "0xKEY")
    monkeypatch.delenv("HL_KILL_SWITCH", raising=False)
    monkeypatch.setenv("HYPERLIQUID_ENV", env)
    monkeypatch.delenv("HL_ALLOW_MAINNET", raising=False)


def _reader(positions, orders, mids):
    return lambda: (positions, orders, mids, TABLE, [])


def test_run_places_new_stop_before_cancelling_old(monkeypatch):
    _arm(monkeypatch)
    calls = []
    out = hm.run(read_fn=_reader([_pos(20)], [_stop(40, 0.55, oid=7), _tp(20, 0.72, 3)], {"FET": "0.67"}),
                 stop_fn=lambda *a, **k: calls.append(("stop", a[3])) or OK,
                 cancel_fn=lambda coin, oid, env: calls.append(("cancel", oid)))
    assert calls == [("stop", 0.6), ("cancel", 7)]
    assert out["results"][0]["placed"] is True


def test_run_keeps_old_stop_when_new_one_is_rejected(monkeypatch):
    _arm(monkeypatch)
    cancelled = []
    rejected = {"status": "ok", "response": {"data": {"statuses": [{"error": "bad"}]}}}
    out = hm.run(read_fn=_reader([_pos(20)], [_stop(40, 0.55, oid=7)], {"FET": "0.67"}),
                 stop_fn=lambda *a, **k: rejected,
                 cancel_fn=lambda *a: cancelled.append(a))
    assert cancelled == [] and out["results"][0]["placed"] is False


def test_run_is_disarmed_by_default(monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    out = hm.run(read_fn=lambda: pytest.fail("must not read when disarmed"))
    assert out["ran"] is False and out["reason"] == "DISARMED"


def test_run_blocks_mainnet(monkeypatch):
    _arm(monkeypatch, env="mainnet")
    out = hm.run(read_fn=lambda: pytest.fail("must not act on mainnet"))
    assert out["reason"] == "MAINNET_NOT_ALLOWED"


# ── endpoint ─────────────────────────────────────────────────────────────────

def test_manage_endpoint_requires_auth(monkeypatch):
    pytest.importorskip("flask")
    import app
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    assert app.app.test_client().post("/api/hl/manage").status_code == 401


def test_manage_endpoint_runs_with_cron_secret(monkeypatch):
    pytest.importorskip("flask")
    import app
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    monkeypatch.setattr(hm, "run", lambda: {"ok": True, "ran": True, "actions": 0})
    r = app.app.test_client().post("/api/hl/manage", headers={"x-cron-secret": "s3cret"})
    assert r.status_code == 200 and r.get_json()["ran"] is True
