"""
Repository / service layer for persistent signal tracking.

All SQL for the signal tables lives here. Route handlers call these functions;
they never build queries themselves.

Two things in this module are pure and therefore testable with no database at
all — deliberately, because they are the rules that protect real money:

  * ``validate_price_structure`` — a LONG whose stop sits above entry, or whose
    target sits below it, is not a bad trade, it is a broken record. Rejected
    before anything is written.
  * ``assert_transition`` — the lifecycle state machine. A terminal signal can
    never reopen, and a signal cannot end up both TP_HIT and SL_HIT.

Everything else is transactional. The create path writes the signal, its
targets, its decision snapshot and its CREATED event inside ONE transaction:
if any part fails the whole thing rolls back and the caller must not publish.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence

import deploy_context
from db import session_scope, DatabaseUnavailable   # noqa: F401  (re-exported)

__all__ = [
    "SignalValidationError", "InvalidTransition",
    "STATUSES", "TERMINAL_STATUSES", "EVENT_TYPES", "ALLOWED_TRANSITIONS",
    "validate_price_structure", "assert_transition", "make_idempotency_key",
    "has_environment_column", "reset_capabilities",
    "create_signal", "get_signal", "list_active_signals", "list_signals",
    "attach_targets",
    "record_target_hit", "record_stop_loss_hit", "close_signal",
    "expire_signal", "cancel_signal", "upsert_postmortem", "archive_signal",
    "list_postmortems", "usage_report",
]


class SignalValidationError(ValueError):
    """The signal record is internally inconsistent. Never persisted."""


class InvalidTransition(ValueError):
    """The requested lifecycle change is not allowed from the current status."""


# ── Lifecycle ────────────────────────────────────────────────────────────────

STATUSES = ("OPEN", "PARTIAL_TP", "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED")

TERMINAL_STATUSES = frozenset({"TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"})

EVENT_TYPES = ("CREATED", "TARGET_HIT", "STOP_LOSS_HIT", "CLOSED",
               "EXPIRED", "CANCELLED", "ANALYSIS_ADDED", "ARCHIVED")

# A terminal status maps to an EMPTY set: nothing may follow it. That single
# fact is what stops a closed trade being reopened and what stops a signal
# recording both TP_HIT and SL_HIT as its outcome.
ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    "OPEN":       frozenset({"PARTIAL_TP", "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"}),
    "PARTIAL_TP": frozenset({"PARTIAL_TP", "TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"}),
    "TP_HIT":     frozenset(),
    "SL_HIT":     frozenset(),
    "CLOSED":     frozenset(),
    "EXPIRED":    frozenset(),
    "CANCELLED":  frozenset(),
}


def assert_transition(current: str, target: str) -> None:
    """Raise InvalidTransition unless current -> target is permitted."""
    if current not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"unknown current status {current!r}")
    if target not in STATUSES:
        raise InvalidTransition(f"unknown target status {target!r}")
    if target not in ALLOWED_TRANSITIONS[current]:
        if current in TERMINAL_STATUSES:
            raise InvalidTransition(
                f"{current} is terminal; refusing to move to {target}"
            )
        raise InvalidTransition(f"{current} -> {target} is not an allowed transition")


# ── Value helpers ────────────────────────────────────────────────────────────

def _dec(value: Any, field: str) -> Decimal:
    """
    Coerce to Decimal via str(), never via float().

    Decimal(0.1) is 0.1000000000000000055511151231257827, Decimal("0.1") is
    exactly 0.1. Going through str() is what keeps a stored price equal to the
    price the strategy actually decided on.
    """
    if value is None:
        raise SignalValidationError(f"{field} is required")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SignalValidationError(f"{field} is not a number: {value!r}") from exc
    if not d.is_finite():
        raise SignalValidationError(f"{field} must be finite, got {value!r}")
    return d


def _opt_dec(value: Any, field: str) -> Optional[Decimal]:
    return None if value is None else _dec(value, field)


def _utc(value: Any, field: str) -> datetime:
    """
    Normalize to a timezone-aware UTC datetime.

    A naive datetime is REJECTED rather than assumed to be UTC: guessing the
    zone of a candle timestamp silently shifts every stored signal.
    """
    if isinstance(value, (int, float)):        # epoch milliseconds
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SignalValidationError(f"{field} is not an ISO timestamp: {value!r}") from exc
    if not isinstance(value, datetime):
        raise SignalValidationError(f"{field} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        raise SignalValidationError(
            f"{field} is naive; supply an aware UTC datetime or epoch milliseconds"
        )
    return value.astimezone(timezone.utc)


def validate_price_structure(direction: str,
                             entry_price: Any,
                             stop_loss: Any,
                             targets: Sequence[Any]) -> None:
    """
    Enforce direction-specific geometry. Pure — no database needed.

    LONG : stop_loss < entry_price, every target > entry_price
    SHORT: stop_loss > entry_price, every target < entry_price

    Targets must also be strictly ordered away from entry (TP1 then TP2 …), so
    "TP2 hit" always implies more profit than "TP1 hit".
    """
    if direction not in ("LONG", "SHORT"):
        raise SignalValidationError(f"direction must be LONG or SHORT, got {direction!r}")

    entry = _dec(entry_price, "entry_price")
    stop = _dec(stop_loss, "stop_loss")
    if entry <= 0:
        raise SignalValidationError("entry_price must be > 0")
    if stop <= 0:
        raise SignalValidationError("stop_loss must be > 0")

    tps = [_dec(t, f"target[{i}]") for i, t in enumerate(targets or [])]
    if any(t <= 0 for t in tps):
        raise SignalValidationError("every target_price must be > 0")

    if direction == "LONG":
        if stop >= entry:
            raise SignalValidationError(
                f"LONG stop_loss ({stop}) must be below entry_price ({entry})")
        for i, t in enumerate(tps, 1):
            if t <= entry:
                raise SignalValidationError(
                    f"LONG target {i} ({t}) must be above entry_price ({entry})")
        if any(b <= a for a, b in zip(tps, tps[1:])):
            raise SignalValidationError("LONG targets must increase: TP1 < TP2 < TP3")
    else:
        if stop <= entry:
            raise SignalValidationError(
                f"SHORT stop_loss ({stop}) must be above entry_price ({entry})")
        for i, t in enumerate(tps, 1):
            if t >= entry:
                raise SignalValidationError(
                    f"SHORT target {i} ({t}) must be below entry_price ({entry})")
        if any(b >= a for a, b in zip(tps, tps[1:])):
            raise SignalValidationError("SHORT targets must decrease: TP1 > TP2 > TP3")


def make_idempotency_key(signal_id: Any, event_type: str, source_ts: Any) -> str:
    """
    Stable key for an event.

    Derived from the signal, the event type and the SOURCE timestamp (the
    candle or exchange event that caused it) — never from wall-clock time, or
    a replay a second later would look like a new event.
    """
    if event_type not in EVENT_TYPES:
        raise SignalValidationError(f"unknown event_type {event_type!r}")
    if isinstance(source_ts, datetime):
        stamp = _utc(source_ts, "source_ts").isoformat()
    else:
        stamp = str(source_ts)
    raw = f"{signal_id}|{event_type}|{stamp}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{event_type.lower()}:{digest}"


def _jsonb(value: Any) -> str:
    """Serialise a payload for a JSONB column, with a safe fallback."""
    return json.dumps(value if value is not None else {}, default=str, sort_keys=True)


# ── Internal SQL helpers ─────────────────────────────────────────────────────

def _sql(text_: str):
    from sqlalchemy import text
    return text(text_)


# ── Schema capabilities ──────────────────────────────────────────────────────
# `signals.environment` arrives in migration 002. The code must work on both
# sides of that migration: if it assumed the column, then deploying before
# migrating would fail EVERY write — and with DB_REQUIRED=true that means
# publishing stops entirely. So the column is probed once and the SQL adapts.
#
# Cached per process. A serverless instance is short-lived and a new deployment
# is always a cold start, so a preview deploy can never inherit a stale "no
# column" answer from before the migration. The only stale case is a warm
# PRODUCTION instance still writing without the column, whose rows then take the
# 'production' default — which is what they are anyway.

_ENV_COLUMN: Optional[bool] = None


def reset_capabilities() -> None:
    """Forget probed schema capabilities. Used by tests and after a migration."""
    global _ENV_COLUMN
    _ENV_COLUMN = None


def has_environment_column(session) -> bool:
    """True when signals.environment exists (i.e. migration 002 has been run)."""
    global _ENV_COLUMN
    if _ENV_COLUMN is None:
        try:
            # pg_attribute rather than information_schema: unqualified
            # to_regclass respects search_path, so this resolves in whatever
            # schema the tables actually live in (tests use a throwaway one).
            _ENV_COLUMN = bool(session.execute(_sql("""
                SELECT count(*) > 0
                FROM   pg_attribute
                WHERE  attrelid = to_regclass('signals')
                  AND  attname  = 'environment'
                  AND  NOT attisdropped
            """)).scalar())
        except Exception:
            # An unreadable catalog is not a reason to fail a write. Assume the
            # older schema: writes still succeed, just untagged.
            _ENV_COLUMN = False
    return _ENV_COLUMN


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            # format(…, 'f') not str(): str() switches to scientific notation
            # below 1e-6, so a sub-satoshi altcoin price would leave the API as
            # "1.2345E-8". Exact either way, but plain notation is what a
            # consumer can parse without surprises. Never float().
            d[k] = format(v, "f")
        elif isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d


def _insert_event(session, signal_id, event_type: str, event_time,
                  price=None, metadata=None, idempotency_key=None) -> bool:
    """
    Append a lifecycle event. Returns True if it was newly inserted, False if
    the idempotency key was already present (a replay).
    """
    key = idempotency_key or make_idempotency_key(signal_id, event_type, event_time)
    res = session.execute(_sql("""
        INSERT INTO signal_events
               (signal_id, event_type, event_time, price, metadata, idempotency_key)
        VALUES (:sid, :etype, :etime, :price, CAST(:meta AS JSONB), :key)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
    """), {"sid": str(signal_id), "etype": event_type,
           "etime": _utc(event_time, "event_time"),
           "price": _opt_dec(price, "price"),
           "meta": _jsonb(metadata), "key": key})
    return res.first() is not None


# ── Create ───────────────────────────────────────────────────────────────────

def create_signal(*,
                  symbol: str,
                  exchange: str,
                  timeframe: str,
                  direction: str,
                  strategy_name: str,
                  strategy_version: str,
                  candle_open_time,
                  candle_close_time,
                  generated_at,
                  entry_price,
                  stop_loss,
                  targets: Sequence[Any],
                  indicator_values: Dict[str, Any],
                  market_context: Dict[str, Any],
                  source_timestamps: Dict[str, Any],
                  input_candle_count: int,
                  data_quality_flags: Optional[Dict[str, Any]] = None,
                  confidence_score: Any = None,
                  environment: Optional[str] = None,
                  session=None) -> Dict[str, Any]:
    """
    Persist a published signal with its targets, decision snapshot and CREATED
    event, atomically.

    Returns ``{"signal": {...}, "created": bool, "idempotent_hit": bool}``.

    When a signal already exists for the same
    (environment, symbol, exchange, timeframe, strategy_name, strategy_version,
    candle_close_time) the existing row is returned with ``created=False`` and
    nothing is written — re-evaluating the same closed candle is a no-op, which
    is what makes the caller safe to retry.

    ``environment`` defaults to this deployment's own label (see
    deploy_context). It is part of the idempotency key so that a PREVIEW deploy
    sharing the database cannot claim a candle and make production's write look
    like a duplicate. Before migration 002 the column does not exist; writes
    then proceed untagged and idempotency is shared, as it was.

    Raises SignalValidationError BEFORE opening a transaction if the record is
    inconsistent. Any database failure propagates after a full rollback, and
    the caller must then not publish the signal as actionable.
    """
    symbol = (symbol or "").strip().upper()
    exchange = (exchange or "").strip()
    timeframe = (timeframe or "").strip()
    if not symbol:
        raise SignalValidationError("symbol is required")
    if not exchange:
        raise SignalValidationError("exchange is required")
    if not timeframe:
        raise SignalValidationError("timeframe is required")
    if not strategy_name or not strategy_version:
        raise SignalValidationError("strategy_name and strategy_version are required")

    env = (environment or deploy_context.environment() or "").strip().lower()
    if not deploy_context.SLUG_RE.match(env):
        # Never fail a real write over a malformed label — the CHECK constraint
        # would reject it, so normalise instead.
        env = "unknown"

    # Geometry first: never open a transaction for a record we will reject.
    validate_price_structure(direction, entry_price, stop_loss, targets)

    open_t = _utc(candle_open_time, "candle_open_time")
    close_t = _utc(candle_close_time, "candle_close_time")
    if close_t <= open_t:
        raise SignalValidationError("candle_close_time must be after candle_open_time")
    gen_t = _utc(generated_at, "generated_at")

    entry_d = _dec(entry_price, "entry_price")
    stop_d = _dec(stop_loss, "stop_loss")
    tps = [_dec(t, f"target[{i}]") for i, t in enumerate(targets or [])]
    conf_d = _opt_dec(confidence_score, "confidence_score")

    def _work(s) -> Dict[str, Any]:
        # The ON CONFLICT target must name the columns of an existing unique
        # index, so it has to match whichever idempotency index this database
        # actually has — pre-002 (no environment) or post-002 (with it).
        tagged = has_environment_column(s)
        env_col = ", environment" if tagged else ""
        env_val = ", :env" if tagged else ""
        env_key = "environment, " if tagged else ""
        env_and = "AND environment=:env " if tagged else ""

        params = {"symbol": symbol, "exchange": exchange, "timeframe": timeframe,
                  "direction": direction, "sname": strategy_name,
                  "sver": strategy_version, "open_t": open_t, "close_t": close_t,
                  "gen_t": gen_t, "entry": entry_d, "stop": stop_d, "conf": conf_d}
        if tagged:
            params["env"] = env

        inserted = s.execute(_sql(f"""
            INSERT INTO signals
                   (symbol, exchange, timeframe, direction,
                    strategy_name, strategy_version,
                    candle_open_time, candle_close_time, generated_at,
                    entry_price, stop_loss, confidence_score, status{env_col})
            VALUES (:symbol, :exchange, :timeframe, :direction,
                    :sname, :sver,
                    :open_t, :close_t, :gen_t,
                    :entry, :stop, :conf, 'OPEN'{env_val})
            ON CONFLICT ({env_key}symbol, exchange, timeframe,
                         strategy_name, strategy_version, candle_close_time)
            DO NOTHING
            RETURNING id
        """), params).first()

        if inserted is None:
            # Idempotent hit: this candle already produced a signal. Return it
            # untouched — do NOT write targets/snapshot/event again.
            existing = s.execute(_sql(f"""
                SELECT * FROM signals
                WHERE symbol=:symbol AND exchange=:exchange AND timeframe=:timeframe
                  AND strategy_name=:sname AND strategy_version=:sver
                  AND candle_close_time=:close_t {env_and}
            """), params).first()
            return {"signal": _row_to_dict(existing) if existing else None,
                    "created": False, "idempotent_hit": True}

        signal_id = inserted[0]

        for n, price in enumerate(tps, start=1):
            s.execute(_sql("""
                INSERT INTO signal_targets (signal_id, target_number, target_price)
                VALUES (:sid, :n, :price)
            """), {"sid": str(signal_id), "n": n, "price": price})

        s.execute(_sql("""
            INSERT INTO signal_indicator_snapshots
                   (signal_id, indicator_values, market_context, source_timestamps,
                    input_candle_count, data_quality_flags)
            VALUES (:sid, CAST(:iv AS JSONB), CAST(:mc AS JSONB), CAST(:st AS JSONB),
                    :count, CAST(:dq AS JSONB))
        """), {"sid": str(signal_id),
               "iv": _jsonb(indicator_values), "mc": _jsonb(market_context),
               "st": _jsonb(source_timestamps), "count": int(input_candle_count or 0),
               "dq": _jsonb(data_quality_flags or {})})

        # Deployment detail (branch, short sha) lives in the event trail rather
        # than in a column: it is audit context for "which preview wrote this",
        # not something anything queries or filters on.
        _deploy = {k: v for k, v in deploy_context.describe().items() if v}
        _insert_event(s, signal_id, "CREATED", gen_t, price=entry_d,
                      metadata={"targets": [str(t) for t in tps],
                                "deployment": {**_deploy, "environment": env}},
                      idempotency_key=make_idempotency_key(signal_id, "CREATED", close_t))

        row = s.execute(_sql("SELECT * FROM signals WHERE id=:id"),
                        {"id": str(signal_id)}).first()
        return {"signal": _row_to_dict(row), "created": True, "idempotent_hit": False}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


# ── Read ─────────────────────────────────────────────────────────────────────

def _lock_signal(s, signal_id):
    """
    Take a row lock for a lifecycle update.

    FOR UPDATE serialises concurrent updaters on this signal, so two workers
    reacting to the same candle cannot both read OPEN and both write a terminal
    status.
    """
    row = s.execute(_sql("SELECT * FROM signals WHERE id=:id FOR UPDATE"),
                    {"id": str(signal_id)}).first()
    if row is None:
        raise SignalValidationError(f"signal {signal_id} not found")
    return row


def get_signal(signal_id, *, session=None) -> Optional[Dict[str, Any]]:
    """One signal with its targets, snapshot, events and postmortem."""
    def _work(s):
        row = s.execute(_sql("SELECT * FROM signals WHERE id=:id"),
                        {"id": str(signal_id)}).first()
        if row is None:
            return None
        out = _row_to_dict(row)
        out["targets"] = [_row_to_dict(r) for r in s.execute(_sql(
            "SELECT * FROM signal_targets WHERE signal_id=:id ORDER BY target_number"
        ), {"id": str(signal_id)}).all()]
        snap = s.execute(_sql(
            "SELECT * FROM signal_indicator_snapshots WHERE signal_id=:id"
        ), {"id": str(signal_id)}).first()
        out["indicator_snapshot"] = _row_to_dict(snap) if snap else None
        out["events"] = [_row_to_dict(r) for r in s.execute(_sql(
            "SELECT * FROM signal_events WHERE signal_id=:id ORDER BY event_time, created_at"
        ), {"id": str(signal_id)}).all()]
        pm = s.execute(_sql("SELECT * FROM signal_postmortems WHERE signal_id=:id"),
                       {"id": str(signal_id)}).first()
        out["postmortem"] = _row_to_dict(pm) if pm else None
        return out

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

# Reserved environment filter values — an environment can never be named these.
ENV_CURRENT = "current"   # only rows this deployment wrote (the default)
ENV_ALL = "all"           # every environment, preview rows included


def _environment_clause(session, environment: Optional[str]):
    """
    SQL fragment restricting a query to one environment.

    Reads default to the CURRENT environment, so a production deployment does
    not serve signals a preview deploy happened to write into the shared
    database. Pass ``"all"`` to see everything.

    Returns ``("", {})`` — no filtering — when the column does not exist yet,
    so the same code runs on both sides of migration 002.
    """
    if not has_environment_column(session):
        return "", {}
    env = (environment or ENV_CURRENT).strip().lower()
    if env == ENV_ALL:
        return "", {}
    if env == ENV_CURRENT:
        env = deploy_context.environment()
    if not deploy_context.SLUG_RE.match(env):
        raise SignalValidationError(
            "environment filter must be a short lowercase slug, 'current' or 'all'")
    return " AND environment = :env", {"env": env}


def list_signals(*, statuses: Optional[Iterable[str]] = None,
                 symbol: Optional[str] = None,
                 timeframe: Optional[str] = None,
                 direction: Optional[str] = None,
                 strategy_version: Optional[str] = None,
                 exchange: Optional[str] = None,
                 include_archived: bool = False,
                 environment: Optional[str] = None,
                 limit: int = DEFAULT_PAGE_SIZE,
                 offset: int = 0,
                 session=None) -> Dict[str, Any]:
    """
    Paginated history, newest first. Archived rows are hidden unless asked for.

    ``environment`` defaults to this deployment's own — pass ``"all"`` to
    include rows written by other environments (e.g. preview deploys sharing the
    database), or a specific slug to look at one.

    Returns {"items": [...], "limit", "offset", "total", "has_more"}.
    """
    limit = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    offset = max(0, int(offset or 0))

    where, params = ["1=1"], {"limit": limit, "offset": offset}
    if statuses:
        sts = [str(x).upper() for x in statuses]
        bad = [x for x in sts if x not in STATUSES]
        if bad:
            raise SignalValidationError(f"unknown status filter: {', '.join(bad)}")
        where.append("status = ANY(:statuses)")
        params["statuses"] = sts
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.strip().upper()
    if timeframe:
        where.append("timeframe = :timeframe")
        params["timeframe"] = timeframe.strip()
    if direction:
        d = direction.strip().upper()
        if d not in ("LONG", "SHORT"):
            raise SignalValidationError("direction filter must be LONG or SHORT")
        where.append("direction = :direction")
        params["direction"] = d
    if strategy_version:
        where.append("strategy_version = :sver")
        params["sver"] = strategy_version.strip()
    if exchange:
        where.append("exchange = :exchange")
        params["exchange"] = exchange.strip()
    if not include_archived:
        where.append("archived_at IS NULL")

    base = " AND ".join(where)

    def _work(s):
        env_sql, env_params = _environment_clause(s, environment)
        clause = base + env_sql
        p = {**params, **env_params}
        total = s.execute(_sql(f"SELECT count(*) FROM signals WHERE {clause}"),
                          p).scalar() or 0
        rows = s.execute(_sql(
            f"SELECT * FROM signals WHERE {clause} "
            f"ORDER BY generated_at DESC, id DESC LIMIT :limit OFFSET :offset"
        ), p).all()
        items = [_row_to_dict(r) for r in rows]
        return {"items": items, "limit": limit, "offset": offset,
                "total": int(total), "has_more": offset + len(items) < int(total)}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def attach_targets(rows: List[Dict[str, Any]], *, session=None) -> List[Dict[str, Any]]:
    """
    Add a ``targets`` list to each signal dict, in ONE query.

    The tracker needs the ladder for every row it shows. Calling get_signal per
    row would be a query per signal on a serverless connection — this is the
    same information for one round trip.
    """
    ids = [str(r["id"]) for r in rows or [] if r.get("id")]
    if not ids:
        for r in rows or []:
            r.setdefault("targets", [])
        return rows

    def _work(s):
        by_signal: Dict[str, List[Dict[str, Any]]] = {}
        for t in s.execute(_sql("""
            SELECT * FROM signal_targets
            WHERE  signal_id = ANY(CAST(:ids AS uuid[]))
            ORDER  BY signal_id, target_number
        """), {"ids": ids}).all():
            d = _row_to_dict(t)
            by_signal.setdefault(str(d["signal_id"]), []).append(d)
        for r in rows:
            r["targets"] = by_signal.get(str(r.get("id")), [])
        return rows

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def list_active_signals(*, environment: Optional[str] = None, session=None,
                        limit: int = MAX_PAGE_SIZE) -> List[Dict[str, Any]]:
    """Signals still working: OPEN or PARTIAL_TP, unarchived, this environment."""
    return list_signals(statuses=["OPEN", "PARTIAL_TP"], limit=limit,
                        environment=environment, session=session)["items"]


# ── Lifecycle updates ────────────────────────────────────────────────────────

def record_target_hit(signal_id, target_number: int, hit_price, hit_at, *,
                      source_ts=None, session=None) -> Dict[str, Any]:
    """
    Mark a take-profit as reached. Idempotent on (signal, target, source time).

    Reaching the LAST target is terminal (TP_HIT); any earlier one moves the
    signal to PARTIAL_TP and leaves it working.
    """
    target_number = int(target_number)
    if target_number <= 0:
        raise SignalValidationError("target_number must be > 0")
    hit_at_t = _utc(hit_at, "hit_at")
    src = source_ts if source_ts is not None else hit_at_t

    def _work(s):
        row = _lock_signal(s, signal_id)
        current = row._mapping["status"]

        key = make_idempotency_key(signal_id, "TARGET_HIT", f"{target_number}|{_utc(src, 'source_ts').isoformat()}")
        already = s.execute(_sql(
            "SELECT 1 FROM signal_events WHERE idempotency_key=:k"), {"k": key}).first()
        if already:
            return {"signal": _row_to_dict(_lock_signal(s, signal_id)),
                    "applied": False, "duplicate": True}

        tgt = s.execute(_sql("""
            SELECT * FROM signal_targets WHERE signal_id=:sid AND target_number=:n
        """), {"sid": str(signal_id), "n": target_number}).first()
        if tgt is None:
            raise SignalValidationError(
                f"signal {signal_id} has no target {target_number}")

        total = s.execute(_sql(
            "SELECT count(*) FROM signal_targets WHERE signal_id=:sid"),
            {"sid": str(signal_id)}).scalar() or 0
        new_status = "TP_HIT" if target_number >= int(total) else "PARTIAL_TP"

        assert_transition(current, new_status)

        s.execute(_sql("""
            UPDATE signal_targets SET hit_at=:at, hit_price=:price
            WHERE signal_id=:sid AND target_number=:n AND hit_at IS NULL
        """), {"at": hit_at_t, "price": _dec(hit_price, "hit_price"),
               "sid": str(signal_id), "n": target_number})

        # Compare-and-set: only advance if the status is still what we checked.
        params = {"status": new_status, "sid": str(signal_id), "cur": current}
        if new_status == "TP_HIT":
            s.execute(_sql("""
                UPDATE signals
                SET status=:status, closed_at=:at,
                    close_price=CAST(:price AS NUMERIC),
                    close_reason='TARGET_HIT',
                    realized_return_pct = CASE WHEN direction='LONG'
                        THEN (CAST(:price AS NUMERIC) - entry_price) / entry_price * 100
                        ELSE (entry_price - CAST(:price AS NUMERIC)) / entry_price * 100 END,
                    updated_at=now()
                WHERE id=:sid AND status=:cur
            """), {**params, "at": hit_at_t, "price": _dec(hit_price, "hit_price")})
        else:
            s.execute(_sql("""
                UPDATE signals SET status=:status, updated_at=now()
                WHERE id=:sid AND status=:cur
            """), params)

        _insert_event(s, signal_id, "TARGET_HIT", hit_at_t,
                      price=hit_price,
                      metadata={"target_number": target_number, "new_status": new_status},
                      idempotency_key=key)
        return {"signal": _row_to_dict(_lock_signal(s, signal_id)),
                "applied": True, "duplicate": False, "status": new_status}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def record_stop_loss_hit(signal_id, hit_price, hit_at, *,
                         source_ts=None, session=None) -> Dict[str, Any]:
    """Mark the stop as hit. Terminal. Idempotent on (signal, source time)."""
    hit_at_t = _utc(hit_at, "hit_at")
    src = source_ts if source_ts is not None else hit_at_t
    price_d = _dec(hit_price, "hit_price")

    def _work(s):
        row = _lock_signal(s, signal_id)
        current = row._mapping["status"]

        key = make_idempotency_key(signal_id, "STOP_LOSS_HIT", src)
        if s.execute(_sql("SELECT 1 FROM signal_events WHERE idempotency_key=:k"),
                     {"k": key}).first():
            return {"signal": _row_to_dict(row), "applied": False, "duplicate": True}

        assert_transition(current, "SL_HIT")

        s.execute(_sql("""
            UPDATE signals
            SET status='SL_HIT', closed_at=:at,
                close_price=CAST(:price AS NUMERIC),
                close_reason='STOP_LOSS_HIT',
                realized_return_pct = CASE WHEN direction='LONG'
                    THEN (CAST(:price AS NUMERIC) - entry_price) / entry_price * 100
                    ELSE (entry_price - CAST(:price AS NUMERIC)) / entry_price * 100 END,
                updated_at=now()
            WHERE id=:sid AND status=:cur
        """), {"at": hit_at_t, "price": price_d, "sid": str(signal_id), "cur": current})

        _insert_event(s, signal_id, "STOP_LOSS_HIT", hit_at_t, price=price_d,
                      metadata={"previous_status": current}, idempotency_key=key)
        return {"signal": _row_to_dict(_lock_signal(s, signal_id)),
                "applied": True, "duplicate": False, "status": "SL_HIT"}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def _terminal_update(signal_id, new_status: str, event_type: str,
                     *, price=None, at=None, reason=None,
                     metadata=None, source_ts=None, session=None) -> Dict[str, Any]:
    """Shared path for CLOSED / EXPIRED / CANCELLED."""
    at_t = _utc(at or datetime.now(timezone.utc), "at")
    src = source_ts if source_ts is not None else at_t
    price_d = _opt_dec(price, "close_price")

    def _work(s):
        row = _lock_signal(s, signal_id)
        current = row._mapping["status"]

        key = make_idempotency_key(signal_id, event_type, src)
        if s.execute(_sql("SELECT 1 FROM signal_events WHERE idempotency_key=:k"),
                     {"k": key}).first():
            return {"signal": _row_to_dict(row), "applied": False, "duplicate": True}

        assert_transition(current, new_status)

        # :price is CAST explicitly — on a CANCELLED/EXPIRED signal it is NULL,
        # and Postgres cannot infer a bare parameter's type inside a CASE.
        s.execute(_sql("""
            UPDATE signals
            SET status=:status, closed_at=:at,
                close_price=CAST(:price AS NUMERIC),
                close_reason=:reason,
                realized_return_pct = CASE
                    WHEN CAST(:price AS NUMERIC) IS NULL THEN realized_return_pct
                    WHEN direction='LONG'
                        THEN (CAST(:price AS NUMERIC) - entry_price) / entry_price * 100
                    ELSE (entry_price - CAST(:price AS NUMERIC)) / entry_price * 100 END,
                updated_at=now()
            WHERE id=:sid AND status=:cur
        """), {"status": new_status, "at": at_t, "price": price_d,
               "reason": reason or new_status, "sid": str(signal_id), "cur": current})

        _insert_event(s, signal_id, event_type, at_t, price=price_d,
                      metadata={**(metadata or {}), "previous_status": current},
                      idempotency_key=key)
        return {"signal": _row_to_dict(_lock_signal(s, signal_id)),
                "applied": True, "duplicate": False, "status": new_status}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def close_signal(signal_id, close_price, closed_at=None, *, reason="MANUAL_CLOSE",
                 source_ts=None, session=None) -> Dict[str, Any]:
    return _terminal_update(signal_id, "CLOSED", "CLOSED", price=close_price,
                            at=closed_at, reason=reason, source_ts=source_ts,
                            session=session)


def expire_signal(signal_id, expired_at=None, *, price=None, source_ts=None,
                  session=None) -> Dict[str, Any]:
    return _terminal_update(signal_id, "EXPIRED", "EXPIRED", price=price,
                            at=expired_at, reason="EXPIRED", source_ts=source_ts,
                            session=session)


def cancel_signal(signal_id, cancelled_at=None, *, reason="CANCELLED",
                  source_ts=None, session=None) -> Dict[str, Any]:
    return _terminal_update(signal_id, "CANCELLED", "CANCELLED", at=cancelled_at,
                            reason=reason, source_ts=source_ts, session=session)


# ── Postmortem / archive ─────────────────────────────────────────────────────

def upsert_postmortem(signal_id, *, outcome: str, strategy_version: str,
                      mfe_pct=None, mae_pct=None, duration_minutes=None,
                      failed_conditions=None, analysis_summary=None,
                      session=None) -> Dict[str, Any]:
    """
    Create or replace the postmortem for a signal.

    Never writes back to strategy parameters — this is analysis output only.
    Changing live strategy behaviour requires a new strategy_version, its own
    backtest and human approval.
    """
    if not outcome:
        raise SignalValidationError("outcome is required")

    def _work(s):
        _lock_signal(s, signal_id)             # ensure it exists
        s.execute(_sql("""
            INSERT INTO signal_postmortems
                   (signal_id, outcome, maximum_favorable_excursion_pct,
                    maximum_adverse_excursion_pct, duration_minutes,
                    failed_conditions, analysis_summary, strategy_version)
            VALUES (:sid, :outcome, :mfe, :mae, :dur,
                    COALESCE(CAST(:failed AS JSONB), '[]'::jsonb), :summary, :sver)
            ON CONFLICT (signal_id) DO UPDATE SET
                outcome = EXCLUDED.outcome,
                -- COALESCE, not straight assignment: an update that only
                -- revises the summary must NOT wipe excursions or failed
                -- conditions that an earlier pass computed. Omitting a field
                -- means "leave it alone"; to clear one, pass an explicit
                -- empty value.
                maximum_favorable_excursion_pct =
                    COALESCE(EXCLUDED.maximum_favorable_excursion_pct,
                             signal_postmortems.maximum_favorable_excursion_pct),
                maximum_adverse_excursion_pct =
                    COALESCE(EXCLUDED.maximum_adverse_excursion_pct,
                             signal_postmortems.maximum_adverse_excursion_pct),
                duration_minutes =
                    COALESCE(EXCLUDED.duration_minutes, signal_postmortems.duration_minutes),
                -- the RAW parameter, not EXCLUDED: the INSERT clause already
                -- defaults a missing value to '[]', so EXCLUDED is never NULL
                -- here and COALESCE against it would always overwrite.
                failed_conditions =
                    COALESCE(CAST(:failed AS JSONB), signal_postmortems.failed_conditions),
                analysis_summary =
                    COALESCE(EXCLUDED.analysis_summary, signal_postmortems.analysis_summary),
                strategy_version  = EXCLUDED.strategy_version,
                updated_at        = now()
        """), {"sid": str(signal_id), "outcome": outcome,
               "mfe": _opt_dec(mfe_pct, "mfe_pct"),
               "mae": _opt_dec(mae_pct, "mae_pct"),
               "dur": None if duration_minutes is None else int(duration_minutes),
               # None => leave the stored value alone. [] => explicitly clear.
               "failed": None if failed_conditions is None else _jsonb(failed_conditions),
               "summary": analysis_summary, "sver": strategy_version})

        # ANALYSIS_ADDED is informational; a re-analysis of the same signal
        # should not append a second identical event.
        _insert_event(s, signal_id, "ANALYSIS_ADDED", datetime.now(timezone.utc),
                      metadata={"outcome": outcome},
                      idempotency_key=make_idempotency_key(
                          signal_id, "ANALYSIS_ADDED", f"{strategy_version}|{outcome}"))

        row = s.execute(_sql("SELECT * FROM signal_postmortems WHERE signal_id=:id"),
                        {"id": str(signal_id)}).first()
        return _row_to_dict(row)

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def list_postmortems(*, outcome: Optional[str] = None,
                     strategy_version: Optional[str] = None,
                     limit: int = DEFAULT_PAGE_SIZE, offset: int = 0,
                     session=None) -> Dict[str, Any]:
    limit = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    offset = max(0, int(offset or 0))
    where, params = ["1=1"], {"limit": limit, "offset": offset}
    if outcome:
        where.append("p.outcome = :outcome")
        params["outcome"] = outcome
    if strategy_version:
        where.append("p.strategy_version = :sver")
        params["sver"] = strategy_version
    clause = " AND ".join(where)

    def _work(s):
        total = s.execute(_sql(
            f"SELECT count(*) FROM signal_postmortems p WHERE {clause}"), params).scalar() or 0
        rows = s.execute(_sql(f"""
            SELECT p.*, s.symbol, s.direction, s.timeframe
            FROM signal_postmortems p JOIN signals s ON s.id = p.signal_id
            WHERE {clause}
            ORDER BY p.created_at DESC LIMIT :limit OFFSET :offset
        """), params).all()
        items = [_row_to_dict(r) for r in rows]
        return {"items": items, "limit": limit, "offset": offset,
                "total": int(total), "has_more": offset + len(items) < int(total)}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def archive_signal(signal_id, *, archived_at=None, session=None) -> Dict[str, Any]:
    """
    Soft-archive a COMPLETED signal.

    Archiving hides a signal from the active board and the default history
    query while keeping every row for analysis. Nothing is deleted in phase 1.
    Refuses to archive a signal that is still working.
    """
    at_t = _utc(archived_at or datetime.now(timezone.utc), "archived_at")

    def _work(s):
        row = _lock_signal(s, signal_id)
        current = row._mapping["status"]
        if current not in TERMINAL_STATUSES:
            raise InvalidTransition(
                f"refusing to archive a signal in {current}; only completed signals are archived")
        if row._mapping["archived_at"] is not None:
            return {"signal": _row_to_dict(row), "applied": False, "duplicate": True}

        s.execute(_sql("UPDATE signals SET archived_at=:at, updated_at=now() "
                       "WHERE id=:sid AND archived_at IS NULL"),
                  {"at": at_t, "sid": str(signal_id)})
        _insert_event(s, signal_id, "ARCHIVED", at_t, metadata={"status": current},
                      idempotency_key=make_idempotency_key(signal_id, "ARCHIVED", current))
        return {"signal": _row_to_dict(_lock_signal(s, signal_id)),
                "applied": True, "duplicate": False}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


# ── Storage reporting ────────────────────────────────────────────────────────

def usage_report(*, session=None) -> Dict[str, Any]:
    """
    Approximate size and row counts, for the free-tier budget.

    Row counts come from pg_class.reltuples (an estimate maintained by
    autovacuum) rather than count(*), so this stays cheap as history grows.
    Review usage at 60-70% of the free allowance — see the README.
    """
    def _work(s):
        size = s.execute(_sql(
            "SELECT pg_database_size(current_database())")).scalar() or 0
        est = {r[0]: int(r[1]) for r in s.execute(_sql("""
            SELECT relname, GREATEST(reltuples, 0)::bigint
            FROM   pg_class
            WHERE  relname IN ('signals','signal_targets','signal_indicator_snapshots',
                               'signal_events','signal_postmortems')
              AND  relkind = 'r'
        """)).all()}
        counts = s.execute(_sql("""
            SELECT
              count(*)                                        AS total,
              count(*) FILTER (WHERE status IN ('OPEN','PARTIAL_TP')
                               AND archived_at IS NULL)       AS active,
              count(*) FILTER (WHERE archived_at IS NOT NULL) AS archived,
              min(generated_at)                               AS oldest,
              max(generated_at)                               AS newest
            FROM signals
        """)).first()
        c = dict(counts._mapping)
        # Per-environment split: on a shared database this is how you see how
        # much of the stored history was written by preview deploys rather than
        # by production.
        by_env = {}
        if has_environment_column(s):
            by_env = {r[0]: int(r[1]) for r in s.execute(_sql(
                "SELECT environment, count(*) FROM signals "
                "GROUP BY environment ORDER BY environment")).all()}
        return {
            "environment": deploy_context.environment(),
            "signals_by_environment": by_env,
            "database_size_bytes": int(size),
            "database_size_pretty": f"{int(size) / (1024*1024):.1f} MB",
            "estimated_row_counts": est,
            "signals_total": int(c["total"] or 0),
            "active_signals": int(c["active"] or 0),
            "archived_signals": int(c["archived"] or 0),
            "oldest_signal_at": c["oldest"].astimezone(timezone.utc).isoformat() if c["oldest"] else None,
            "newest_signal_at": c["newest"].astimezone(timezone.utc).isoformat() if c["newest"] else None,
            "review_guidance": ("Review retention when the database reaches 60-70% of the "
                                "Neon free-tier storage allowance. Automatic deletion is "
                                "not implemented in phase 1."),
        }

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)
