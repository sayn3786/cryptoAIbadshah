"""
The 1D picks on Hyperliquid TESTNET, on their OWN account (owner, Nov 25 2026).

The 15m scalp keeps HYPERLIQUID_ACCOUNT_ADDRESS. The daily close picks trade
a second testnet account, so each book has its own record:
  HYPERLIQUID_1D_ACCOUNT_ADDRESS   the account (0x...)
  HYPERLIQUID_1D_AGENT_KEY         its API (agent) wallet key - can trade,
                                   cannot withdraw
  HL_1D_EXECUTE=1                  the switch (default OFF)
  HL_1D_RISK_USD                   $ lost at the stop, full size (default 10)
  HL_1D_LEVERAGE                   leverage, capped by HL's max (default 5)
  HL_1D_MAX_EXPOSURE_USD           the book's total notional (default 5000)
Testnet only, always: the network is fixed here, whatever HYPERLIQUID_ENV
says. LIVE_TRADING_ENABLED and HL_KILL_SWITCH gate it like the rest.

Each 1D pick is traded as the tracker trades it:
  * a resting limit at the engine entry, with the stop and the LAST target
    attached (they activate on the fill, so a fill is never without a stop);
  * on the fill, TP1 and TP2 as reduce-only limits for a third each;
  * after TP1 the stop moves to entry (the position manager, hl_manage, run on
    this account);
  * the size cap (size_cap, tested on 1D): full size below 3 open the same
    way, then 3 / (open + 1) of the $ risk;
  * not filled in 24 hours -> cancelled; open 7 days -> closed at market;
  * the tracker closes it early (opposite signal) or ends it -> closed at
    market here too, so the HL book follows the published record.
One trade per coin. The picks come from the morning close set (_close_load).
`step` is pure: every read and write is injected, so it is tested without the
SDK, the network or a database.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

import size_cap

ENV = "testnet"
TAG = "1d"
HOUR_MS = 3_600_000
FILL_MS = 24 * HOUR_MS
HOLD_MS = 7 * 24 * HOUR_MS
DEFAULT_RISK_USD = 10.0
DEFAULT_LEVERAGE = 5
DEFAULT_MAX_EXPOSURE_USD = 5000.0
WORKING = ("PENDING", "OPEN", "PARTIAL_TP")
KEEP = 300


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def address() -> str:
    return os.getenv("HYPERLIQUID_1D_ACCOUNT_ADDRESS", "").strip()


def key() -> str:
    return os.getenv("HYPERLIQUID_1D_AGENT_KEY", "").strip()


def gate() -> Dict[str, Any]:
    """Whether the 1D book may act, and why not. No secret is revealed."""
    why = []
    if not _flag("HL_1D_EXECUTE"):
        why.append("HL_1D_EXECUTE is off")
    if not address():
        why.append("HYPERLIQUID_1D_ACCOUNT_ADDRESS is not set")
    if not key():
        why.append("HYPERLIQUID_1D_AGENT_KEY is not set")
    if _flag("HL_KILL_SWITCH"):
        why.append("HL_KILL_SWITCH is on")
    if not _flag("LIVE_TRADING_ENABLED"):
        why.append("LIVE_TRADING_ENABLED is off")
    a = address()
    if a and a.lower() == os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "").strip().lower():
        why.append("the 1D account must not be the scalp's account")
    return {"ok": not why, "reasons": why, "env": ENV}


def cfg() -> Dict[str, float]:
    return {"risk_usd": _num_env("HL_1D_RISK_USD", DEFAULT_RISK_USD),
            "leverage": max(1, int(_num_env("HL_1D_LEVERAGE", DEFAULT_LEVERAGE))),
            "max_exposure_usd": _num_env("HL_1D_MAX_EXPOSURE_USD", DEFAULT_MAX_EXPOSURE_USD)}


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def pick_levels(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{entry, sl, tps} of a close pick when they are coherent, else None."""
    e, sl = _f(p.get("entry")), _f(p.get("sl"))
    tps = [t for t in (_f(x) for x in p.get("tp_targets") or []) if t]
    d = str(p.get("direction") or "").upper()
    if not e or not sl or not tps or d not in ("LONG", "SHORT"):
        return None
    sgn = 1 if d == "LONG" else -1
    if sgn * (e - sl) <= 0 or any(sgn * (t - e) <= 0 for t in tps):
        return None
    return {"entry": e, "sl": sl, "tps": sorted(tps, key=lambda t: sgn * t), "direction": d}


def size_for(levels: Dict[str, Any], risk_usd: float, factor: float) -> Dict[str, float]:
    """Notional that loses risk_usd x factor at the stop."""
    stop_pct = abs(levels["entry"] - levels["sl"]) / levels["entry"]
    return {"notional_usd": round(risk_usd * factor / stop_pct, 2), "stop_pct": stop_pct}


def _active(rec: Dict[str, Any]) -> bool:
    return rec.get("state") in ("pending", "open")


def step(book: Dict[str, Dict[str, Any]], picks: List[Dict[str, Any]], *, io: Any,
         now_ms: int, conf: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """One pass. `book` {signal_id: record} is updated in place. `io` supplies:
      positions() -> [{coin, side, size, entry_px}]     table() -> asset table
      mid(coin) -> float|None                           status(signal_id) -> tracker status|None
      order(cloid) -> hl_account.order_status dict|None
      place(coin, is_buy, size, px, sl, tp, cloids, leverage) -> entry_status dict
      tp(coin, is_buy_exit, size, px, cloid) -> resp    cancel(coin, oid)
      close(coin, cloid) -> resp                        cancel_exits(coin)
      free_usd() -> float|None                          resolve(symbol, table) -> coin|None
      round_px(px, sz_dec), round_sz(sz, sz_dec), accepted(resp) -> (ok, detail)
      cloid(signal_id, intent) -> str
    Returns {"placed": [...], "filled": [...], "closed": [...], "skipped": {...}, "errors": [...]}."""
    conf = conf or cfg()
    out: Dict[str, Any] = {"placed": [], "filled": [], "closed": [], "skipped": {}, "errors": []}
    positions = {str(p.get("coin") or "").upper(): p for p in io.positions()}
    table = io.table()

    # 1) the trades already in the book
    for sid, r in book.items():
        if not _active(r):
            continue
        coin = r["coin"]
        st = io.status(sid)
        if r["state"] == "pending":
            o = io.order(r["cloid"])
            if o and o.get("status") == "filled" or coin in positions:
                r.update(state="open", filled_ms=now_ms)
                _add_tps(r, io, table, out)
                out["filled"].append(r)
            elif now_ms - r["placed_ms"] >= FILL_MS or (st and st not in WORKING):
                try:
                    if r.get("oid") is not None:
                        io.cancel(coin, r["oid"])
                    r.update(state="cancelled", closed_ms=now_ms,
                             reason="not filled in 24h" if now_ms - r["placed_ms"] >= FILL_MS
                             else f"tracker {st.lower()}")
                    out["closed"].append(r)
                except Exception as exc:                 # noqa: BLE001
                    out["errors"].append({"coin": coin, "step": "cancel", "error": type(exc).__name__})
            continue
        # open
        if coin not in positions:                        # the stop or the last target filled
            r.update(state="closed", closed_ms=now_ms, reason="stop or target on HL")
            io.cancel_exits(coin)
            out["closed"].append(r)
            continue
        why = None
        if now_ms - (r.get("filled_ms") or r["placed_ms"]) >= HOLD_MS:
            why = "7 days"
        elif st and st not in WORKING:
            why = f"tracker {st.lower()}"
        if why:
            try:
                ok, detail = io.accepted(io.close(coin, io.cloid(sid, "close")))
                if ok:
                    io.cancel_exits(coin)
                    r.update(state="closed", closed_ms=now_ms, reason=why)
                    out["closed"].append(r)
                else:
                    out["errors"].append({"coin": coin, "step": "close", "error": str(detail)[:120]})
            except Exception as exc:                     # noqa: BLE001
                out["errors"].append({"coin": coin, "step": "close", "error": type(exc).__name__})

    # 2) new picks, strongest first, sized by what is already open the same way
    busy = {r["coin"] for r in book.values() if _active(r)} | set(positions)
    exposure = sum(float(r.get("notional_usd") or 0) for r in book.values() if _active(r))
    for p in sorted(picks, key=lambda x: -float(x.get("strength") or 0)):
        sid = str(p.get("signal_id") or "")
        sym = str(p.get("symbol") or "").upper()
        if not sid or sid in book:
            continue
        lv = pick_levels(p)
        if not lv:
            out["skipped"][sym] = "BAD_LEVELS"
            continue
        st = io.status(sid)
        if st is not None and st not in ("PENDING", "OPEN"):
            out["skipped"][sym] = f"TRACKER_{st}"
            continue
        coin = io.resolve(sym, table)
        if coin is None:
            out["skipped"][sym] = "NOT_ON_HYPERLIQUID"
            continue
        if coin in busy:
            out["skipped"][sym] = "COIN_BUSY"
            continue
        mid = io.mid(coin)
        sgn = 1 if lv["direction"] == "LONG" else -1
        if not mid or sgn * (mid - lv["sl"]) <= 0 or sgn * (mid - lv["tps"][0]) >= 0:
            out["skipped"][sym] = "PRICE_MOVED_ON"            # already through the stop or TP1
            continue
        same = {r["coin"] for r in book.values() if _active(r) and r["direction"] == lv["direction"]}
        factor = size_cap.factor(len(same))
        sz = size_for(lv, conf["risk_usd"], factor)
        if exposure + sz["notional_usd"] > conf["max_exposure_usd"] + 1e-9:
            out["skipped"][sym] = "MAX_EXPOSURE"
            continue
        info = table.get(coin) or {}
        dec = int(info.get("sz_decimals") or 0)
        lev = max(1, min(int(conf["leverage"]), int(info.get("max_leverage") or conf["leverage"])))
        free = io.free_usd()
        if free is not None and sz["notional_usd"] / lev > free:
            out["skipped"][sym] = "INSUFFICIENT_MARGIN"
            continue
        size = io.round_sz(sz["notional_usd"] / lv["entry"], dec)
        if not size or size * lv["entry"] < 10:
            out["skipped"][sym] = "BELOW_MIN_NOTIONAL"
            continue
        px, sl, last = (io.round_px(v, dec) for v in (lv["entry"], lv["sl"], lv["tps"][-1]))
        cl = {k: io.cloid(sid, k) for k in ("open", "sl", "tp3")}
        try:
            res = io.place(coin, lv["direction"] == "LONG", size, px, sl, last, cl, lev)
        except Exception as exc:                         # noqa: BLE001
            out["errors"].append({"coin": coin, "step": "place", "error": type(exc).__name__})
            continue
        if res.get("state") == "error":
            out["errors"].append({"coin": coin, "step": "place", "error": str(res.get("error"))[:120]})
            continue
        r = {"signal_id": sid, "symbol": sym, "coin": coin, "direction": lv["direction"],
             "entry": px, "sl": sl, "tps": [io.round_px(t, dec) for t in lv["tps"]], "size": size,
             "sz_decimals": dec, "notional_usd": round(size * px, 2), "leverage": lev, "size_cap": factor,
             "risk_usd": round(conf["risk_usd"] * factor, 2), "cloid": cl["open"], "oid": res.get("oid"),
             "placed_ms": now_ms, "state": "pending", "strength": p.get("strength")}
        book[sid] = r
        busy.add(coin)
        exposure += r["notional_usd"]
        out["placed"].append(r)
        if res.get("state") == "filled":
            r.update(state="open", filled_ms=now_ms, fill_px=res.get("avg_px"))
            _add_tps(r, io, table, out)
            out["filled"].append(r)
    return out


def _add_tps(r: Dict[str, Any], io: Any, table: Dict[str, Any], out: Dict[str, Any]) -> None:
    """TP1 and TP2 as reduce-only limits, a third each (TP3, attached at the
    entry, closes what is left)."""
    tps = r.get("tps") or []
    if len(tps) < 2:
        return
    n = len(tps)
    third = io.round_sz(r["size"] / n, int(r.get("sz_decimals") or 0))
    if not third:
        return
    exit_buy = r["direction"] != "LONG"
    for i, px in enumerate(tps[:-1], start=1):
        try:
            ok, detail = io.accepted(io.tp(r["coin"], exit_buy, third, px, io.cloid(r["signal_id"], f"tp{i}")))
            if not ok:
                out["errors"].append({"coin": r["coin"], "step": f"tp{i}", "error": str(detail)[:120]})
        except Exception as exc:                         # noqa: BLE001
            out["errors"].append({"coin": r["coin"], "step": f"tp{i}", "error": type(exc).__name__})


def _px(v) -> str:
    return f"{v:,.6g}" if v else "-"


def note(kind: str, r: Dict[str, Any]) -> str:
    head = f"📘 1D on HL testnet · {r['symbol']} {r['direction']}"
    if kind == "placed":
        cap = f" · size {r['size_cap']:g}× (size cap)" if r.get("size_cap", 1) < 1 else ""
        return (f"{head}\nlimit {_px(r['entry'])} · stop {_px(r['sl'])} · targets "
                f"{' / '.join(_px(t) for t in r['tps'])}\n${r['notional_usd']:,.0f} at {r['leverage']}x = "
                f"${r['risk_usd']:g} at the stop{cap} · cancelled if not filled in 24h")
    if kind == "filled":
        return f"{head}: filled ~{_px(r.get('fill_px') or r['entry'])} · TP1/TP2 placed, a third each"
    return f"{head}: {r.get('state')} ({r.get('reason')})"


def summary(book: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(book.values())
    return {"pending": sum(r.get("state") == "pending" for r in rows),
            "open": sum(r.get("state") == "open" for r in rows),
            "closed": sum(r.get("state") == "closed" for r in rows),
            "cancelled": sum(r.get("state") == "cancelled" for r in rows),
            "exposure_usd": round(sum(float(r.get("notional_usd") or 0) for r in rows if _active(r)), 2)}


def trim(book: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Keep every active record and the latest finished ones."""
    act = {k: v for k, v in book.items() if _active(v)}
    done = sorted(((k, v) for k, v in book.items() if not _active(v)),
                  key=lambda kv: kv[1].get("closed_ms") or kv[1].get("placed_ms") or 0)[-KEEP:]
    return {**dict(done), **act}


def real_io(*, status_fn: Callable[[str], Optional[str]]):
    """The SDK / info-API wiring for `step`. Must run inside
    hl_account.using(address(), key(), TAG)."""
    import hl_account as ha
    import hl_exchange as he
    import hl_execution as hx
    import hl_meta as hm

    class IO:
        def __init__(self):
            self._state = ha.account_state(env=ENV)
            self._orders = None
            self._mids = None

        def positions(self):
            return self._state.get("open_positions") or []

        def table(self):
            return hm.asset_table(env=ENV)

        def free_usd(self):
            a = self._state.get("account_value_usd")
            try:
                s = ha.spot_usdc(env=ENV)
            except Exception:                            # noqa: BLE001
                s = None
            return None if (a is None and s is None) else max(a or 0.0, s or 0.0)

        def mid(self, coin):
            if self._mids is None:
                self._mids = ha.all_mids(env=ENV) or {}
            return _f(self._mids.get(coin))

        def status(self, sid):
            return status_fn(sid)

        def order(self, cloid):
            return ha.order_status(cloid, env=ENV)

        def place(self, coin, is_buy, size, px, sl, tp, cl, lev):
            return he.entry_status(he.send_limit_open_with_tpsl(
                coin, is_buy, size, px, sl, tp, cloid=cl["open"], sl_cloid=cl["sl"], tp_cloid=cl["tp3"],
                env=ENV, leverage=lev))

        def tp(self, coin, exit_buy, size, px, cloid):
            return he.send_take_profit(coin, exit_buy, size, px, cloid=cloid, env=ENV)

        def cancel(self, coin, oid):
            return he.cancel_order(coin, oid, ENV)

        def close(self, coin, cloid):
            return he.send_market_close(coin, cloid, ENV)

        def cancel_exits(self, coin):
            if self._orders is None:
                self._orders = ha._post_info({"type": "frontendOpenOrders", "user": ha.account_address()},
                                             env=ENV) or []
            for o in self._orders:
                if str(o.get("coin") or "").upper() == coin and o.get("reduceOnly"):
                    try:
                        he.cancel_order(coin, o.get("oid"), ENV)
                    except Exception:                    # noqa: BLE001
                        pass

        resolve = staticmethod(hm.resolve_coin)
        round_px = staticmethod(hm.round_price)
        round_sz = staticmethod(hm.round_size)
        accepted = staticmethod(he.order_accepted)

        @staticmethod
        def cloid(sid, intent):
            return hx.client_order_id(f"1d:{sid}", intent, "")
    return IO()
