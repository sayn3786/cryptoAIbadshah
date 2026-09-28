"""
Hyperliquid AUTO-EXECUTE — Phase 4b. Feed the just-published signals into the
Phase-4a guarded open path, on the publish run, for the Confirmed tier only,
with a stop-loss and TP1 attached to every fill.

Nothing here fires unless BOTH:
  * the Phase-3 arm gate is on (hl_execution.is_armed(): live switch on, agent
    key present, kill switch off), AND
  * HL_AUTO_EXECUTE is explicitly on — a SEPARATE switch from the manual
    endpoint, defaulting OFF, so wiring auto-exec in is a deliberate second act.
MAINNET stays hard-blocked inside open_position unless HL_ALLOW_MAINNET is set,
so this is testnet-only by default even when armed and enabled.

Scope: only the Confirmed tier (confidence_score >= HL_AUTO_MIN_STRENGTH,
default the v53 Confirmed floor of 69) from the LATEST published slot — the tier
v53 recalibrated to be predictive. Everything else about safety is inherited
from open_position: the deterministic cloid + exact-once claim (a cron retry
no-ops), reconcile-before-act (never a second position on a coin we hold), the
per-run order cap and the total-exposure cap.

`execute` is a pure decision loop: it takes the signal list, the account
snapshot and the asset table, and calls injected `open_fn` / `exit_fn`
(the real SDK wrappers by default, mocked in tests), so the whole batch — caps,
tier filter, exit attachment — is tested without the SDK, the network or a DB.
"""
from __future__ import annotations

import math
import os
from typing import Any, Callable, Dict, List, Optional

import hl_exchange
import hl_execution
import hl_meta
import rec_policy

# The Confirmed-tier floor, shared with the strength calibration so the two can
# never drift. Overridable per-deployment for a tighter or looser auto scope.
DEFAULT_MIN_STRENGTH = rec_policy.CONFIRMED_TIER_FLOOR      # 69.0


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_auto_enabled() -> bool:
    """The dedicated auto-execute switch — OFF by default and independent of the
    arm gate, so auto-exec cannot ride in on the manual endpoint's switches."""
    return _flag("HL_AUTO_EXECUTE")


def auto_min_strength() -> float:
    return hl_execution._num_env("HL_AUTO_MIN_STRENGTH", DEFAULT_MIN_STRENGTH)


def gate_status(env: Optional[str] = None) -> Dict[str, Any]:
    """Why auto-exec would or would not run right now — without placing anything.
    `ready` is armed AND enabled; the arm reasons are surfaced for the operator."""
    arm = hl_execution.arm_status()
    enabled = is_auto_enabled()
    reasons = list(arm.get("reasons") or [])
    if not enabled:
        reasons.append("HL_AUTO_EXECUTE is off")
    return {
        "ready": bool(arm.get("armed") and enabled),
        "armed": bool(arm.get("armed")),
        "auto_enabled": enabled,
        "min_strength": auto_min_strength(),
        "reasons": reasons,
    }


# ── selecting what to trade ──────────────────────────────────────────────────

def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None                      # reject NaN
    except (TypeError, ValueError):
        return None


def _targets(row: Dict[str, Any]) -> List[float]:
    """Valid target prices from an attached ladder, in target_number order."""
    tgts = row.get("targets") or []
    if not isinstance(tgts, (list, tuple)) or not tgts:
        return []
    def _num(t):
        return t.get("target_number") if isinstance(t, dict) else None
    ordered = sorted((t for t in tgts if isinstance(t, dict)),
                     key=lambda t: (_num(t) is None, _num(t) or 0))
    return [px for px in (_f(t.get("target_price")) for t in ordered) if px and px > 0]


def _first_target(row: Dict[str, Any]) -> Optional[float]:
    """The TP1 price from an attached target ladder (target_number 1, else the
    lowest-numbered), or None."""
    tps = _targets(row)
    return tps[0] if tps else None


def _second_target(row: Dict[str, Any]) -> Optional[float]:
    tps = _targets(row)
    return tps[1] if len(tps) > 1 else None


DEFAULT_TP1_FRACTION = 0.5      # share of the position TP1 closes; TP2 takes the rest

# v54: the placed stop sits this many ATR(14, 2H) beyond the signal's stop.
# Backtests found the signal stop too close to routine pullbacks once the
# break-even move no longer rescues the trade. 0 = the published stop (v53).
DEFAULT_STOP_ATR_ADD = 1.0


def stop_atr_add() -> float:
    v = hl_execution._num_env("HL_STOP_ATR_ADD", DEFAULT_STOP_ATR_ADD)
    return v if 0 <= v <= 5 else DEFAULT_STOP_ATR_ADD


def atr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """ATR(period) over CLOSED candles (true range incl. gaps), the measure the
    signal engine sizes stops with. None when there are too few candles."""
    c = [x for x in candles or [] if isinstance(x, dict)]
    if len(c) < period + 1:
        return None
    try:
        trs = [max(float(b["high"]) - float(b["low"]),
                   abs(float(b["high"]) - float(a["close"])),
                   abs(float(b["low"]) - float(a["close"])))
               for a, b in zip(c[-period - 1:], c[-period:])]
    except (KeyError, TypeError, ValueError):
        return None
    v = sum(trs) / len(trs)
    return v if v > 0 else None


def placed_stop(sig: Dict[str, Any], *, add: Optional[float] = None) -> Optional[float]:
    """The stop to place: the signal's stop moved `add` x ATR further from entry
    (sig["atr"], ATR of the 2H chart). Falls back to the signal's stop when
    there is no ATR; never crosses zero."""
    sl = _f(sig.get("sl"))
    if not sl:
        return None
    add = stop_atr_add() if add is None else add
    a = _f(sig.get("atr"))
    if not add or not a:
        return sl
    if sig.get("direction") == "LONG":
        return max(sl - add * a, sl * 0.01)
    return sl + add * a


def tp1_fraction() -> float:
    f = hl_execution._num_env("HL_TP1_FRACTION", DEFAULT_TP1_FRACTION)
    return f if 0 < f < 1 else DEFAULT_TP1_FRACTION


def plan_tp_split(size: float, sz_decimals: int, tp1_px: Optional[float],
                  tp2_px: Optional[float], *, fraction: Optional[float] = None,
                  min_order_usd: float = hl_meta.HL_MIN_ORDER_USD):
    """(tp1_size, tp2_size) for a scale-out, or None to close all at TP1.

    None when there is no TP2, or when either part would be below Hyperliquid's
    minimum order value (a rejected TP would leave that part with no target).
    Sizes are rounded DOWN to the lot; TP2 takes the whole remainder so the two
    always sum to the position size."""
    if not size or not tp1_px or not tp2_px:
        return None
    f = tp1_fraction() if fraction is None else fraction
    first = hl_meta.round_size(size * f, sz_decimals)
    if not first:
        return None
    q = 10 ** max(0, int(sz_decimals))
    rest = round(size - first, max(0, int(sz_decimals)))
    rest = math.floor(rest * q + 1e-9) / q
    if rest <= 0:
        return None
    if first * tp1_px < min_order_usd or rest * tp2_px < min_order_usd:
        return None
    return first, rest


def _candle_ms(row: Dict[str, Any]) -> Any:
    """A stable candle identity for the exact-once claim. Prefers the epoch ms
    field, else the raw close-time value (hashed downstream, so any stable
    representation works)."""
    for k in ("candle_close_ms", "candle_close_time", "candle_close", "candle_ts"):
        if row.get(k) is not None:
            return row[k]
    return 0


def _as_utc(v: Any):
    """A candle_close_time (datetime or ISO string) as an aware UTC datetime."""
    from datetime import datetime, timezone
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def select_confirmed(rows: List[Dict[str, Any]], *,
                     min_strength: Optional[float] = None,
                     not_before: Any = None) -> List[Dict[str, Any]]:
    """The Confirmed-tier signals from the LATEST published slot.

    Filters to a real direction, a positive entry, and confidence_score >= the
    Confirmed floor; then keeps only the newest candle's cohort so a run acts on
    the freshly published set, not on older still-open signals from prior slots.
    Rows are expected newest-first (list_signals default), but the latest slot is
    picked by max candle_close_time regardless of order. With `not_before` (the
    current slot's start), rows built on an earlier candle are excluded, so a
    slot with no new Confirmed signal selects nothing.
    """
    thr = auto_min_strength() if min_strength is None else float(min_strength)
    usable = []
    for r in rows or []:
        cs = _f(r.get("confidence_score"))
        entry = _f(r.get("entry_price") if r.get("entry_price") is not None
                   else r.get("entry"))
        direction = str(r.get("direction") or "").upper()
        if cs is None or cs < thr:
            continue
        if direction not in ("LONG", "SHORT") or not entry or entry <= 0:
            continue
        usable.append(r)
    if not_before is not None:
        # Only the CURRENT slot's signals. Without this, a slot that published
        # nothing Confirmed made the previous slot's (still-open) signals "the
        # latest cohort", and auto-exec re-attempted trades hours old.
        floor = _as_utc(not_before)
        usable = [r for r in usable
                  if (t := _as_utc(r.get("candle_close_time"))) is not None and t >= floor]
    if not usable:
        return []
    latest = max((str(r.get("candle_close_time") or "") for r in usable), default="")
    if latest:
        usable = [r for r in usable if str(r.get("candle_close_time") or "") == latest]
    return usable


def to_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    """A store row → the signal shape open_position + the exits consume."""
    return {
        "symbol":    (row.get("symbol") or "").upper(),
        "entry":     _f(row.get("entry_price") if row.get("entry_price") is not None
                        else row.get("entry")),
        "direction": str(row.get("direction") or "").upper(),
        "id":        row.get("id") or row.get("signal_id"),
        "candle_ts": _candle_ms(row),
        "sl":        _f(row.get("stop_loss") if row.get("stop_loss") is not None
                        else row.get("sl")),
        "tp1":       _first_target(row),
        "tp2":       _second_target(row),
        "confidence_score": _f(row.get("confidence_score")),
    }


# ── the guarded batch ────────────────────────────────────────────────────────

def _reflect_open(account_state: Dict[str, Any], coin: str, notional: Any) -> None:
    """Record a just-opened position back into the snapshot so the exposure and
    position-exists guards see it on the NEXT signal in the same batch."""
    pos = account_state.setdefault("open_positions", [])
    pos.append({"coin": coin,
                "position_value_usd": _f(notional) or 0.0})


def _attach_exits(sig: Dict[str, Any], res: Dict[str, Any], *,
                  table: Dict[str, Any], exit_fn: Callable,
                  env: Optional[str]) -> None:
    """Place the reduce-only SL + TP1 for a filled open. Best-effort: a failure
    is recorded on the result and never raises — the position stays either way,
    and reduce-only exits can only ever shrink it."""
    coin = res.get("coin")
    size = res.get("size")
    if not coin or not size:
        res["exits_ok"] = False
        res["exits_error"] = "missing coin/size on fill"
        return
    sz_dec = int((table.get(coin) or {}).get("sz_decimals") or 0)
    stop = placed_stop(sig)
    sl_px = hl_meta.round_price(stop, sz_dec) if stop else None
    tp_px = hl_meta.round_price(sig.get("tp1"), sz_dec) if sig.get("tp1") else None
    tp2_px = hl_meta.round_price(sig.get("tp2"), sz_dec) if sig.get("tp2") else None
    # Scale out: TP1 closes part, TP2 the rest (the position manager then moves
    # the stop to entry). Falls back to all-at-TP1 when a part would be too small.
    split = plan_tp_split(float(size), sz_dec, tp_px, tp2_px)
    # Exit side CLOSES the position: a LONG exits by selling.
    is_buy_exit = sig.get("direction") != "LONG"
    sid, cts = sig.get("id"), sig.get("candle_ts")
    extra: Dict[str, Any] = {}
    if split:
        extra = {"tp_size": split[0], "tp2_px": tp2_px, "tp2_size": split[1],
                 "tp2_cloid": hl_execution.client_order_id(sid, "tp2", cts)}
    try:
        res["exits"] = exit_fn(
            coin, is_buy_exit, size, sl_px, tp_px, env=env,
            sl_cloid=hl_execution.client_order_id(sid, "sl", cts) if sl_px else None,
            tp_cloid=hl_execution.client_order_id(sid, "tp1", cts) if tp_px else None,
            **extra)
        res["exits_ok"] = True
        res["exit_prices"] = {"sl": sl_px, "tp": tp_px,
                              "tp2": tp2_px if split else None,
                              "signal_sl": sig.get("sl")}
        res["tp_split"] = list(split) if split else None
    except Exception as exc:                              # noqa: BLE001
        res["exits_ok"] = False
        res["exits_error"] = str(exc)


def leftover_exits(coin: str, open_orders: Optional[List[Dict[str, Any]]],
                   account_state: Dict[str, Any]) -> List[Any]:
    """Order ids of REDUCE-ONLY orders on `coin` while the account holds no
    position on it: exits left by a closed trade. Cancelled before a new open,
    so an old take-profit can't close part of the new position. Pure."""
    if not coin or any((p.get("coin") or "").upper() == coin.upper()
                       for p in (account_state or {}).get("open_positions") or []):
        return []
    return [o.get("oid") for o in open_orders or []
            if isinstance(o, dict) and o.get("reduceOnly")
            and str(o.get("coin") or "").upper() == coin.upper() and o.get("oid") is not None]


def execute(signals: List[Dict[str, Any]], *,
            account_state: Dict[str, Any],
            table: Optional[Dict[str, Any]] = None,
            cfg: Optional[Dict[str, Any]] = None,
            open_fn: Optional[Callable] = None,
            exit_fn: Optional[Callable] = None,
            open_orders: Optional[List[Dict[str, Any]]] = None,
            cancel_fn: Optional[Callable] = None,
            env: Optional[str] = None) -> Dict[str, Any]:
    """Open each signal through the Phase-4a guard, attaching a stop + TP1 to
    every fill. Returns {"attempted", "executed", "results"}.

    The per-run order cap and the exposure cap are enforced by open_position;
    this loop threads `run_order_count` and reflects each fill back into the
    account snapshot so those caps see the orders placed earlier in the SAME run.
    Never raises for a business reject — open_position returns a reason dict.
    """
    cfg = cfg or hl_exchange.caps()
    table = table if table is not None else hl_meta.asset_table(env=env)
    open_fn = open_fn or hl_exchange.open_position
    exit_fn = exit_fn or hl_exchange.send_exit_orders

    results: List[Dict[str, Any]] = []
    run_count = 0
    for sig in signals:
        cleared = []
        if open_orders is not None:
            coin = hl_meta.resolve_coin(sig.get("symbol"), table)
            cancel = cancel_fn or hl_exchange.cancel_order
            for oid in leftover_exits(coin, open_orders, account_state):
                try:
                    cancel(coin, oid, env)
                    cleared.append(oid)
                except Exception:                         # noqa: BLE001 — the manager retries
                    pass
            if cleared:
                open_orders = [o for o in open_orders if o.get("oid") not in cleared]
        res = open_fn(sig, account_state=account_state, table=table,
                      run_order_count=run_count, cfg=cfg, env=env)
        if cleared:
            res["cleared_leftover_oids"] = cleared
        if res.get("ok"):
            run_count += 1
            _reflect_open(account_state, res.get("coin"), res.get("notional_usd"))
            _attach_exits(sig, res, table=table, exit_fn=exit_fn, env=env)
        results.append(res)
    return {"attempted": len(signals), "executed": run_count, "results": results}
