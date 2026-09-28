"""
v54 execution on Hyperliquid (testnet; defaults, each switchable by env):

  * the placed stop sits HL_STOP_ATR_ADD (1) x ATR(14, 2H) beyond the signal's;
  * the stop moves to entry once price has gone HL_BREAKEVEN_TRIGGER (1R) in
    favour, not when TP1 fills;
  * take-profits are resting reduce-only LIMIT orders (HL_TP_ORDER=limit);
  * reduce-only orders left on a coin with no position are cancelled (by the
    manager after 2 minutes, and by auto-exec right before it opens that coin),
    so a leftover TP can't close part of a later trade.
No network: exchange calls are injected fakes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_account as ha                                                # noqa: E402
import hl_autoexec as ax                                               # noqa: E402
import hl_exchange as hx                                               # noqa: E402
import hl_manage as hm                                                 # noqa: E402
import ops_alerts                                                      # noqa: E402

TABLE = {"FET": {"sz_decimals": 0}, "ETH": {"sz_decimals": 4}}
NOW = 10_000_000_000


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for k in ("HL_BREAKEVEN_TRIGGER", "HL_STOP_ATR_ADD", "HL_TP_ORDER"):
        monkeypatch.delenv(k, raising=False)


def _pos(size, entry=0.60, side="long", coin="FET"):
    return {"coin": coin, "side": side, "size": size, "entry_px": entry}


def _stop(sz, px, oid=1, coin="FET", ts=NOW):
    return {"coin": coin, "isTrigger": True, "reduceOnly": True, "timestamp": ts,
            "orderType": "Stop Market", "sz": str(sz), "triggerPx": str(px), "oid": oid}


def _ltp(sz, px, oid=2, coin="FET", ts=NOW):
    return {"coin": coin, "isTrigger": False, "reduceOnly": True, "timestamp": ts,
            "orderType": "Limit", "sz": str(sz), "limitPx": str(px), "oid": oid}


# ── stop + ATR ───────────────────────────────────────────────────────────────

def _candles(n, rng):
    return [{"timestamp": i, "open": 100, "high": 100 + rng / 2, "low": 100 - rng / 2,
             "close": 100} for i in range(n)]


def test_atr_is_the_mean_true_range_of_the_last_14():
    assert ax.atr(_candles(20, 2.0)) == pytest.approx(2.0)
    assert ax.atr(_candles(10, 2.0)) is None
    assert ax.atr([]) is None


def test_placed_stop_moves_one_atr_beyond_the_signal_stop():
    long = {"direction": "LONG", "sl": 0.55, "atr": 0.02}
    short = {"direction": "SHORT", "sl": 3100.0, "atr": 25.0}
    assert ax.placed_stop(long) == pytest.approx(0.53)
    assert ax.placed_stop(short) == pytest.approx(3125.0)
    assert ax.placed_stop({**long, "atr": None}) == 0.55          # no ATR: signal stop
    assert ax.placed_stop(long, add=0) == 0.55                    # v53 setting
    assert ax.placed_stop({"direction": "LONG", "sl": 1.0, "atr": 5.0}) == 0.01  # never <= 0


def test_stop_atr_add_env(monkeypatch):
    assert ax.stop_atr_add() == 1.0
    monkeypatch.setenv("HL_STOP_ATR_ADD", "0")
    assert ax.stop_atr_add() == 0
    monkeypatch.setenv("HL_STOP_ATR_ADD", "99")
    assert ax.stop_atr_add() == 1.0                               # out of range: default


def test_attach_exits_places_the_widened_stop_and_reports_the_signal_stop():
    sent = {}
    def exit_fn(coin, is_buy_exit, size, sl_px, tp_px, **kw):
        sent.update(sl_px=sl_px)
        return {}
    sig = {"symbol": "ETH", "direction": "LONG", "id": "s1", "candle_ts": 1,
           "sl": 2900.0, "tp1": 3100.0, "tp2": 3200.0, "atr": 30.0}
    res = {"ok": True, "coin": "ETH", "size": 0.008}
    ax._attach_exits(sig, res, table=TABLE, exit_fn=exit_fn, env=None)
    assert sent["sl_px"] == 2870.0
    assert res["exit_prices"]["sl"] == 2870.0 and res["exit_prices"]["signal_sl"] == 2900.0


def test_stale_entry_guard_still_uses_the_published_stop():
    # allowed drift = min(0.5 x 3%, 2%) = 1.5% of the SIGNAL stop, whatever the ATR
    cfg = hx.caps()
    assert hx.allowed_entry_drift(100.0, 97.0, cfg) == pytest.approx(0.015)


def test_opened_alert_shows_the_atr_buffer():
    res = {"coin": "ETH", "side": "buy", "size": 0.008, "notional_usd": 25, "leverage": 3,
           "mark_px": 3000.0, "exit_prices": {"sl": 2870.0, "tp": 3100.0, "tp2": None,
                                              "signal_sl": 2900.0}}
    text = ops_alerts.fmt_opened(res, {})
    assert "Stop 2,870.00 (signal stop 2,900.00 + ATR buffer)" in text


# ── break-even at 1R ─────────────────────────────────────────────────────────

def test_breakeven_trigger_env(monkeypatch):
    assert hm.breakeven_trigger() == 1.0
    for raw, want in (("tp1", "tp1"), ("1.5R", 1.5), ("0.8", 0.8), ("junk", 1.0), ("0", 1.0)):
        monkeypatch.setenv("HL_BREAKEVEN_TRIGGER", raw)
        assert hm.breakeven_trigger() == want


def test_no_move_before_1r_even_after_tp1_filled():
    # entry 0.60, stop 0.55 → 1R at 0.65. TP1 filled (position halved), mark 0.64.
    orders = [_stop(40, 0.55, oid=7), _ltp(20, 0.72, 3)]
    assert hm.plan([_pos(20)], orders, {"FET": "0.64"}, TABLE, now_ms=NOW) == []


def test_moves_to_entry_at_1r_even_before_tp1():
    orders = [_stop(40, 0.55, oid=7), _ltp(20, 0.70, 2), _ltp(20, 0.80, 3)]
    [a] = hm.plan([_pos(40)], orders, {"FET": "0.65"}, TABLE, now_ms=NOW)
    assert a["action"] == "move_stop" and a["stop_px"] == 0.6
    assert a["trigger"] == "1R" and a["cancel_oids"] == [7] and a["size"] == 40


def test_1r_uses_the_stop_that_fires_first_and_mirrors_for_shorts():
    orders = [_stop(0.008, 3100, oid=7, coin="ETH")]
    pos = _pos(0.008, entry=3000, side="short", coin="ETH")
    assert hm.plan([pos], orders, {"ETH": "2910"}, TABLE, now_ms=NOW) == []
    [a] = hm.plan([pos], orders, {"ETH": "2900"}, TABLE, now_ms=NOW)
    assert a["action"] == "move_stop" and a["stop_px"] == 3000


def test_custom_multiple():
    orders = [_stop(40, 0.55, oid=7)]
    assert hm.plan([_pos(40)], orders, {"FET": "0.65"}, TABLE, trigger=1.5, now_ms=NOW) == []
    [a] = hm.plan([_pos(40)], orders, {"FET": "0.675"}, TABLE, trigger=1.5, now_ms=NOW)
    assert a["trigger"] == "1.5R"


def test_after_the_move_it_is_a_no_op():
    orders = [_stop(40, 0.60, oid=8)]
    assert hm.plan([_pos(40)], orders, {"FET": "0.70"}, TABLE, now_ms=NOW) == []


def test_stop_moved_alert_says_why():
    assert "price reached 1R in profit" in ops_alerts.fmt_stop_moved(
        {"coin": "FET", "stop_px": 0.6, "size": 40, "trigger": "1R"})
    assert "TP1 hit" in ops_alerts.fmt_stop_moved({"coin": "FET", "stop_px": 0.6, "size": 20})


# ── orphans ──────────────────────────────────────────────────────────────────

def test_orders_of_a_closed_position_are_cancelled_after_2_minutes():
    old = NOW - 3 * 60 * 1000
    orders = [_stop(40, 0.55, oid=7, ts=old), _ltp(20, 0.72, oid=3, ts=old),
              _ltp(0.004, 3100, oid=9, coin="ETH", ts=old)]
    acts = hm.plan([_pos(0.004, entry=3000, coin="ETH")], orders, {"ETH": "3000"}, TABLE,
                   now_ms=NOW)
    assert acts == [{"coin": "FET", "action": "cancel_orphans", "cancel_oids": [7, 3]}]


def test_fresh_orders_are_never_treated_as_orphans():
    orders = [_stop(40, 0.55, oid=7, ts=NOW - 60_000), _ltp(20, 0.72, oid=3)]
    assert hm.plan([], orders, {}, TABLE, now_ms=NOW) == []


def test_non_reduce_only_orders_are_left_alone():
    o = {**_ltp(20, 0.72, oid=3, ts=0), "reduceOnly": False}
    assert hm.plan([], [o], {}, TABLE, now_ms=NOW) == []


def test_run_cancels_orphans(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "on")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    monkeypatch.setenv("HYPERLIQUID_AGENT_KEY", "0xKEY")
    monkeypatch.delenv("HL_KILL_SWITCH", raising=False)
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    cancelled = []
    orders = [_stop(40, 0.55, oid=7, ts=0)]
    out = hm.run(read_fn=lambda: ([], orders, {}, TABLE, []),
                 cancel_fn=lambda coin, oid, env: cancelled.append((coin, oid)))
    assert cancelled == [("FET", 7)] and out["results"][0]["action"] == "cancel_orphans"


# ── limit take-profits ───────────────────────────────────────────────────────

class _FakeEx:
    def __init__(self):
        self.calls = []

    def order(self, coin, is_buy, sz, px, order_type, **kw):
        self.calls.append((coin, is_buy, sz, px, order_type, kw.get("reduce_only")))
        return {"status": "ok"}


def _send(monkeypatch, **kw):
    pytest.importorskip("hyperliquid")
    ex = _FakeEx()
    monkeypatch.setattr(hx, "_exchange", lambda env=None: ex)
    hx.send_exit_orders("ETH", False, 0.008, 2870.0, 3100.0, tp_size=0.004,
                        tp2_px=3200.0, tp2_size=0.004, **kw)
    return ex.calls


def test_take_profits_are_resting_limits_and_the_stop_a_trigger(monkeypatch):
    calls = _send(monkeypatch)
    sl, tp1, tp2 = calls
    assert "trigger" in sl[4] and sl[4]["trigger"]["tpsl"] == "sl" and sl[2] == 0.008
    assert tp1[4] == {"limit": {"tif": "Gtc"}} and tp1[3] == 3100.0 and tp1[2] == 0.004
    assert tp2[4] == {"limit": {"tif": "Gtc"}} and tp2[3] == 3200.0
    assert all(c[5] is True for c in calls)                  # every exit reduce-only


def test_trigger_take_profits_remain_selectable(monkeypatch):
    monkeypatch.setenv("HL_TP_ORDER", "trigger")
    calls = _send(monkeypatch)
    assert all("trigger" in c[4] for c in calls)


def test_positions_table_shows_limit_take_profits():
    state = {"open_positions": [{"coin": "FET", "side": "long", "size": 40,
                                 "entry_px": 0.60}]}
    orders = [_stop(40, 0.55, oid=7), _ltp(20, 0.70, 2), _ltp(20, 0.80, 3)]
    [p] = ha.enrich_positions(state, orders, {"FET": "0.62"})
    assert p["sl_px"] == 0.55 and p["tp_px"] == 0.70 and p["tp_all_px"] == [0.70, 0.80]
    assert p["rr"] == 2.0


# ── leftovers cleared right before a new open ────────────────────────────────

def test_leftover_exits_only_without_a_position():
    orders = [_ltp(20, 0.72, oid=3), _stop(40, 0.55, oid=7),
              {**_ltp(1, 3100, oid=9, coin="ETH")}, {**_ltp(5, 0.7, oid=4), "reduceOnly": False}]
    assert ax.leftover_exits("FET", orders, {"open_positions": []}) == [3, 7]
    assert ax.leftover_exits("FET", orders, {"open_positions": [{"coin": "FET"}]}) == []


def test_execute_clears_leftovers_before_opening():
    calls = []
    orders = [_ltp(20, 0.72, oid=3), _stop(40, 0.55, oid=7)]

    def open_fn(sig, **kw):
        calls.append("open")
        return {"ok": False, "reason": "STALE_ENTRY"}

    out = ax.execute([{"symbol": "FET", "direction": "LONG"}],
                     account_state={"open_positions": []}, table=TABLE, cfg={},
                     open_fn=open_fn, exit_fn=lambda *a, **k: {}, open_orders=orders,
                     cancel_fn=lambda coin, oid, env: calls.append(("cancel", coin, oid)))
    assert calls == [("cancel", "FET", 3), ("cancel", "FET", 7), "open"]
    assert out["results"][0]["cleared_leftover_oids"] == [3, 7]


def test_execute_without_order_list_does_not_cancel():
    out = ax.execute([{"symbol": "FET", "direction": "LONG"}],
                     account_state={"open_positions": []}, table=TABLE, cfg={},
                     open_fn=lambda sig, **kw: {"ok": False, "reason": "X"},
                     exit_fn=lambda *a, **k: {},
                     cancel_fn=lambda *a: pytest.fail("no order list, no cancel"))
    assert "cleared_leftover_oids" not in out["results"][0]
