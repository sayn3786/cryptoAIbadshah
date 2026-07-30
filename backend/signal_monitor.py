"""
What happened to the signals we published?

Signals were being recorded and then left at OPEN forever: the lifecycle
functions in signal_store existed and were tested, but nothing ever called
them. Entries with no exits are not a track record. This module is the driver.

The decision half — `evaluate` — is pure: candles in, actions out. No database,
no network, no clock. That matters because the rules it encodes are the ones
that decide whether a trade is recorded as a win or a loss, and they need to be
testable exactly.

Two rules are deliberately pessimistic, and both exist so the stored history
cannot flatter the strategy:

  * When one candle touches BOTH a target and the stop, the STOP is recorded.
    A candle says where price went, not in what order, so the pessimistic read
    is the honest one. backtest.py has always made the same assumption.
  * A gap straight past a level still counts as reached. Price traded through
    it; pretending otherwise would invent fills that never happened.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "evaluate", "Action", "DEFAULT_MAX_AGE_HOURS", "run_monitor",
]

# A signal that has neither hit a target nor been stopped after this long is
# expired rather than left open indefinitely: the setup it was based on is no
# longer the market it was published into. Terminal, but distinct from a loss —
# EXPIRED and SL_HIT must never be conflated in the outcome history.
DEFAULT_MAX_AGE_HOURS = 72

Action = Dict[str, Any]


def _dec(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _utc(value) -> Optional[datetime]:
    """Accept a datetime, an ISO string or epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    try:
        txt = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _reached(direction: str, level: Decimal, high: Decimal, low: Decimal) -> bool:
    """Did this candle trade at or through the level, in the trade's favour?"""
    return high >= level if direction == "LONG" else low <= level


def _stopped(direction: str, stop: Decimal, high: Decimal, low: Decimal) -> bool:
    return low <= stop if direction == "LONG" else high >= stop


def evaluate(signal: Dict[str, Any],
             targets: Sequence[Dict[str, Any]],
             candles: Sequence[Dict[str, Any]],
             *,
             now: Optional[datetime] = None,
             max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> List[Action]:
    """
    Decide what has happened to one signal, from the candles since it was made.

    ``signal`` is a stored row (direction, entry_price, stop_loss, status,
    candle_close_time, generated_at). ``targets`` are its stored targets, each
    with target_number, target_price and hit_at. ``candles`` are closed candles
    for the signal's symbol and timeframe, oldest first, each with timestamp
    (epoch ms), high and low.

    Returns actions in the order they must be applied::

        {"kind": "TARGET_HIT", "target_number": 1, "price": Decimal,
         "at": datetime, "source_ts": "..."}
        {"kind": "STOP_LOSS_HIT", "price": Decimal, "at": …, "source_ts": …}
        {"kind": "EXPIRED", "at": …, "source_ts": …}

    Empty when nothing has changed — the normal case, and why this can run on a
    schedule. Every action carries a source_ts derived from the CANDLE, never
    from wall-clock time, so re-running over the same candles is a no-op at the
    store's idempotency layer rather than a duplicate event.
    """
    status = (signal.get("status") or "").upper()
    if status not in ("OPEN", "PARTIAL_TP"):
        return []                        # already terminal: nothing to decide

    direction = (signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return []

    stop = _dec(signal.get("stop_loss"))
    if stop is None or stop <= 0:
        return []

    # Only candles AFTER the one the signal was made on. The signal candle
    # itself already happened when the decision was taken; counting it would
    # let a trade be stopped out by the very bar that triggered it.
    after = _utc(signal.get("candle_close_time"))
    started = _utc(signal.get("generated_at")) or after

    pending = []
    for t in sorted(targets or [], key=lambda x: int(x.get("target_number") or 0)):
        if t.get("hit_at"):
            continue                     # already recorded
        price = _dec(t.get("target_price"))
        if price is not None and price > 0:
            pending.append((int(t["target_number"]), price))

    actions: List[Action] = []
    last_seen: Optional[datetime] = None

    for candle in candles or []:
        at = _utc(candle.get("timestamp"))
        if at is None:
            continue
        if after is not None and at < after:
            continue
        high, low = _dec(candle.get("high")), _dec(candle.get("low"))
        if high is None or low is None:
            continue
        last_seen = at
        # The candle's own open time IS the source timestamp. It must be a real
        # datetime, not an epoch string: record_target_hit parses source_ts as a
        # timestamp (the other lifecycle calls hash it raw), so a bare "17724…"
        # would be rejected as "not an ISO timestamp" and no target hit could
        # ever be recorded.
        src = at

        hit_now = [(n, p) for n, p in pending
                   if _reached(direction, p, high, low)]
        stop_now = _stopped(direction, stop, high, low)

        if stop_now:
            # Pessimistic: one candle cannot tell us the order, so the stop
            # wins even when a target was touched in the same bar. Anything
            # else would let the record claim wins it cannot prove.
            actions.append({"kind": "STOP_LOSS_HIT", "price": stop,
                            "at": at, "source_ts": src,
                            "also_touched": [n for n, _ in hit_now]})
            return actions

        for n, p in hit_now:
            actions.append({"kind": "TARGET_HIT", "target_number": n,
                            "price": p, "at": at, "source_ts": src})
            pending = [(m, q) for m, q in pending if m != n]

        if not pending and actions:
            return actions               # final target reached: terminal

    # Nothing terminal. Has it simply gone stale?
    if started is not None and max_age_hours:
        reference = now or datetime.now(timezone.utc)
        if reference - started >= timedelta(hours=max_age_hours):
            # Derived, never wall-clock: the last candle we saw, or failing that
            # the moment the signal became too old. Two runs an hour apart must
            # produce the same key, or the second would look like a new event.
            at = last_seen or (started + timedelta(hours=max_age_hours))
            actions.append({"kind": "EXPIRED", "at": at, "source_ts": at,
                            "age_hours": round(
                                (reference - started).total_seconds() / 3600, 1)})
    return actions


# ── Applying the decision ────────────────────────────────────────────────────

def apply_actions(store, signal_id, actions: Sequence[Action]) -> List[Dict[str, Any]]:
    """
    Push decided actions through the store, in order, stopping at the first
    that does not apply.

    Every call is idempotent on its source timestamp, so a replay over the same
    candles changes nothing. A transition the state machine rejects (a signal
    someone closed by hand while this ran) is reported, not forced.
    """
    applied = []
    for action in actions:
        kind = action["kind"]
        try:
            if kind == "TARGET_HIT":
                res = store.record_target_hit(
                    signal_id, action["target_number"], action["price"],
                    action["at"], source_ts=action["source_ts"])
            elif kind == "STOP_LOSS_HIT":
                res = store.record_stop_loss_hit(
                    signal_id, action["price"], action["at"],
                    source_ts=action["source_ts"])
            elif kind == "EXPIRED":
                res = store.expire_signal(
                    signal_id, action["at"], source_ts=action["source_ts"])
            else:
                continue
        except store.InvalidTransition as exc:
            applied.append({"kind": kind, "applied": False,
                            "error": "INVALID_TRANSITION", "detail": str(exc)})
            break
        except store.SignalValidationError as exc:
            applied.append({"kind": kind, "applied": False,
                            "error": "INVALID", "detail": str(exc)})
            break
        applied.append({"kind": kind, "applied": bool(res.get("applied")),
                        "duplicate": bool(res.get("duplicate")),
                        "status": (res.get("signal") or {}).get("status")})
    return applied


def run_monitor(store, fetch_candles: Callable[[str, str], Sequence[Dict[str, Any]]],
                *, now: Optional[datetime] = None,
                max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
                limit: int = 100) -> Dict[str, Any]:
    """
    Evaluate every working signal and record what the market did to it.

    ``fetch_candles(symbol, timeframe)`` returns closed candles for that pair,
    oldest first. Candles are fetched ONCE per (symbol, timeframe) even when
    several signals share one — the same market cannot have moved differently
    for two of them.

    One signal's failure never stops the run: it is reported and the rest
    continue. A monitor that abandons the batch on the first bad row is worse
    than no monitor, because the remaining trades silently stay open.
    """
    summary = {"checked": 0, "targets_hit": 0, "stopped": 0, "expired": 0,
               "unchanged": 0, "errors": [], "results": []}

    try:
        active = store.list_active_signals(limit=limit)
    except Exception as exc:
        summary["errors"].append({"symbol": None, "error": str(exc)[:200]})
        return summary

    candle_cache: Dict[tuple, Sequence[Dict[str, Any]]] = {}

    for row in active:
        sid, symbol = row.get("id"), row.get("symbol")
        timeframe = row.get("timeframe")
        summary["checked"] += 1
        try:
            key = (symbol, timeframe)
            if key not in candle_cache:
                candle_cache[key] = fetch_candles(symbol, timeframe) or []
            detail = store.get_signal(sid)
            if not detail:
                continue
            actions = evaluate(detail, detail.get("targets") or [],
                               candle_cache[key], now=now,
                               max_age_hours=max_age_hours)
            if not actions:
                summary["unchanged"] += 1
                continue
            applied = apply_actions(store, sid, actions)
            for a in applied:
                if not a.get("applied"):
                    continue
                if a["kind"] == "TARGET_HIT":
                    summary["targets_hit"] += 1
                elif a["kind"] == "STOP_LOSS_HIT":
                    summary["stopped"] += 1
                elif a["kind"] == "EXPIRED":
                    summary["expired"] += 1
            summary["results"].append({"signal_id": sid, "symbol": symbol,
                                       "applied": applied})
        except Exception as exc:
            summary["errors"].append({"symbol": symbol, "signal_id": sid,
                                      "error": str(exc)[:200]})
    return summary
