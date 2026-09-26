"""Research-only, point-in-time candle features and future-return labels.

Never imported by the live signal engine. No fitted model or trading actions.
"""
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone, timedelta

HOUR_MS = 3_600_000
FEATURE_VERSION = "candles_1h_v2"
LABEL_VERSION = "next_open_4h_20bps_v1"
NEUTRAL_BPS = 20.0  # research definition, NOT an estimate of actual trading costs
SOURCES = frozenset({"binance", "okx", "bybit", "gateio", "kucoin", "mexc", "htx", "lbank"})


class InvalidData(ValueError):
    pass


def _number(value):
    if isinstance(value, bool):
        raise InvalidData("INVALID_NUMBER")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidData("INVALID_NUMBER") from None
    if not math.isfinite(number):
        raise InvalidData("NONFINITE_NUMBER")
    return number


def milliseconds(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise InvalidData("AWARE_TIMESTAMP_REQUIRED")
    return int(at.timestamp() * 1000)


def _candles(candles):
    """Reject duplicate/invalid bars rather than silently repair provider data."""
    result = []
    for candle in candles:
        timestamp = _number(candle.get("timestamp"))
        if timestamp != int(timestamp) or timestamp < 0 or int(timestamp) % HOUR_MS:
            raise InvalidData("INVALID_HOURLY_TIMESTAMP")
        row = {k: _number(candle.get(k)) for k in ("open", "high", "low", "close", "volume")}
        if min(row[k] for k in ("open", "high", "low", "close")) <= 0 or row["volume"] < 0:
            raise InvalidData("INVALID_OHLCV")
        if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise InvalidData("INVALID_OHLCV")
        row["timestamp"] = int(timestamp)
        result.append(row)
    result.sort(key=lambda c: c["timestamp"])
    if len({c["timestamp"] for c in result}) != len(result):
        raise InvalidData("DUPLICATE_CANDLES")
    return result


def _ema(values, span):
    value = values[0]
    alpha = 2 / (span + 1)
    for current in values[1:]:
        value += alpha * (current - value)
    return value


def feature_snapshot(symbol, candles, source, observed_at, environment, slot_at=None, *, fetch_started_at):
    """64 contiguous closed hourly bars; inputs beyond observation never enter features.

    All feature formulas belong to FEATURE_VERSION. The 14-bar RSI/ATR here use
    simple window averages, not the trading engine's smoothing conventions.
    """
    observed_ms = milliseconds(observed_at)
    cutoff_ms = milliseconds(fetch_started_at)
    if cutoff_ms > observed_ms:
        raise InvalidData("FETCH_CUTOFF_AFTER_OBSERVATION")
    slot_ms = milliseconds(slot_at or observed_at) // (4 * HOUR_MS) * (4 * HOUR_MS)
    if slot_ms > observed_ms or observed_ms >= slot_ms + 4 * HOUR_MS:
        raise InvalidData("OBSERVATION_OUTSIDE_SLOT")
    record = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ml:{environment}:{FEATURE_VERSION}:{slot_ms}:{symbol}")),
        "environment": environment, "symbol": symbol, "feature_version": FEATURE_VERSION,
        "slot_at": datetime.fromtimestamp(slot_ms / 1000, timezone.utc),
        "observed_at": observed_at, "fetch_started_at": fetch_started_at,
        "source": source if source in SOURCES else "unsupported",
        "entry_at_ms": (observed_ms // HOUR_MS + 1) * HOUR_MS,
        "features": {}, "quality": "invalid", "reason": None,
        "last_candle_close_ms": None, "input_hash": None,
    }
    try:
        if source == "fetch_failed":
            raise InvalidData("FETCH_FAILED")
        if source not in SOURCES:
            raise InvalidData("UNSUPPORTED_OR_SYNTHETIC_SOURCE")
        # Filter by timestamp first: a forming/future candle is not an input,
        # even if its OHLCV payload is malformed.
        closed = [c for c in candles if _number(c.get("timestamp")) + HOUR_MS <= cutoff_ms]
        rows = _candles(closed)[-64:]
        if len(rows) != 64:
            raise InvalidData("INSUFFICIENT_HISTORY")
        if any(b["timestamp"] - a["timestamp"] != HOUR_MS for a, b in zip(rows, rows[1:])):
            raise InvalidData("GAPPED_OR_WRONG_INTERVAL")
        close_ms = rows[-1]["timestamp"] + HOUR_MS
        if cutoff_ms - close_ms >= HOUR_MS or observed_ms - cutoff_ms >= HOUR_MS:
            raise InvalidData("STALE_CANDLES")
        prices = [c["close"] for c in rows]
        last = prices[-1]
        deltas = [b - a for a, b in zip(prices[-15:], prices[-14:])]
        gain = sum(max(d, 0) for d in deltas)
        loss = sum(max(-d, 0) for d in deltas)
        rsi = 50.0 if gain + loss == 0 else 100 * gain / (gain + loss)
        true_ranges = [max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
                       for p, c in zip(rows[-15:], rows[-14:])]
        volumes = [c["volume"] for c in rows]
        avg_volume = sum(volumes[-21:-1]) / 20
        features = {f"return_{n}h_pct": (last / prices[-n-1] - 1) * 100 for n in (1, 4, 12, 24)}
        features.update({
            "rsi14_simple": rsi,
            "ema20_distance_pct": (last / _ema(prices, 20) - 1) * 100,
            "ema50_distance_pct": (last / _ema(prices, 50) - 1) * 100,
            "macd_12_26_pct": (_ema(prices, 12) - _ema(prices, 26)) / last * 100,
            "atr14_simple_pct": sum(true_ranges) / 14 / last * 100,
            "relative_volume20": volumes[-1] / avg_volume if avg_volume > 0 else None,
            "range24_pct": (max(c["high"] for c in rows[-24:]) - min(c["low"] for c in rows[-24:])) / last * 100,
        })
        for value in features.values():
            if value is not None:
                _number(value)
        record.update(features=features, quality="ready", last_candle_close_ms=close_ms,
                      input_hash=hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())
    except (InvalidData, TypeError, AttributeError) as exc:
        record["reason"] = str(exc) if isinstance(exc, InvalidData) else "MALFORMED_CANDLES"
    return record


def label_snapshot(snapshot, candles, source, available_at):
    """Four complete hourly bars from the NEXT hourly open after observation.

    Raw spot return only. NOT TP-before-SL, execution P&L, or net return.
    Caller persists only successful labels; unavailable labels remain pending.
    """
    if snapshot["quality"] != "ready":
        raise InvalidData("FEATURES_NOT_READY")
    if source not in SOURCES or source != snapshot["source"]:
        raise InvalidData("SOURCE_MISMATCH")
    start = int(snapshot["entry_at_ms"])
    end = start + 4 * HOUR_MS
    now_ms = milliseconds(available_at)
    if now_ms < end:
        raise InvalidData("LABEL_NOT_MATURE")
    rows = _candles([c for c in candles if start <= _number(c.get("timestamp")) < end])
    if [r["timestamp"] for r in rows] != [start + i * HOUR_MS for i in range(4)]:
        raise InvalidData("MISSING_LABEL_CANDLES")
    entry, exit_price = rows[0]["open"], rows[-1]["close"]
    # Stable inclusive neutral boundaries despite binary floating-point noise.
    bps = round((exit_price / entry - 1) * 10000, 10)
    return {"snapshot_id": snapshot["id"], "label_version": LABEL_VERSION,
            "horizon_hours": 4, "neutral_bps": NEUTRAL_BPS,
            "entry_at_ms": start, "exit_at_ms": end, "available_at": available_at,
            "entry_price": entry, "exit_price": exit_price, "return_bps": bps,
            "direction": "UP" if bps > NEUTRAL_BPS else "DOWN" if bps < -NEUTRAL_BPS else "NEUTRAL",
            "source": source,
            "input_hash": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


def save_snapshot(record, session):
    from sqlalchemy import text
    params = {**record, "features": json.dumps(record["features"], allow_nan=False)}
    return session.execute(text("""
        INSERT INTO ml_feature_snapshots
        (id, environment, symbol, feature_version, slot_at, observed_at, fetch_started_at, source,
         entry_at_ms, features, quality, reason, last_candle_close_ms, input_hash)
        VALUES (:id, :environment, :symbol, :feature_version, :slot_at, :observed_at, :fetch_started_at,
                :source, :entry_at_ms, CAST(:features AS jsonb), :quality, :reason,
                :last_candle_close_ms, :input_hash)
        ON CONFLICT (environment, feature_version, slot_at, symbol) DO NOTHING
    """), params).rowcount


def save_label(record, session):
    from sqlalchemy import text
    return session.execute(text("""
        INSERT INTO ml_labels
        (snapshot_id, label_version, horizon_hours, neutral_bps, entry_at_ms,
         exit_at_ms, available_at, entry_price, exit_price, return_bps, direction, source, input_hash)
        VALUES (:snapshot_id, :label_version, :horizon_hours, :neutral_bps, :entry_at_ms,
                :exit_at_ms, :available_at, :entry_price, :exit_price, :return_bps, :direction, :source, :input_hash)
        ON CONFLICT (snapshot_id, label_version) DO NOTHING
    """), record).rowcount


def pending_labels(session, environment, now, limit, *, symbols=None, source=None):
    from sqlalchemy import text
    extra = ""
    params = {}
    if symbols is not None:
        if not symbols:
            return []
        names = []
        for i, symbol in enumerate(symbols):
            name = f"symbol_{i}"
            names.append(":" + name)
            params[name] = symbol
        extra += " AND f.symbol IN (" + ",".join(names) + ")"
    if source is not None:
        extra += " AND f.source = :source"
        params["source"] = source
    return session.execute(text("""
        SELECT f.* FROM ml_feature_snapshots f
        LEFT JOIN ml_label_jobs j ON j.snapshot_id = f.id AND j.label_version = :label_version
        WHERE f.environment = :environment AND f.feature_version = :version
          AND f.quality = 'ready' AND f.entry_at_ms + 14400000 <= :now_ms
          AND (j.snapshot_id IS NULL OR (j.status = 'retry' AND j.next_attempt_at <= :now))
          AND NOT EXISTS (SELECT 1 FROM ml_labels l WHERE l.snapshot_id = f.id
                          AND l.label_version = :label_version)
    """ + extra + " ORDER BY COALESCE(j.attempts, 0), f.observed_at, f.id LIMIT :limit"), {"environment": environment, "version": FEATURE_VERSION,
            "label_version": LABEL_VERSION, "now_ms": milliseconds(now), "now": now,
            "limit": limit, **params}).mappings().all()


def record_label_failure(session, snapshot_id, reason, now, *, permanent=False):
    """Three failed attempts maximum, then explicit operator backfill queue.

    Mutable job metadata is separate from immutable features and outcomes.
    Never substitutes another source, creates a neutral label, or deletes evidence.
    """
    from sqlalchemy import text
    session.execute(text("""
        INSERT INTO ml_label_jobs
          (snapshot_id, label_version, status, attempts, last_reason, last_attempt_at, next_attempt_at)
        VALUES (:id, :version, :status, 1, :reason, :now, :next)
        ON CONFLICT (snapshot_id, label_version) DO UPDATE SET
          attempts = ml_label_jobs.attempts + 1,
          status = CASE WHEN :permanent OR ml_label_jobs.attempts + 1 >= 3
                        THEN 'backfill_needed' ELSE 'retry' END,
          last_reason = :reason, last_attempt_at = :now,
          next_attempt_at = CASE WHEN :permanent OR ml_label_jobs.attempts + 1 >= 3
                                 THEN NULL ELSE :next END
    """), {"id": snapshot_id, "version": LABEL_VERSION,
            "status": "backfill_needed" if permanent else "retry", "permanent": permanent,
            "reason": reason, "now": now, "next": None if permanent else now + timedelta(hours=4)})
