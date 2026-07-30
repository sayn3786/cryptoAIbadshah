"""
The tracker view: what each published signal is doing, and what to do about it.

Stored rows answer "what happened". A trader looking at a list needs the next
question answered too — is this working, how far is it from its target, is the
stop still where it started, and is there anything to DO right now.

Everything here is pure: rows in, view models out. No database, no network, no
clock beyond what the caller passes. The remarks are advisory text about a
position that already exists; nothing here changes a signal, and nothing here
feeds back into scoring.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "CLOSED_WINDOW_DAYS", "TERMINAL_STATUSES", "build_row", "build_tracker",
    "summarise",
]

# Closed trades are shown for three days and then drop off the list. The point
# of the view is the decisions in front of you; older outcomes belong in the
# history endpoint, not in a working list that grows forever.
CLOSED_WINDOW_DAYS = 3

TERMINAL_STATUSES = ("TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED")

# Outcome classification. EXPIRED is deliberately NOT a loss: nothing was lost,
# the setup simply stopped being current. Folding it into losses would make the
# strategy look worse than it is, exactly as folding it into wins would flatter it.
_OUTCOME = {
    "TP_HIT":    "WIN",
    "SL_HIT":    "LOSS",
    "EXPIRED":   "EXPIRED",
    "CANCELLED": "CANCELLED",
}


def _dec(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d


def _f(value) -> Optional[float]:
    d = _dec(value)
    return float(d) if d is not None else None


def _utc(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _pct(frm: Optional[Decimal], to: Optional[Decimal]) -> Optional[float]:
    if frm is None or to is None or frm == 0:
        return None
    return round(float((to - frm) / frm * 100), 2)


def _signed(direction: str, frm: Optional[Decimal],
            to: Optional[Decimal]) -> Optional[float]:
    """Move from `frm` to `to` expressed in the trade's favour."""
    raw = _pct(frm, to)
    if raw is None:
        return None
    return raw if direction == "LONG" else round(-raw, 2)


def build_row(signal: Dict[str, Any],
              targets: Optional[Sequence[Dict[str, Any]]] = None,
              live_price=None,
              *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    One table row: the numbers, the ladder state, and the remark.

    ``live_price`` may be None — for a closed trade it is irrelevant, and for an
    open one the row still renders, just without live progress. Absence of a
    price is never reported as a move of zero.
    """
    now = now or datetime.now(timezone.utc)
    direction = (signal.get("direction") or "").upper()
    status = (signal.get("status") or "").upper()
    entry = _dec(signal.get("entry_price"))
    stop = _dec(signal.get("stop_loss"))
    live = _dec(live_price)
    close_price = _dec(signal.get("close_price"))
    is_terminal = status in TERMINAL_STATUSES

    rows = sorted(targets if targets is not None else (signal.get("targets") or []),
                  key=lambda t: int(t.get("target_number") or 0))
    ladder = []
    for t in rows:
        price = _dec(t.get("target_price"))
        hit_at = _utc(t.get("hit_at"))
        ladder.append({
            "number":   int(t.get("target_number") or 0),
            "price":    _f(price),
            "hit":      hit_at is not None,
            "hit_at":   hit_at.isoformat() if hit_at else None,
            "hit_price": _f(t.get("hit_price")),
            # How far price still has to travel to reach this rung, as a
            # percentage of the live price. Negative means already through it.
            "distance_pct": (_signed(direction, live, price)
                             if (live and price and not hit_at) else None),
        })

    hit_numbers = [t["number"] for t in ladder if t["hit"]]
    # A finished trade has no next target. Highlighting one on a stopped-out row
    # would read as "still to come" for a position that no longer exists.
    next_target = (None if is_terminal
                   else next((t for t in ladder if not t["hit"]), None))

    # Reference price: the close for a finished trade, the live tick otherwise.
    reference = close_price if (is_terminal and close_price) else live
    move_pct = _signed(direction, entry, reference)

    # Risk/reward realised so far, in R — the move divided by the original risk.
    r_multiple = None
    if entry is not None and stop is not None and reference is not None and entry != stop:
        risk = abs(entry - stop)
        gain = (reference - entry) if direction == "LONG" else (entry - reference)
        if risk > 0:
            r_multiple = round(float(gain / risk), 2)

    opened = _utc(signal.get("generated_at"))
    closed = _utc(signal.get("closed_at"))
    age_h = round(((closed or now) - opened).total_seconds() / 3600, 1) if opened else None

    outcome = _OUTCOME.get(status)
    if status == "CLOSED":
        # A manual close is judged on the number, not on the label.
        outcome = ("WIN" if (move_pct or 0) > 0 else
                   "LOSS" if (move_pct or 0) < 0 else "BREAKEVEN")

    row = {
        "signal_id":     signal.get("id"),
        "symbol":        signal.get("symbol"),
        "direction":     direction,
        "timeframe":     signal.get("timeframe"),
        "status":        status,
        "state":         "closed" if is_terminal else "live",
        "entry":         _f(entry),
        "stop_loss":     _f(stop),
        "live_price":    _f(live),
        "close_price":   _f(close_price),
        "targets":       ladder,
        "targets_hit":   hit_numbers,
        "next_target":   next_target["number"] if next_target else None,
        "move_pct":      move_pct,
        "r_multiple":    r_multiple,
        # Cushion, not distance: how far price sits ABOVE the stop for a LONG
        # (below it for a SHORT). Positive is safe, negative means price has
        # already traded through the stop and the record has not caught up.
        "stop_distance_pct": (_signed(direction, stop, live) if (live and stop) else None),
        "confidence":    _f(signal.get("confidence_score")),
        "opened_at":     opened.isoformat() if opened else None,
        "closed_at":     closed.isoformat() if closed else None,
        "age_hours":     age_h,
        "close_reason":  signal.get("close_reason"),
        "realized_return_pct": _f(signal.get("realized_return_pct")),
        "outcome":       outcome,
        "environment":   signal.get("environment"),
        "strategy_version": signal.get("strategy_version"),
    }
    row["remark"], row["action"] = _remark(row)
    return row


def _remark(row: Dict[str, Any]) -> tuple:
    """
    Plain-language state, and the next course of action.

    Returns (remark, action) where action is a short verb phrase for the column
    a trader scans: what, if anything, to do about this position now.
    """
    status, hits = row["status"], row["targets_hit"]
    move, r = row["move_pct"], row["r_multiple"]
    nxt = row["next_target"]
    stop_pct = row["stop_distance_pct"]

    def near(n):
        t = next((x for x in row["targets"] if x["number"] == n), None)
        return t["distance_pct"] if t else None

    # ── Finished ────────────────────────────────────────────────────────────
    if status == "TP_HIT":
        return (f"All targets hit ({_join(hits)}) — closed {_pm(move)}",
                "Done. Nothing to manage.")
    if status == "SL_HIT":
        partial = f" after {_join(hits)}" if hits else ""
        return (f"Stopped out{partial} — closed {_pm(move)}",
                "Done. Review whether the stop sat in a liquidity pool.")
    if status == "EXPIRED":
        return (f"Expired after {row['age_hours']}h without resolving"
                f"{f' — was {_pm(move)}' if move is not None else ''}",
                "Setup went stale. Close it if you are still in it.")
    if status == "CANCELLED":
        return ("Cancelled before it resolved", "No action.")
    if status == "CLOSED":
        return (f"Closed manually {_pm(move)}", "No action.")

    # ── Working ─────────────────────────────────────────────────────────────
    if status == "PARTIAL_TP":
        remark = f"{_join(hits)} hit"
        if move is not None:
            remark += f" — running {_pm(move)}"
        if stop_pct is not None and stop_pct <= 0:
            # Price is through the stop and the monitor has not caught up. The
            # stop is the only thing that matters now — advising a breakeven
            # move on a position that is already stopped is worse than useless.
            return (f"{remark}, now through the stop",
                    "Treat as stopped. The record updates on the next closed candle.")
        action = "Move stop to entry (breakeven) and let the rest run."
        if nxt and near(nxt) is not None:
            action = (f"Move stop to entry (breakeven); TP{nxt} is "
                      f"{abs(near(nxt)):.2f}% away."
                      if near(nxt) >= 0 else
                      f"Move stop to entry (breakeven); TP{nxt} already trading "
                      f"through — take it.")
        return remark, action

    # OPEN
    if stop_pct is not None and stop_pct <= 0:
        # Price is at or through the stop but no stop event is recorded, which
        # means the monitor has not seen a closed candle for it yet.
        return (f"At the stop ({_pm(move)}) — awaiting candle close",
                "Treat as stopped. The record updates on the next closed candle.")
    if move is not None and move < 0:
        detail = f", stop {abs(stop_pct):.2f}% away" if stop_pct is not None else ""
        return (f"Underwater {_pm(move)}{detail}",
                "Hold to the stop or cut early — do not widen it.")
    if nxt and near(nxt) is not None:
        gap = near(nxt)
        # Negative means price has already traded THROUGH the rung. Calling that
        # "approaching" reads as "not there yet" — the opposite of the truth, and
        # the difference between waiting and taking profit.
        if gap <= 0:
            return (f"Through TP{nxt} ({abs(gap):.2f}% past) — awaiting candle close",
                    f"TP{nxt} is available now. The record updates on the next "
                    f"closed candle.")
        if gap <= 0.5:
            return (f"Approaching TP{nxt} ({gap:.2f}% away)",
                    f"Prepare to take TP{nxt}.")
    if move is not None:
        r_txt = f" ({r:+.2f}R)" if r is not None else ""
        return (f"Working {_pm(move)}{r_txt}", "Hold. Stop unchanged.")
    return ("Working — no live price", "Hold.")


def _pm(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def _join(numbers: Sequence[int]) -> str:
    if not numbers:
        return "no targets"
    return ", ".join(f"TP{n}" for n in numbers)


def build_tracker(active: Sequence[Dict[str, Any]],
                  closed: Sequence[Dict[str, Any]],
                  prices: Optional[Dict[str, Any]] = None,
                  *, now: Optional[datetime] = None,
                  window_days: int = CLOSED_WINDOW_DAYS) -> Dict[str, Any]:
    """
    The whole view: live signals, then trades closed inside the window.

    ``prices`` maps SYMBOL -> live price. A symbol missing from it is not an
    error; that row simply renders without live progress.
    """
    now = now or datetime.now(timezone.utc)
    prices = prices or {}
    cutoff = now - timedelta(days=window_days)

    live_rows = [build_row(s, s.get("targets"), prices.get(s.get("symbol")), now=now)
                 for s in active or []]

    closed_rows = []
    for s in closed or []:
        at = _utc(s.get("closed_at")) or _utc(s.get("generated_at"))
        if at is None or at < cutoff:
            continue
        closed_rows.append(build_row(s, s.get("targets"),
                                     prices.get(s.get("symbol")), now=now))

    live_rows.sort(key=lambda r: (r["opened_at"] or ""), reverse=True)
    closed_rows.sort(key=lambda r: (r["closed_at"] or r["opened_at"] or ""), reverse=True)

    return {
        "live": live_rows,
        "closed": closed_rows,
        "window_days": window_days,
        "summary": summarise(closed_rows),
        "generated_at": now.isoformat(),
    }


def summarise(closed_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Scoreboard for the closed window.

    Win rate counts only DECIDED trades — wins and losses. Expired and cancelled
    signals are reported separately and excluded from the denominator, because a
    setup that never resolved is not evidence either way.
    """
    wins = [r for r in closed_rows if r["outcome"] == "WIN"]
    losses = [r for r in closed_rows if r["outcome"] == "LOSS"]
    decided = len(wins) + len(losses)
    moves = [r["move_pct"] for r in closed_rows if r["move_pct"] is not None]

    return {
        "closed": len(closed_rows),
        "wins": len(wins),
        "losses": len(losses),
        "expired": sum(1 for r in closed_rows if r["outcome"] == "EXPIRED"),
        "cancelled": sum(1 for r in closed_rows if r["outcome"] == "CANCELLED"),
        "breakeven": sum(1 for r in closed_rows if r["outcome"] == "BREAKEVEN"),
        "decided": decided,
        "win_rate_pct": round(len(wins) / decided * 100, 1) if decided else None,
        "avg_move_pct": round(sum(moves) / len(moves), 2) if moves else None,
        "best_pct": max(moves) if moves else None,
        "worst_pct": min(moves) if moves else None,
    }
