#!/usr/bin/env python3
"""Explicit research collector/labeler. Never invoked by live signal requests.

Run with --help. Reads provider candles; DB writes require --write. No orders,
Telegram messages, migrations, or production signal updates are performed.
"""
import argparse
import ast
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import ml_dataset as dataset


def _universe():
    # app.py starts the live publication scheduler on import. Read only its
    # two literal configuration assignments without executing application code.
    # Fail loudly if these stop being literals; do not fall back to importing app.
    path = os.path.join(os.path.dirname(__file__), "..", "backend", "app.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    config = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("SCAN_SYMBOLS", "SYMBOLS"):
                    config[target.id] = ast.literal_eval(node.value)
    return config["SCAN_SYMBOLS"], config["SYMBOLS"]


def collect(args):
    SCAN_SYMBOLS, SYMBOLS = _universe()
    from binance import BinanceClient
    import db
    symbols = list(SCAN_SYMBOLS) if not args.symbols else args.symbols.split(",")
    if not symbols or len(set(symbols)) != len(symbols) or any(s not in SYMBOLS for s in symbols):
        raise ValueError("Use unique known symbols separated by commas")
    slot_at = datetime.now(timezone.utc)
    counts = Counter()
    for symbol in symbols:
        # Do not call build_analysis: price-only research has no need for the
        # paid sources or any of the live recommendation/auto-exec code paths.
        fetch_started = datetime.now(timezone.utc)
        try:
            candles, source = BinanceClient().get_spot_klines_sourced(SYMBOLS[symbol], "1h", 100)
        except Exception:
            candles, source = [], "fetch_failed"
        observed = datetime.now(timezone.utc)
        try:
            record = dataset.feature_snapshot(symbol, candles, source, observed, args.environment, slot_at,
                                              fetch_started_at=fetch_started)
        except dataset.InvalidData:
            counts["run_crossed_slot_boundary"] += 1
            break
        counts[record["quality"]] += 1
        if record["reason"]:
            counts[record["reason"]] += 1
        if args.write:
            with db.session_scope() as session:
                counts["inserted"] += dataset.save_snapshot(record, session)
    print(json.dumps({"mode": "write" if args.write else "dry_run", "counts": counts}))
    return 0 if counts["ready"] == len(symbols) else 2


def label(args):
    _, SYMBOLS = _universe()
    from binance import BinanceClient
    import db
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        records = dataset.pending_labels(session, args.environment, now, args.limit)
    counts, cache = Counter(), {}
    def failed(record, reason, permanent=False):
        counts[reason] += 1
        if args.write:
            with db.session_scope() as session:
                dataset.record_label_failure(session, record["id"], reason,
                                             datetime.now(timezone.utc), permanent=permanent)
    for record in records:
        symbol = record["symbol"]
        if dataset.milliseconds(now) - record["entry_at_ms"] >= 196 * dataset.HOUR_MS:
            failed(record, "HISTORICAL_BACKFILL_REQUIRED", permanent=True)
            continue
        if symbol not in SYMBOLS:
            failed(record, "UNKNOWN_SYMBOL", permanent=True)
            continue
        if symbol not in cache:
            try:
                candles, source = BinanceClient().get_spot_klines_sourced(SYMBOLS[symbol], "1h", 200)
                cache[symbol] = (candles, source, datetime.now(timezone.utc))
            except Exception:
                cache[symbol] = ([], "fetch_failed", datetime.now(timezone.utc))
        candles, source, available = cache[symbol]
        try:
            result = dataset.label_snapshot(record, candles, source, available)
        except dataset.InvalidData as exc:
            failed(record, str(exc))
            continue
        counts["ready"] += 1
        if args.write:
            with db.session_scope() as session:
                counts["inserted"] += dataset.save_label(result, session)
    print(json.dumps({"mode": "write" if args.write else "dry_run", "attempted": len(records),
                      "limit": args.limit, "counts": counts}))
    return 0 if counts["ready"] == len(records) else 2


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "label"))
    parser.add_argument("--environment", required=True, help="Explicit dataset namespace, e.g. research")
    parser.add_argument("--symbols", help="Collect subset, e.g. BTC,ETH; default is all SCAN_SYMBOLS")
    parser.add_argument("--limit", type=int, default=100, help="Maximum pending labels per run (1–500)")
    parser.add_argument("--write", action="store_true", help="Persist to configured DATABASE_URL; otherwise dry-run")
    args = parser.parse_args()
    if not args.environment.strip() or not 1 <= args.limit <= 500:
        parser.error("environment is required and limit must be 1–500")
    try:
        return collect(args) if args.command == "collect" else label(args)
    except Exception:
        # Never log a provider response or DB exception with a connection URL.
        print(json.dumps({"ok": False, "reason": "RESEARCH_JOB_FAILED_CHECK_DATABASE_AND_MIGRATIONS_013_014"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
