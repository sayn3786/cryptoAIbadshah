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

import time
from concurrent.futures import (ThreadPoolExecutor, as_completed,
                                TimeoutError as FuturesTimeout)
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "evaluate", "Action", "DEFAULT_MAX_AGE_HOURS", "DEFAULT_FILL_WINDOW_HOURS",
    "DEFAULT_BUDGET_SECONDS", "run_monitor", "excursion",
]

# A signal that has neither hit a target nor been stopped after this long is
# expired rather than left open indefinitely: the setup it was based on is no
# longer the market it was published into. Terminal, but distinct from a loss —
# EXPIRED and SL_HIT must never be conflated in the outcome history.
DEFAULT_MAX_AGE_HOURS = 72

# How long a working order stays live before it is withdrawn unfilled. A limit
# entry that price never came back to is NOT a trade: it is cancelled, and it
# never enters the win rate, the averages or the P/L. Counting unfilled orders
# was the single largest distortion in the outcome history — every statistic was
# measuring a population that included trades nobody could have taken.
DEFAULT_FILL_WINDOW_HOURS = 24

# After a non-final target is banked, the stop moves to the entry. The tracker
# has always ADVISED this ("move stop to entry (breakeven)") — recording the
# trade as though the stop never moved contradicted our own instruction and
# booked a full loss on a position that had already been made risk-free.
BREAKEVEN_AFTER_PARTIAL = True

# Wall-clock budget for one run, comfortably inside the serverless function's
# 60-second ceiling. The first real run timed out at exactly 60s
# (FUNCTION_INVOCATION_TIMEOUT) with two dozen signals: market data was fetched
# one symbol at a time, and the whole run died with NOTHING recorded.
#
# Stopping cleanly beats being killed. Every decision is keyed on the candle
# that caused it, so a partial run commits what it got and the next hourly tick
# resumes from there — a monitor that times out records nothing, forever.
DEFAULT_BUDGET_SECONDS = 45.0

# How many symbols to fetch market data for at once. The fetches are IO-bound
# and independent; serially, wall-clock was the sum of every symbol.
FETCH_WORKERS = 8

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


def _touched(level: Decimal, high: Decimal, low: Decimal) -> bool:
    """Did this candle trade AT the level, from either side?"""
    return low <= level <= high


def excursion(direction: str, entry: Decimal,
              candles: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """
    How far the trade ran in favour (MFE) and against (MAE), in percent of entry.

    Signed in the TRADE's favour: mfe is positive, mae negative. For a LONG the
    best is the highest high and the worst the lowest low; a SHORT mirrors it.

    This is the standard diagnostic for stop and target placement — a loss that
    first ran +1.8R is a stop problem, not a signal problem — and it costs
    nothing, because the caller has already walked these candles.
    """
    highs = [_dec(c.get("high")) for c in candles or []]
    lows = [_dec(c.get("low")) for c in candles or []]
    highs = [h for h in highs if h is not None]
    lows = [l for l in lows if l is not None]
    if not highs or not lows or entry is None or entry <= 0:
        return {"mfe_pct": None, "mae_pct": None}

    if direction == "LONG":
        best, worst = max(highs), min(lows)
        mfe = (best - entry) / entry * 100
        mae = (worst - entry) / entry * 100
    else:
        best, worst = min(lows), max(highs)
        mfe = (entry - best) / entry * 100
        mae = (entry - worst) / entry * 100
    # An excursion never crosses zero the wrong way: a trade that only ever went
    # against you has an MFE of 0, not a negative "best".
    return {"mfe_pct": round(float(max(mfe, Decimal(0))), 8),
            "mae_pct": round(float(min(mae, Decimal(0))), 8)}


def evaluate(signal: Dict[str, Any],
             targets: Sequence[Dict[str, Any]],
             candles: Sequence[Dict[str, Any]],
             *,
             now: Optional[datetime] = None,
             max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
             fill_window_hours: int = DEFAULT_FILL_WINDOW_HOURS,
             breakeven_after_partial: bool = BREAKEVEN_AFTER_PARTIAL
             ) -> List[Action]:
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
    if status not in ("PENDING", "OPEN", "PARTIAL_TP"):
        return []                        # already terminal: nothing to decide

    direction = (signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return []

    # The EFFECTIVE stop: where it actually sits now, which is the original
    # until something moved it. stop_loss itself is never rewritten — it is the
    # record of the risk originally taken.
    stop = _dec(signal.get("current_stop_loss")) or _dec(signal.get("stop_loss"))
    if stop is None or stop <= 0:
        return []

    entry = _dec(signal.get("entry_price"))
    # PENDING means the order has not filled yet. Until price TOUCHES the entry
    # there is no position, so no target and no stop can be hit — the monitor
    # used to skip this entirely and book outcomes on trades nobody was in.
    pending_fill = status == "PENDING"
    if pending_fill and (entry is None or entry <= 0):
        return []                        # cannot decide a fill without an entry

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
    last_close: Optional[Decimal] = None

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
        last_close = _dec(candle.get("close")) or last_close

        if pending_fill:
            if not _touched(entry, high, low):
                continue                 # order still working — nothing to judge
            actions.append({"kind": "ENTRY_FILLED", "price": entry,
                            "at": at, "source_ts": at})
            pending_fill = False
            # The filling candle can also resolve the trade — a bar that reaches
            # the entry and then runs to the stop is a real same-bar stop-out —
            # so fall through rather than skipping to the next candle.
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

        # Part of the position is banked and the rest is still running, so the
        # stop goes to breakeven — the same move the tracker tells you to make.
        # From here the trade cannot lose, which is exactly what the record
        # should say if it happens.
        if (hit_now and breakeven_after_partial and entry is not None
                and entry > 0 and stop != entry):
            actions.append({"kind": "STOP_MOVED", "price": entry, "at": at,
                            "source_ts": src, "reason": "BREAKEVEN_AFTER_TP"})
            stop = entry

    # A working order that price never came back to is CANCELLED, not expired
    # and certainly not a loss. It never became a position, so it must not reach
    # the win rate, the averages or the P/L in any form.
    if pending_fill and started is not None and fill_window_hours:
        reference = now or datetime.now(timezone.utc)
        if reference - started >= timedelta(hours=fill_window_hours):
            at = last_seen or (started + timedelta(hours=fill_window_hours))
            return [{"kind": "CANCELLED", "at": at, "source_ts": at,
                     "reason": "NEVER_FILLED",
                     "age_hours": round(
                         (reference - started).total_seconds() / 3600, 1)}]
    if pending_fill:
        return actions               # still working — nothing more to decide

    # Nothing terminal. Has it simply gone stale?
    if started is not None and max_age_hours:
        reference = now or datetime.now(timezone.utc)
        if reference - started >= timedelta(hours=max_age_hours):
            # Derived, never wall-clock: the last candle we saw, or failing that
            # the moment the signal became too old. Two runs an hour apart must
            # produce the same key, or the second would look like a new event.
            at = last_seen or (started + timedelta(hours=max_age_hours))
            # Expiry CLOSES the trade at the last price we saw, so the record
            # says where it was abandoned and what that cost. Without a price
            # the row lands with a NULL close and NULL return — "expired" with
            # no number, which is not an outcome anyone can learn from. A
            # sideways trade is still a result.
            actions.append({"kind": "EXPIRED", "at": at, "source_ts": at,
                            "price": last_close,
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
            if kind == "ENTRY_FILLED":
                res = store.record_entry_fill(
                    signal_id, action["price"], action["at"],
                    source_ts=action["source_ts"])
            elif kind == "STOP_MOVED":
                res = store.record_stop_move(
                    signal_id, action["price"], action["at"],
                    reason=action.get("reason", "BREAKEVEN"),
                    source_ts=action["source_ts"])
            elif kind == "CANCELLED":
                res = store.cancel_signal(
                    signal_id, action["at"], reason=action.get("reason", "CANCELLED"),
                    source_ts=action["source_ts"])
            elif kind == "TARGET_HIT":
                res = store.record_target_hit(
                    signal_id, action["target_number"], action["price"],
                    action["at"], source_ts=action["source_ts"])
            elif kind == "STOP_LOSS_HIT":
                res = store.record_stop_loss_hit(
                    signal_id, action["price"], action["at"],
                    source_ts=action["source_ts"])
            elif kind == "EXPIRED":
                # price closes the trade on the record and lets the store
                # compute realized_return_pct; None leaves both NULL.
                res = store.expire_signal(
                    signal_id, action["at"], price=action.get("price"),
                    source_ts=action["source_ts"])
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
        except Exception as exc:
            # Anything else — a store that predates this action, a column that
            # migration 004 has not added yet — is reported HERE rather than
            # thrown, so the actions already applied are not lost from the
            # summary. They committed; the caller must be told about them.
            applied.append({"kind": kind, "applied": False,
                            "error": "FAILED", "detail": str(exc)[:200]})
            break
        applied.append({"kind": kind, "applied": bool(res.get("applied")),
                        "duplicate": bool(res.get("duplicate")),
                        "status": (res.get("signal") or {}).get("status")})
    return applied


def _record_excursion(store, signal: Dict[str, Any],
                      candles: Sequence[Dict[str, Any]],
                      actions: Sequence[Action]) -> bool:
    """
    Measure MFE/MAE from the fill onwards and store it. True when recorded.

    The fill time comes from the row, or from an ENTRY_FILLED decided in THIS
    run — otherwise a trade that filled and moved in the same pass would not be
    measured until the next one.
    """
    filled_at = _utc(signal.get("entry_filled_at"))
    if filled_at is None:
        filled_at = next((a["at"] for a in actions if a["kind"] == "ENTRY_FILLED"),
                         None)
    if filled_at is None:
        # Never filled — or a pre-003 row that was never marked. For the latter
        # the signal candle is the honest starting point.
        if (signal.get("status") or "").upper() == "PENDING":
            return False
        filled_at = _utc(signal.get("candle_close_time"))
    if filled_at is None:
        return False

    since = [c for c in candles or []
             if (_utc(c.get("timestamp")) or filled_at) >= filled_at]
    if not since:
        return False

    entry = _dec(signal.get("entry_fill_price")) or _dec(signal.get("entry_price"))
    ex = excursion((signal.get("direction") or "").upper(), entry, since)
    if ex["mfe_pct"] is None and ex["mae_pct"] is None:
        return False
    try:
        store.record_excursion(signal["id"], mfe_pct=ex["mfe_pct"],
                               mae_pct=ex["mae_pct"])
    except Exception:
        return False                     # a measurement must never break a run
    return True


def run_monitor(store, fetch_candles: Callable[[str, str], Sequence[Dict[str, Any]]],
                *, now: Optional[datetime] = None,
                max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
                fill_window_hours: int = DEFAULT_FILL_WINDOW_HOURS,
                breakeven_after_partial: bool = BREAKEVEN_AFTER_PARTIAL,
                budget_seconds: float = DEFAULT_BUDGET_SECONDS,
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
    started = time.monotonic()
    summary = {"checked": 0, "filled": 0, "targets_hit": 0, "stops_moved": 0,
               "stopped": 0, "expired": 0, "cancelled": 0, "measured": 0,
               "unchanged": 0, "skipped": 0, "truncated": False,
               "elapsed_s": 0.0, "errors": [], "results": []}

    try:
        active = store.list_active_signals(limit=limit)
    except Exception as exc:
        summary["errors"].append({"symbol": None, "error": str(exc)[:200]})
        return summary

    # Fetch every symbol's candles ONCE, in parallel, before touching anything.
    # Serially this was the sum of every symbol's round trip, which is what blew
    # the 60-second function ceiling with two dozen signals.
    pairs = {(r.get("symbol"), r.get("timeframe")) for r in active
             if r.get("symbol")}
    candle_cache: Dict[tuple, Sequence[Dict[str, Any]]] = {}
    if pairs:
        ex = ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(pairs)))
        try:
            futures = {ex.submit(fetch_candles, sym, tf or "2H"): (sym, tf)
                       for sym, tf in pairs}
            remaining = max(budget_seconds - (time.monotonic() - started), 1.0)
            try:
                for fut in as_completed(futures, timeout=remaining):
                    key = futures[fut]
                    try:
                        candle_cache[key] = fut.result() or []
                    except Exception as exc:
                        summary["errors"].append({"symbol": key[0],
                                                  "error": str(exc)[:200]})
            except (FuturesTimeout, TimeoutError):
                # Aliases on 3.11+, distinct classes before it.
                # Whatever arrived is usable. The symbols that did not are
                # simply not evaluated this run.
                summary["truncated"] = True
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    for row in active:
        sid, symbol = row.get("id"), row.get("symbol")
        timeframe = row.get("timeframe")
        if time.monotonic() - started > budget_seconds:
            # Stop cleanly rather than being killed mid-write. Everything
            # decided so far has already committed, and the next run resumes
            # from here because every decision is keyed on its candle.
            summary["truncated"] = True
            summary["skipped"] += 1
            continue
        summary["checked"] += 1
        try:
            key = (symbol, timeframe)
            if key not in candle_cache:
                summary["skipped"] += 1
                continue                 # no market data this run
            detail = store.get_signal(sid)
            if not detail:
                continue
            actions = evaluate(detail, detail.get("targets") or [],
                               candle_cache[key], now=now,
                               max_age_hours=max_age_hours,
                               fill_window_hours=fill_window_hours,
                               breakeven_after_partial=breakeven_after_partial)

            # MFE/MAE is a measurement of a position, not a change to it, so it
            # is recorded outside the action list — and only from the fill
            # onwards, because an order that had not filled yet was not exposed
            # to anything. Runs even when nothing else happened: the high-water
            # marks move while the trade just sits there.
            if _record_excursion(store, detail, candle_cache[key], actions):
                summary["measured"] += 1

            if not actions:
                summary["unchanged"] += 1
                continue
            applied = apply_actions(store, sid, actions)
            for a in applied:
                if not a.get("applied"):
                    continue
                if a["kind"] == "ENTRY_FILLED":
                    summary["filled"] += 1
                elif a["kind"] == "TARGET_HIT":
                    summary["targets_hit"] += 1
                elif a["kind"] == "STOP_LOSS_HIT":
                    summary["stopped"] += 1
                elif a["kind"] == "EXPIRED":
                    summary["expired"] += 1
                elif a["kind"] == "CANCELLED":
                    summary["cancelled"] += 1
                elif a["kind"] == "STOP_MOVED":
                    summary["stops_moved"] += 1
            summary["results"].append({"signal_id": sid, "symbol": symbol,
                                       "applied": applied})
        except Exception as exc:
            summary["errors"].append({"symbol": symbol, "signal_id": sid,
                                      "error": str(exc)[:200]})
    summary["elapsed_s"] = round(time.monotonic() - started, 2)
    return summary
