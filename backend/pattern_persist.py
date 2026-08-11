"""
Keep a confirmed chart pattern on the card until it actually resolves.

The triangle/wedge detector is stateless — it re-fits from the newest swing
pivots on every load. So a confirmed breakout vanishes the moment a retest
prints a fresh pivot and the rails re-fit to something that no longer counts as
a wedge, even though price never hit the target and never failed. On a daily
chart that is a card gone overnight (seen on TAO 1D).

This module remembers a confirmed breakout by its FIXED lines and levels — the
rails, the break level, the target, the apex — and tracks it forward against
price until it genuinely hits target, fails, or expires at the apex. Then the
card can persist across the days the stateless detector would have dropped it.

DISPLAY ONLY. A re-surfaced pattern is tagged ``display_only`` and the scorer
skips it, so nothing here changes what trades. It does not need to change
scoring anyway: the scorer already ignores any breakout older than five candles
(`PATTERN_BREAK_FRESH_BARS`), so a pattern old enough to have been re-fit away
contributes nothing to the score whether or not its card is showing. That is
what keeps this out of the v45 strategy freeze.

The re-evaluation is PURE — a stored record and the candles in, a verdict out —
so it is tested exhaustively. The only impure part is the KV read/write, which
is thin, guarded, and degrades to doing nothing when KV is not configured (the
card then simply behaves as it does today).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import kv
from patterns import (BREAK_GIVEBACK_FRAC, FAILURE_SHOW_BARS, MIN_RETEST_BUFFER,
                      _retest_state, _same_structure)

__all__ = [
    "record_from_pattern", "reevaluate", "to_display_pattern", "reconcile",
    "PERSIST_STATUSES", "MAX_TRACKED",
]

# Only genuinely confirmed, directional breakouts are worth tracking forward. A
# forming or neutral structure has nothing fixed to track.
PERSIST_STATUSES = ("confirmed",)

# A symbol/timeframe rarely has more than a couple of live structures. Cap the
# stored list so a pathological run can never grow the KV value without bound.
MAX_TRACKED = 6


# ── Geometry off the stored rail endpoints ───────────────────────────────────
# A pattern's rails are stored as two (timestamp, price) endpoints each. Working
# in price-vs-time (not price-vs-index) makes every level index-independent, so a
# record stays valid as candles roll and the index of any bar changes.

def _interp(line: Sequence[Dict], ts: float) -> Optional[float]:
    """Price on a rail at time ``ts``, linearly inter/extrapolated. None if the
    endpoints cannot define a line (same timestamp, missing data)."""
    try:
        t0, p0 = float(line[0]["timestamp"]), float(line[0]["price"])
        t1, p1 = float(line[1]["timestamp"]), float(line[1]["price"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if t1 == t0:
        return None
    return p0 + (p1 - p0) * (ts - t0) / (t1 - t0)


def _apex_ts(upper_line: Sequence[Dict], lower_line: Sequence[Dict]) -> Optional[float]:
    """When the two rails meet — the deadline past which the structure expires."""
    try:
        t0 = float(upper_line[0]["timestamp"])
        t1 = float(upper_line[1]["timestamp"])
        pu0, pu1 = float(upper_line[0]["price"]), float(upper_line[1]["price"])
        pl0, pl1 = float(lower_line[0]["price"]), float(lower_line[1]["price"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    span = t1 - t0
    if span == 0:
        return None
    su = (pu1 - pu0) / span
    sl = (pl1 - pl0) / span
    if su == sl:
        return None                       # parallel rails: no apex
    return t0 + (pl0 - pu0) / (su - sl)


# ── Building a storable record from a freshly-detected pattern ───────────────

def record_from_pattern(p: Dict, symbol: str, tf: str, now_ts: int) -> Optional[Dict]:
    """
    A compact, forward-trackable record from a confirmed detected pattern, or
    None when it is not a confirmed directional breakout with drawable rails.

    Everything needed to track it forward is captured as absolute values — the
    break level, the flat failure level, the target and the apex time — so
    re-evaluation never has to re-derive it from pivots that may be gone.
    """
    if (p.get("status") not in PERSIST_STATUSES or not p.get("confirmed")
            or p.get("direction") not in ("bullish", "bearish")):
        return None
    upper_line, lower_line = p.get("upper_line"), p.get("lower_line")
    break_ts = p.get("break_ts")
    target = p.get("target")
    if not (upper_line and lower_line) or break_ts is None or target is None:
        return None

    bullish = p["direction"] == "bullish"
    # The broken rail (up-break clears the UPPER rail; down-break the LOWER), and
    # the opposite rail held FLAT at the breakout — the level a failure closes
    # back through.
    brk_lvl = _interp(upper_line if bullish else lower_line, break_ts)
    fail_lvl = _interp(lower_line if bullish else upper_line, break_ts)
    if brk_lvl is None or fail_lvl is None:
        return None

    # ── Textbook target date and the void deadline (two independent dates) ───
    # The rails meet at the APEX; a converging pattern that reaches its apex
    # unresolved is void (Edwards & Magee; Bulkowski) — so the apex is the hard
    # structural deadline. Separately, the measured move is expected to complete
    # within roughly the time the pattern took to FORM (breakout + formation
    # width), the classic rule-of-thumb "expected by" date. These are kept as
    # two independent projections — neither caps the other — and the pattern
    # expires at whichever comes first (see ``reevaluate``).
    apex = _apex_ts(upper_line, lower_line)
    try:
        start_ts = int(upper_line[0]["timestamp"])
    except (KeyError, IndexError, TypeError, ValueError):
        start_ts = None
    target_eta = None
    if start_ts is not None:
        target_eta = int(break_ts) + (int(break_ts) - start_ts)

    # Keyed on the STRUCTURE (type + direction), not the break bar. One confirmed
    # falling wedge is one story to track; a later re-fit with a different break
    # bar must not spawn a second, and — because a tracked key is never
    # overwritten — the first confirmation's rails and target are FROZEN until it
    # resolves. That is what stops the target drifting 287 → 220 day to day.
    return {
        "symbol": symbol, "timeframe": tf,
        "key": f"{p.get('type')}:{p['direction']}",
        "type": p.get("type"), "label": p.get("label"),
        "direction": p["direction"],
        "break_ts": int(break_ts),
        "break_level": round(brk_lvl, 8),
        "fail_level": round(fail_lvl, 8),
        "target": target,
        "apex_ts": apex,
        "target_eta_ts": target_eta,
        "pattern_start_ts": start_ts,
        "upper_line": upper_line, "lower_line": lower_line,
        "converge_pct": p.get("converge_pct"),
        "breakout_volume": p.get("breakout_volume"),
        "breakout_dir": p.get("breakout_dir"),
        "first_seen_ts": now_ts,
        "last_seen_ts": now_ts,
    }


# ── Re-evaluating a stored record against current candles ────────────────────

def reevaluate(record: Dict, candles: Sequence[Dict], current_price: float,
               now_ts: int) -> Dict:
    """
    What has become of a tracked breakout. Pure.

    Returns ``{"state": ...}`` where state is one of:

      'live'        still holding the breakout side, target not yet reached
      'target_hit'  price reached the measured-move target — resolved
      'failed'      closed back through the failure level, or gave the move back
      'expired'     ran out its deadline — the apex OR the measured-move-in-time
                    projection, whichever comes first — without resolving

    ``candles`` need only cover from the break to now (the structure window is
    plenty); ``current_price`` is the last closed price.
    """
    direction = record.get("direction")
    bullish = direction == "bullish"
    brk = _num(record.get("break_level"))
    fail = _num(record.get("fail_level"))
    target = _num(record.get("target"))
    break_ts = record.get("break_ts")
    apex_ts = record.get("apex_ts")
    target_eta_ts = record.get("target_eta_ts")
    if brk is None or fail is None or target is None or break_ts is None:
        return {"state": "failed", "reason": "malformed record"}

    # Reaching the target wins over any deadline — if price is AT the target we
    # call it a hit even on the very bar the deadline lapses.
    if current_price is not None:
        if bullish and current_price >= target:
            return {"state": "target_hit"}
        if not bullish and current_price <= target:
            return {"state": "target_hit"}

    # Otherwise the pattern expires at whichever of its two textbook dates comes
    # first: the apex void deadline, or the measured-move-in-time "expected by".
    deadlines = [d for d in (apex_ts, target_eta_ts) if d is not None]
    if deadlines and now_ts >= min(deadlines):
        return {"state": "expired"}

    since = [c for c in candles or [] if _num(c.get("timestamp")) is not None
             and int(c["timestamp"]) >= int(break_ts)]

    # A close back through the flat failure level ends it.
    fail_ts = None
    for c in since:
        close = _num(c.get("close"))
        if close is None:
            continue
        if (bullish and close < fail) or (not bullish and close > fail):
            fail_ts = int(c["timestamp"])
            break

    # Or a full give-back of the move from the break level to the extreme since.
    if fail_ts is None and since and current_price is not None:
        if bullish:
            peak = max((_num(c.get("high")) or brk) for c in since)
            floor_ = brk - max((peak - brk) * BREAK_GIVEBACK_FRAC, brk * MIN_RETEST_BUFFER)
            if current_price < floor_:
                fail_ts = now_ts
        else:
            trough = min((_num(c.get("low")) or brk) for c in since)
            ceil_ = brk + max((brk - trough) * BREAK_GIVEBACK_FRAC, brk * MIN_RETEST_BUFFER)
            if current_price > ceil_:
                fail_ts = now_ts

    if fail_ts is not None:
        # Show a fresh failure briefly (so the card can say it broke), then let
        # it drop. Freshness is judged in bars against the candles we have.
        fresh = _fresh_bars(candles, fail_ts) <= FAILURE_SHOW_BARS
        return {"state": "failed" if fresh else "expired", "failed_ts": fail_ts}

    return {"state": "live"}


def to_display_pattern(record: Dict, candles: Sequence[Dict],
                       current_price: float, *, failed_ts=None,
                       resolved: Optional[str] = None) -> Dict:
    """
    Shape a stored record back into the triangle_patterns dict the card renders.

    Tagged ``display_only`` so the scorer skips it, and ``tracked`` so the card
    can say it is a persisted breakout rather than a fresh detection. Rebuilds
    the retest note from the candles so it stays current.
    """
    bullish = record.get("direction") == "bullish"
    # upper_now/lower_now must be the rail value at the LAST PIVOT — the end of
    # the drawn line — because that is exactly what the fresh detector reports,
    # and _same_structure dedupes on these. Extrapolating them to the current
    # candle instead would make the tracked copy never match its own fresh
    # detection, so the card would show the wedge twice on the day it is both
    # detected and tracked.
    upper_line = record.get("upper_line") or []
    lower_line = record.get("lower_line") or []
    upper_now = _num(upper_line[-1].get("price")) if upper_line else None
    lower_now = _num(lower_line[-1].get("price")) if lower_line else None

    break_idx = _index_of_ts(candles, record.get("break_ts"))
    retest = (_retest_state(candles, break_idx, record.get("break_level"), bullish)
              if break_idx is not None else None)

    if resolved == "target":
        status = "target_hit"
    elif failed_ts:
        status = "failed"
    else:
        status = "confirmed"
    return {
        "type": record.get("type"), "label": record.get("label"),
        "direction": record.get("direction"),
        # Carry the timeframe through so the card's TF pill reads "1W"/"1D"
        # rather than "undefined" (the fresh detector sets this; a tracked
        # re-surface must too).
        "timeframe": record.get("timeframe"),
        "status": status, "confirmed": status == "confirmed",
        "display_only": True, "tracked": True,
        "target": record.get("target"),
        # Textbook target date and the void deadline, so the card can say WHEN
        # the move is expected and by when the pattern is void if it does not.
        "target_eta_ts": record.get("target_eta_ts"),
        "expiry_ts": record.get("apex_ts"),
        "confirmed_at": record.get("break_ts"),
        "upper_now": round(upper_now, 8) if upper_now is not None else None,
        "lower_now": round(lower_now, 8) if lower_now is not None else None,
        "converge_pct": record.get("converge_pct"),
        "breakout_dir": record.get("breakout_dir"),
        "break_ts": record.get("break_ts"),
        "breakout_volume": record.get("breakout_volume"),
        "failed_ts": failed_ts,
        "failure_reason": ("closed back through the level" if failed_ts else None),
        "target_reached": resolved == "target",
        "retest": retest,
        "upper_line": record.get("upper_line"),
        "lower_line": record.get("lower_line"),
        "sweep": None, "conflict": None,
    }


# ── Reconcile: fresh detections + what we were tracking ──────────────────────

def _structure_key(pattern: Dict) -> str:
    return f"{pattern.get('type')}:{pattern.get('direction')}"


def reconcile(fresh: Sequence[Dict], records: List[Dict], candles: Sequence[Dict],
              current_price: float, symbol: str, tf: str,
              now_ts: int) -> Tuple[List[Dict], List[Dict]]:
    """
    Hold a confirmed pattern, frozen, until it resolves. PURE — KV read/write
    happen in ``load``/``save`` around this.

    Returns ``(display, updated_records)``:
      * ``display`` — the full triangle/wedge list to show: every tracked
        confirmed structure (with its ORIGINAL rails and target), plus any fresh
        detection that is NOT a re-fit of one already tracked.
      * ``updated_records`` — the store to persist.

    The rules that give the user what they asked for — *a breakout stays until
    it invalidates or reaches target*:

      1. A confirmed structure is captured ONCE (keyed by type+direction) and
         never re-measured. Its rails, target and target date are frozen.
      2. It is held every load and re-evaluated only for OUTCOME — target hit,
         invalidated, or voided at the apex — not re-fitted.
      3. A fresh re-fit of a structure already tracked is suppressed, so the
         drifting new fit never replaces or duplicates the frozen one.
      4. It leaves the card only when it reaches target (shown once as reached),
         fully invalidates (shown briefly as failed), or reaches its apex void
         deadline.
    """
    by_key: Dict[str, Dict] = {r["key"]: dict(r) for r in records
                               if isinstance(r, dict) and r.get("key")}

    # Capture a NEW confirmation only when that structure is not already tracked.
    # An already-tracked key is left untouched — that is the freeze.
    for p in fresh or []:
        rec = record_from_pattern(p, symbol, tf, now_ts)
        if rec and rec["key"] not in by_key:
            by_key[rec["key"]] = rec

    display: List[Dict] = []
    survivors: Dict[str, Dict] = {}
    for key, rec in by_key.items():
        verdict = reevaluate(rec, candles, current_price, now_ts)
        state = verdict.get("state")
        if state == "target_hit":
            # Reached its measured move — show it as reached this once, then let
            # it fall off (not carried into survivors).
            display.append(to_display_pattern(rec, candles, current_price,
                                              resolved="target"))
            continue
        if state == "expired":
            continue                              # voided at the apex, or an old failure
        if state == "failed":
            display.append(to_display_pattern(rec, candles, current_price,
                                              failed_ts=verdict.get("failed_ts")))
            rec["last_seen_ts"] = now_ts
            survivors[key] = rec                  # keep briefly so 'failed' can show
            continue
        display.append(to_display_pattern(rec, candles, current_price))
        rec["last_seen_ts"] = now_ts
        survivors[key] = rec

    # Fresh detections that are NOT a re-fit of a tracked structure show as-is:
    # a forming pattern, or a different type/direction entirely. A fresh re-fit
    # of a tracked structure is dropped — the frozen one is the story.
    tracked = set(by_key)
    for p in fresh or []:
        if _structure_key(p) in tracked:
            continue
        display.append(p)

    updated = sorted(survivors.values(),
                     key=lambda r: r.get("break_ts", 0), reverse=True)[:MAX_TRACKED]
    return display, updated


# ── KV-backed load / save (thin, guarded) ────────────────────────────────────

def _kv_key(symbol: str, tf: str, environment: str) -> str:
    return f"patlive:{environment}:{symbol}:{tf}"


def load(symbol: str, tf: str, environment: str) -> List[Dict]:
    """Tracked records for a symbol/timeframe, or [] on any problem."""
    try:
        raw = kv.get_value(_kv_key(symbol, tf, environment))
        if not raw:
            return []
        data = json.loads(raw)
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def save(symbol: str, tf: str, environment: str, records: List[Dict]) -> None:
    """Persist the tracked records. Best-effort; never raises."""
    try:
        kv.set_value(_kv_key(symbol, tf, environment),
                     json.dumps(records, default=str))
    except Exception:
        pass


# ── small helpers ────────────────────────────────────────────────────────────

def _structs_match(a: Dict, b: Dict) -> bool:
    """`_same_structure`, but a missing rail field can never crash the display
    path — an unmatchable pattern just isn't deduped (a possible duplicate card
    is far better than a 500 on the analysis view)."""
    try:
        return _same_structure(a, b)
    except (KeyError, TypeError):
        return False


def _num(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _index_of_ts(candles: Sequence[Dict], ts) -> Optional[int]:
    if ts is None:
        return None
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    for i, c in enumerate(candles or []):
        if _num(c.get("timestamp")) is not None and int(c["timestamp"]) == ts:
            return i
    return None


def _fresh_bars(candles: Sequence[Dict], ts) -> int:
    """How many closed bars since ``ts`` (0 = the last bar). Large when unknown."""
    idx = _index_of_ts(candles, ts)
    if idx is None:
        return 10 ** 9
    return (len(candles) - 1) - idx
