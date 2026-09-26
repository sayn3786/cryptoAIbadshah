"""
Hyperliquid POSITION MANAGER — move the stop to entry once TP1 has filled.

Auto-exec opens each position with a stop for the FULL size and a split
take-profit (TP1 closes part, TP2 the rest). Nothing on the exchange can move a
stop when another order fills, so this runs on a schedule and looks at what is
actually on the exchange:

  * TP1 has filled  ⇔  part of the position has been closed while the rest is
    still open. Two independent signals, either is enough: the stop (placed for
    the FULL size) is now bigger than the position, or the account's fill
    history shows a closing fill on the coin after its latest opening fill.
    Stateless: no database, no ledger — the exchange is the source of truth,
    and a re-run is a no-op.
  * Then the remainder's stop moves to ENTRY (the position's average entry
    price): the new stop is placed FIRST and the old one cancelled after, so the
    position is never without a stop, even if a cancel fails.
  * If price has already come back through entry before the manager ran, a stop
    at entry would sit on the wrong side of the market — the remainder is closed
    at market instead, which is what the stop at entry would have done.

Only protective actions (a tighter stop, a reduce-only close). Gated like order
placement: the arm switch must be on, and MAINNET is blocked unless
HL_ALLOW_MAINNET is set. `plan` is pure and unit-tested; `run` does the I/O.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import hl_account
import hl_execution
import hl_meta


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _same_px(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) <= abs(b) * 1e-6


def partially_closed(coin: str, fills: Optional[List[Dict[str, Any]]]) -> bool:
    """A closing fill on `coin` after its most recent opening fill."""
    opens, closes = [], []
    for f in fills or []:
        if not isinstance(f, dict) or str(f.get("coin")) != str(coin):
            continue
        d, t = str(f.get("dir") or ""), _num(f.get("time"))
        if t is None:
            continue
        if d.startswith("Open"):
            opens.append(t)
        elif d.startswith("Close"):
            closes.append(t)
    return bool(opens and closes and max(closes) > max(opens))


def plan(positions: List[Dict[str, Any]], orders: List[Dict[str, Any]],
         mids: Dict[str, Any], table: Dict[str, Any],
         fills: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """What to do for each open position. Pure.

    `positions` are hl_account.parse_state rows; `orders` Hyperliquid's
    frontendOpenOrders; `mids` allMids; `table` the asset table (szDecimals);
    `fills` the account's recent userFills (optional second TP1 signal).
    Returns actions, each {"coin", "action", ...}:
      move_stop       place a stop at entry for the remainder, cancel `cancel_oids`
      cancel_stale    a stop at entry already exists — just cancel the old ones
      close_remainder price is already back through entry — close at market
    Positions that need nothing are omitted.
    """
    stops_by_coin: Dict[str, List[Dict[str, Any]]] = {}
    for o in orders or []:
        if not (isinstance(o, dict) and o.get("isTrigger") and o.get("reduceOnly")):
            continue
        stops_by_coin.setdefault(str(o.get("coin")), []).append(o)

    actions = []
    for p in positions or []:
        coin, side = p.get("coin"), p.get("side")
        size, entry = _num(p.get("size")), _num(p.get("entry_px"))
        if not coin or not size or not entry or side not in ("long", "short"):
            continue
        mark = _num((mids or {}).get(coin))
        stops = [o for o in stops_by_coin.get(str(coin), [])
                 if hl_account._classify_trigger(o, side, mark) == "sl"]
        if not stops:
            continue                                   # nothing to move (UI flags it)
        sz_dec = int((table.get(coin) or {}).get("sz_decimals") or 0)
        be_px = hl_meta.round_price(entry, sz_dec)
        at_entry = [o for o in stops if _same_px(_num(o.get("triggerPx")), be_px)]
        others = [o for o in stops if o not in at_entry]
        biggest = max((_num(o.get("sz")) or 0) for o in stops)
        step = 10 ** -max(0, sz_dec)
        partial = (biggest > size + step / 2           # position shrank below its stop
                   or partially_closed(coin, fills))

        if at_entry:
            if others:
                actions.append({"coin": coin, "action": "cancel_stale",
                                "cancel_oids": [o.get("oid") for o in others]})
            continue
        if not partial:
            continue                                   # TP1 not filled yet
        # Would a stop at entry already be through the market?
        crossed = mark is not None and (mark <= be_px if side == "long" else mark >= be_px)
        if crossed:
            actions.append({"coin": coin, "action": "close_remainder",
                            "side": side, "size": size, "entry_px": be_px,
                            "mark_px": mark})
            continue
        actions.append({"coin": coin, "action": "move_stop", "side": side,
                        "size": size, "stop_px": be_px, "entry_px": entry,
                        "cancel_oids": [o.get("oid") for o in others]})
    return actions


def _flag(name: str) -> bool:
    import os
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def run(*, env: Optional[str] = None,
        stop_fn: Optional[Callable] = None,
        cancel_fn: Optional[Callable] = None,
        close_fn: Optional[Callable] = None,
        read_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """One manager pass. Never raises for an exchange rejection — each action's
    outcome is reported. I/O is injectable for tests."""
    import hl_exchange
    st = hl_execution.arm_status()
    if not st["armed"]:
        return {"ok": True, "ran": False, "reason": "DISARMED", "detail": st["reasons"]}
    e = hl_account._env(env)
    if e == "mainnet" and not _flag("HL_ALLOW_MAINNET"):
        return {"ok": True, "ran": False, "reason": "MAINNET_NOT_ALLOWED"}

    stop_fn = stop_fn or hl_exchange.send_stop
    cancel_fn = cancel_fn or hl_exchange.cancel_order
    close_fn = close_fn or hl_exchange.send_market_close
    if read_fn is None:
        def read_fn():
            addr = hl_account.account_address()
            state = hl_account.account_state(env=env)
            orders = hl_account._post_info({"type": "frontendOpenOrders", "user": addr}, env=env) or []
            has_pos = bool(state.get("open_positions"))
            mids = hl_account.all_mids(env=env) if has_pos else {}
            fills = []
            if has_pos:
                try:
                    fills = hl_account._post_info({"type": "userFills", "user": addr}, env=env) or []
                except Exception:                       # noqa: BLE001 — stop-size signal still works
                    fills = []
            return (state.get("open_positions") or [], orders, mids,
                    hl_meta.asset_table(env=env), fills)
    positions, orders, mids, table, fills = read_fn()
    actions = plan(positions, orders, mids, table, fills)

    results = []
    for a in actions:
        coin = a["coin"]
        res = dict(a)
        try:
            if a["action"] == "move_stop":
                # Deterministic id: a re-run after a lost response is rejected as
                # a duplicate by the exchange instead of stacking a second stop.
                cloid = hl_execution.client_order_id(coin, "breakeven", a["stop_px"])
                is_buy_exit = a["side"] != "long"
                resp = stop_fn(coin, is_buy_exit, a["size"], a["stop_px"], cloid=cloid, env=env)
                ok, detail = hl_exchange.order_accepted(resp)
                res["placed"] = ok
                if not ok:
                    res["error"] = detail
                    results.append(res)                # keep the old stop; retry next run
                    continue
                res["cancelled"] = _cancel_all(cancel_fn, coin, a["cancel_oids"], env)
            elif a["action"] == "cancel_stale":
                res["cancelled"] = _cancel_all(cancel_fn, coin, a["cancel_oids"], env)
            elif a["action"] == "close_remainder":
                cloid = hl_execution.client_order_id(coin, "breakeven_close", a["entry_px"])
                resp = close_fn(coin, cloid, env)
                ok, detail = hl_exchange.order_accepted(resp)
                res["closed"] = ok
                if not ok:
                    res["error"] = detail
        except Exception as exc:                        # noqa: BLE001
            res["error"] = type(exc).__name__
        results.append(res)
    return {"ok": True, "ran": True, "positions": len(positions),
            "actions": len(actions), "results": results}


def _cancel_all(cancel_fn: Callable, coin: str, oids: List[Any],
                env: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for oid in oids or []:
        try:
            cancel_fn(coin, oid, env)
            out.append({"oid": oid, "ok": True})
        except Exception as exc:                        # noqa: BLE001
            out.append({"oid": oid, "ok": False, "error": type(exc).__name__})
    return out
