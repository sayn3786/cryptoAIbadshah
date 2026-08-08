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

    return {
        "symbol": symbol, "timeframe": tf,
        "key": f"{p.get('type')}:{p['direction']}:{int(break_ts)}",
        "type": p.get("type"), "label": p.get("label"),
        "direction": p["direction"],
        "break_ts": int(break_ts),
        "break_level": round(brk_lvl, 8),
        "fail_level": round(fail_lvl, 8),
        "target": target,
        "apex_ts": _apex_ts(upper_line, lower_line),
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
      'expired'     price is past the apex without resolving

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
    if brk is None or fail is None or target is None or break_ts is None:
        return {"state": "failed", "reason": "malformed record"}

    if apex_ts is not None and now_ts >= apex_ts:
        return {"state": "expired"}

    if current_price is not None:
        if bullish and current_price >= target:
            return {"state": "target_hit"}
        if not bullish and current_price <= target:
            return {"state": "target_hit"}

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
                       current_price: float, *, failed_ts=None) -> Dict:
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

    status = "failed" if failed_ts else "confirmed"
    return {
        "type": record.get("type"), "label": record.get("label"),
        "direction": record.get("direction"),
        "status": status, "confirmed": failed_ts is None,
        "display_only": True, "tracked": True,
        "target": record.get("target"),
        "upper_now": round(upper_now, 8) if upper_now is not None else None,
        "lower_now": round(lower_now, 8) if lower_now is not None else None,
        "converge_pct": record.get("converge_pct"),
        "breakout_dir": record.get("breakout_dir"),
        "break_ts": record.get("break_ts"),
        "breakout_volume": record.get("breakout_volume"),
        "failed_ts": failed_ts,
        "failure_reason": ("closed back through the level" if failed_ts else None),
        "retest": retest,
        "upper_line": record.get("upper_line"),
        "lower_line": record.get("lower_line"),
        "sweep": None, "conflict": None,
    }


# ── Reconcile: fresh detections + what we were tracking ──────────────────────

def reconcile(fresh: Sequence[Dict], records: List[Dict], candles: Sequence[Dict],
              current_price: float, symbol: str, tf: str,
              now_ts: int) -> Tuple[List[Dict], List[Dict]]:
    """
    Merge freshly-detected patterns with the ones we were tracking. PURE — the
    KV read and write happen in ``load``/``save`` around this.

    Returns ``(additions, updated_records)``:
      * ``additions`` — tracked patterns to append to the card list because the
        fresh detector no longer surfaces them (deduped against what it does).
      * ``updated_records`` — the store to persist: fresh confirmations upserted,
        resolved/expired ones dropped.
    """
    by_key: Dict[str, Dict] = {r["key"]: dict(r) for r in records
                               if isinstance(r, dict) and r.get("key")}

    # Upsert every fresh confirmation — refresh rails/target while it is still
    # detected, so the record stays current for the day the detector loses it.
    for p in fresh or []:
        rec = record_from_pattern(p, symbol, tf, now_ts)
        if rec:
            prior = by_key.get(rec["key"])
            if prior:
                rec["first_seen_ts"] = prior.get("first_seen_ts", now_ts)
            by_key[rec["key"]] = rec

    additions: List[Dict] = []
    for key, rec in list(by_key.items()):
        display = to_display_pattern(rec, candles, current_price)
        # Already on the card via a fresh detection? Leave it to the fresh one.
        if any(_structs_match(display, f) for f in (fresh or [])):
            rec["last_seen_ts"] = now_ts
            continue
        verdict = reevaluate(rec, candles, current_price, now_ts)
        state = verdict.get("state")
        if state in ("target_hit", "expired"):
            del by_key[key]                       # resolved — stop tracking
            continue
        if state == "failed":
            additions.append(to_display_pattern(rec, candles, current_price,
                                                failed_ts=verdict.get("failed_ts")))
            rec["last_seen_ts"] = now_ts
            continue
        additions.append(display)                 # live — keep showing it
        rec["last_seen_ts"] = now_ts

    # Newest breakouts first, and never store more than the cap.
    updated = sorted(by_key.values(),
                     key=lambda r: r.get("break_ts", 0), reverse=True)[:MAX_TRACKED]
    return additions, updated


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
