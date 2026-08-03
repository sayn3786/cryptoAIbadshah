"""
Recording what the detectors saw, and when.

Pattern state was entirely ephemeral. Every detector recomputes from candles on
each request, so "this divergence was confirmed on the 4pm bar and expired
eleven candles later" existed only while those candles stayed inside the
lookback. Once they aged out there was no way to ask whether a pattern had ever
fired, how long it lasted, or what followed it.

**This is a log, never an input.** The detectors read candles and are the only
source of truth about pattern state; if a row here ever disagreed with a
recomputation, the recomputation is right. Nothing in the scoring path reads
from this module, deliberately — the same rule that keeps postmortem data from
modifying live strategy parameters.

Written on the publication bar only, keyed on that bar. Re-running a publication
records nothing new.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

import deploy_context
from db import session_scope
from signal_store import _sql, _row_to_dict, SignalValidationError

__all__ = [
    "PATTERN_KINDS", "OBSERVABLE_STATUSES", "build_events", "stats",
    "record_events", "list_events", "has_pattern_events",
]

# Matches the keys in lifecycle.FRESH_BARS. A kind not listed here is not
# recorded rather than recorded under a name nothing else recognises.
PATTERN_KINDS = ("rsi_divergence", "choch", "liquidity_grab", "engulfing",
                 "flag", "triangle", "acc_eql_fvg")

OBSERVABLE_STATUSES = ("forming", "confirmed", "expired", "invalidated",
                       # Two fits of the same candles confirmed in opposite
                       # directions. Worth counting: it measures how often the
                       # detector produces an unreadable chart.
                       "conflicted")

# Detectors do not all use the same word for the same lifecycle state. Flags say
# "failed" when a breakout gives back its level; the lifecycle module says
# "invalidated". They mean the same thing, and before this map every failed flag
# was silently DROPPED by the allow-list below — which lost exactly the events a
# postmortem is looking for. One canonical value is stored so a query counting
# invalidations does not have to know both spellings.
_STATUS_ALIASES = {
    "failed": "invalidated",
    "broken": "invalidated",
    "retest_failed": "invalidated",
}

# Allow-list for `detail`, exactly as the decision snapshot does it. A future
# detector key holding something large or sensitive cannot leak in, because
# nothing is copied unless it is named.
_DETAIL_KEYS = ("description", "level", "signal", "type", "strength",
                "points", "kind", "invalidation_reason", "closes_to_confirm",
                "breakout_dir", "retest_state", "trend")

_MAX_STR = 400


def _clip(v: Any) -> Any:
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + "…"
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _clip(x) for k, x in list(v.items())[:12]}
    if isinstance(v, (list, tuple)):
        return [_clip(x) for x in list(v)[:8]]
    return str(v)[:_MAX_STR]


def _detail(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _clip(payload[k]) for k in _DETAIL_KEYS if payload.get(k) is not None}


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise SignalValidationError("candle_close_time must be timezone-aware")
        return value.astimezone(timezone.utc)
    raise SignalValidationError("candle_close_time must be a datetime")


def idempotency_key(environment: str, symbol: str, timeframe: str,
                    kind: str, status: str, close_t: datetime,
                    pattern_type: Optional[str] = None,
                    direction: Optional[str] = None) -> str:
    """
    Derived from the BAR, never the clock.

    Two observations of the same pattern in the same state on the same candle
    are the same event, however many times the analysis is recomputed — and it
    is recomputed on every dashboard load.

    TYPE AND DIRECTION ARE PART OF THE IDENTITY. Without them a bullish flag
    and a bearish flag confirming on the same bar hashed to the same key, so
    the second was discarded as a duplicate — quietly undercounting exactly
    what a "how often do flags fail" question is trying to measure. They are
    different observations and each deserves its row.
    """
    raw = "|".join([environment, symbol, timeframe, kind, status,
                    _utc(close_t).isoformat(), pattern_type or "", direction or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def build_events(symbol: str, timeframe: str, close_t: datetime,
                 patterns: Iterable[Dict[str, Any]],
                 *, environment: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Turn annotated detector payloads into rows.

    Each item needs ``kind`` and a lifecycle ``status``; everything else is
    optional. Items without both are skipped rather than stored half-formed —
    a row that cannot say what it observed is worse than no row.
    """
    env = environment or deploy_context.environment()
    sym = (symbol or "").upper()
    out: List[Dict[str, Any]] = []
    for p in patterns or []:
        if not p:
            continue
        kind, status = p.get("kind"), p.get("status")
        status = _STATUS_ALIASES.get(status, status)
        if kind not in PATTERN_KINDS or status not in OBSERVABLE_STATUSES:
            continue
        out.append({
            "environment": env,
            "symbol": sym,
            "timeframe": timeframe,
            "pattern_kind": kind,
            "pattern_type": p.get("type") or p.get("signal"),
            "direction": p.get("direction"),
            "status": status,
            "candle_close_time": _utc(close_t),
            "age_candles": p.get("age_candles"),
            "fresh_bars": p.get("fresh_bars"),
            "freshness": (None if p.get("freshness") is None
                          else Decimal(str(round(float(p["freshness"]), 4)))),
            "strength": (None if p.get("strength") is None
                         else Decimal(str(p["strength"]))),
            "detail": _detail(p),
            "idempotency_key": idempotency_key(
                env, sym, timeframe, kind, status, close_t,
                p.get("type") or p.get("signal"), p.get("direction")),
        })
    return out


def has_pattern_events(session) -> bool:
    """
    Does the table exist yet?

    Deploy-then-migrate: the code ships before the migration is run by hand, so
    recording has to be a no-op until the table is there rather than an error on
    every publication.
    """
    return bool(session.execute(
        _sql("SELECT to_regclass('pattern_events') IS NOT NULL")).scalar())


def record_events(rows: List[Dict[str, Any]], *, session=None) -> Dict[str, Any]:
    """
    Insert observations, skipping any already recorded for that bar.

    Never raises for an expected failure — this is a log, and losing a log entry
    must never stop a signal being published.
    """
    if not rows:
        return {"recorded": 0, "duplicates": 0, "skipped_reason": None}

    def _work(s):
        if not has_pattern_events(s):
            return {"recorded": 0, "duplicates": 0,
                    "skipped_reason": "MIGRATION_005_NOT_APPLIED"}
        recorded = 0
        for r in rows:
            res = s.execute(_sql("""
                INSERT INTO pattern_events
                    (environment, symbol, timeframe, pattern_kind, pattern_type,
                     direction, status, candle_close_time, age_candles,
                     fresh_bars, freshness, strength, detail, idempotency_key)
                VALUES
                    (:environment, :symbol, :timeframe, :pattern_kind, :pattern_type,
                     :direction, :status, :candle_close_time, :age_candles,
                     :fresh_bars, :freshness, :strength, CAST(:detail AS jsonb),
                     :idempotency_key)
                ON CONFLICT (idempotency_key) DO NOTHING
            """), {**r, "detail": json.dumps(r["detail"])})
            recorded += int(res.rowcount or 0)
        return {"recorded": recorded, "duplicates": len(rows) - recorded,
                "skipped_reason": None}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def stats(*, days: int = 30, symbol: Optional[str] = None,
          environment: Optional[str] = None, session=None) -> Dict[str, Any]:
    """
    How patterns worked out, grouped by timeframe and kind.

    Counts DISTINCT pattern identities, not rows. A pattern that stays confirmed
    for six bars logs six observations — one per bar, which is what makes the
    log idempotent — so counting rows would say "six confirmations" about one
    pattern. Identity here is (symbol, timeframe, kind, type, direction).

    THE CAVEAT THAT MATTERS: two separate instances of the same pattern on the
    same symbol inside the window collapse into one. A shorter window is a
    truer count, a longer one a bigger sample, and there is no way to have both
    until the detectors carry an instance id. `days` is reported back so a
    reader knows which trade-off they are looking at.

    `invalidation_rate` is invalidated / (confirmed + invalidated) — of the
    patterns that resolved, the share that broke. Patterns still forming are
    excluded: they have not resolved, and counting them either way would be a
    guess. None when nothing has resolved yet.
    """
    days = max(1, min(int(days or 30), 365))
    where = ["candle_close_time >= now() - make_interval(days => :days)"]
    params: Dict[str, Any] = {"days": days}
    if symbol:
        where.append("symbol = :symbol"); params["symbol"] = symbol.upper()
    if environment and environment != "all":
        where.append("environment = :env"); params["env"] = environment
    clause = " AND ".join(where)

    def _work(s):
        if not has_pattern_events(s):
            return {"available": False, "days": days, "groups": []}
        rows = s.execute(_sql(f"""
            SELECT timeframe, pattern_kind, status,
                   count(DISTINCT (symbol, pattern_type, direction)) AS n
            FROM   pattern_events
            WHERE  {clause}
            GROUP  BY timeframe, pattern_kind, status
        """), params).all()

        by: Dict[tuple, Dict[str, Any]] = {}
        for r in rows:
            m = r._mapping
            key = (m["timeframe"], m["pattern_kind"])
            g = by.setdefault(key, {"timeframe": m["timeframe"],
                                    "pattern_kind": m["pattern_kind"],
                                    "forming": 0, "confirmed": 0,
                                    "invalidated": 0, "expired": 0})
            if m["status"] in g:
                g[m["status"]] = int(m["n"])
        groups = []
        for g in by.values():
            resolved = g["confirmed"] + g["invalidated"]
            g["resolved"] = resolved
            g["invalidation_rate"] = (round(g["invalidated"] / resolved, 4)
                                      if resolved else None)
            groups.append(g)
        groups.sort(key=lambda g: (g["timeframe"], g["pattern_kind"]))
        return {"available": True, "days": days, "groups": groups}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def list_events(*, symbol: Optional[str] = None,
                timeframe: Optional[str] = None,
                pattern_kind: Optional[str] = None,
                status: Optional[str] = None,
                environment: Optional[str] = None,
                limit: int = 100, session=None) -> List[Dict[str, Any]]:
    """Newest observation first."""
    limit = max(1, min(int(limit or 100), 500))
    where, params = ["1=1"], {"limit": limit}
    if symbol:
        where.append("symbol = :symbol"); params["symbol"] = symbol.upper()
    if timeframe:
        where.append("timeframe = :timeframe"); params["timeframe"] = timeframe
    if pattern_kind:
        where.append("pattern_kind = :kind"); params["kind"] = pattern_kind
    if status:
        where.append("status = :status"); params["status"] = status
    if environment and environment != "all":
        where.append("environment = :env"); params["env"] = environment
    clause = " AND ".join(where)

    def _work(s):
        if not has_pattern_events(s):
            return []
        rows = s.execute(_sql(
            f"SELECT * FROM pattern_events WHERE {clause} "
            f"ORDER BY candle_close_time DESC, observed_at DESC LIMIT :limit"
        ), params).all()
        return [_row_to_dict(r) for r in rows]

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)
