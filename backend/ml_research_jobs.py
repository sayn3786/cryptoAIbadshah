"""Small synchronous cloud research jobs. No app import, signals or orders."""
from collections import Counter
from datetime import datetime, timezone

import requests
import ml_dataset as dataset
import db

SOURCES = ("okx", "binance", "bybit")
SYMBOLS = ("BTC", "ETH")


def fetch_candles(symbol, source):
    """One bounded request, no pagination, synthetic data or exchange fallback."""
    if symbol not in SYMBOLS or source not in SOURCES:
        raise ValueError("UNSUPPORTED_MARKET")
    if source == "okx":
        url, params = "https://www.okx.com/api/v5/market/candles", {"instId": symbol + "-USDT", "bar": "1H", "limit": 200}
    elif source == "binance":
        url, params = "https://api.binance.com/api/v3/klines", {"symbol": symbol + "USDT", "interval": "1h", "limit": 200}
    else:
        url, params = "https://api.bybit.com/v5/market/kline", {"category": "spot", "symbol": symbol + "USDT", "interval": "60", "limit": 200}
    response = requests.get(url, params=params, timeout=(3, 6), allow_redirects=False)
    response.raise_for_status()
    payload = response.json()
    if source == "okx":
        if payload.get("code") != "0":
            raise ValueError("PROVIDER_FAILED")
        rows = payload.get("data", [])
    elif source == "bybit":
        if payload.get("retCode") != 0:
            raise ValueError("PROVIDER_FAILED")
        rows = payload.get("result", {}).get("list", [])
    else:
        rows = payload
    if not isinstance(rows, list) or not rows or len(rows) > 300:
        raise ValueError("PROVIDER_FAILED")
    return [{"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]


def collect(symbols, source, environment, write=False):
    slot = datetime.now(timezone.utc)
    records, counts = [], Counter()
    for symbol in symbols:
        started = datetime.now(timezone.utc)
        try:
            candles = fetch_candles(symbol, source)
            actual_source = source
        except Exception:
            candles, actual_source = [], "fetch_failed"
        observed = datetime.now(timezone.utc)
        try:
            record = dataset.feature_snapshot(symbol, candles, actual_source, observed,
                environment, slot, fetch_started_at=started)
        except dataset.InvalidData:
            counts["SLOT_BOUNDARY_CROSSED"] += 1
            continue
        records.append(record)
        counts[record["quality"]] += 1
        if record["reason"]:
            counts[record["reason"]] += 1
    if write and records:
        with db.session_scope() as session:
            for record in records:
                counts["inserted"] += dataset.save_snapshot(record, session)
    return {"ok": counts["ready"] == len(symbols), "mode": "write" if write else "dry_run",
            "environment": environment, "source": source, "counts": dict(counts)}


def label(symbols, source, environment, write=False, limit=10):
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        records = dataset.pending_labels(session, environment, now, limit, symbols=symbols, source=source)
    counts, cache, results, failures = Counter(), {}, [], []
    for record in records:
        if dataset.milliseconds(now) - record["entry_at_ms"] >= 196 * dataset.HOUR_MS:
            failures.append((record["id"], "HISTORICAL_BACKFILL_REQUIRED", True))
            counts["HISTORICAL_BACKFILL_REQUIRED"] += 1
            continue
        symbol = record["symbol"]
        if symbol not in cache:
            try:
                cache[symbol] = (fetch_candles(symbol, source), datetime.now(timezone.utc))
            except Exception:
                cache[symbol] = (None, datetime.now(timezone.utc))
        candles, available = cache[symbol]
        try:
            if candles is None:
                raise dataset.InvalidData("FETCH_FAILED")
            result = dataset.label_snapshot(record, candles, source, available)
            results.append(result)
            counts["ready"] += 1
        except dataset.InvalidData as exc:
            failures.append((record["id"], str(exc), False))
            counts[str(exc)] += 1
    if write and (results or failures):
        with db.session_scope() as session:
            for result in results:
                counts["inserted"] += dataset.save_label(result, session)
            for identity, reason, permanent in failures:
                dataset.record_label_failure(session, identity, reason, datetime.now(timezone.utc), permanent=permanent)
    return {"ok": not failures, "mode": "write" if write else "dry_run",
            "environment": environment, "source": source, "attempted": len(records),
            "limit": limit, "counts": dict(counts)}
