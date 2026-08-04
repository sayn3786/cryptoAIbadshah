"""
Full-universe walk-forward validation, offline.

The HTTP endpoint cannot do this. A complete replay is three analyses per symbol
per slot — thirty-one symbols over a hundred slots is nearly ten thousand
`generate_signal` calls — and it runs inside a 60-second function ceiling that
cannot be raised on this plan. So the endpoint is capped, and a capped run is
labelled `subset_price_only`, because the top three out of five alts is not the
top three production would have published out of thirty.

This is where the real number comes from.

    python -m portfolio_backtest_cli --symbols production --slots 100 \\
        --limit 1000 --output report.json

    # download once, replay many times
    python -m portfolio_backtest_cli --symbols production --limit 1000 \\
        --save-candles candles.json
    python -m portfolio_backtest_cli --candles candles.json --slots 200 \\
        --output report.json

FAILS CLOSED. If any required symbol or timeframe is missing history, it stops
and says which. Dropping a symbol and carrying on would produce a report that
looks like full-universe parity and is not — and the symbol most likely to fail
a fetch is a thin one, which is exactly the kind that would have ranked
differently. Use --allow-missing to run anyway; the report is then labelled a
subset, because that is what it is.

Reads only. No database, no publication, no cache writes. It imports app for the
symbol list and the market client, which starts app's scheduler thread — that
thread only pre-warms an in-process cache and writes nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portfolio_backtest as pbt                                    # noqa: E402

TIMEFRAMES = ("1H", "2H", "4H")

# Enough closed bars for the indicators to seed before the first slot is scored.
MIN_BARS = {"1H": 200, "2H": 120, "4H": 80}


class MissingHistory(RuntimeError):
    """A required symbol/timeframe could not be filled. Never swallowed."""


def load_candles(path: str) -> Dict[str, Dict[str, List[Dict]]]:
    try:
        with open(path, encoding="utf-8") as fh:
            market = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingHistory(f"cannot read saved candles ({exc})") from exc
    if not isinstance(market, dict):
        raise MissingHistory("saved candles must be a symbol-keyed JSON object")
    return market


def validate_market_history(market: Dict, symbols,
                            minimums: Optional[Dict[str, int]] = None) -> List[str]:
    """Return every cached-history problem; an empty list means usable input."""
    minimums = minimums or MIN_BARS
    problems: List[str] = []
    for sym in symbols:
        tfs = market.get(sym)
        if not isinstance(tfs, dict):
            problems.append(f"{sym}: symbol history missing or not an object")
            continue
        for tf in TIMEFRAMES:
            candles = tfs.get(tf)
            if not isinstance(candles, list):
                problems.append(f"{sym} {tf}: timeframe missing or not an array")
                continue
            need = int(minimums.get(tf, 0))
            if len(candles) < need:
                problems.append(f"{sym} {tf}: {len(candles)} bars, need {need}")
            previous_ts = None
            for index, candle in enumerate(candles):
                prefix = f"{sym} {tf} candle {index}"
                if not isinstance(candle, dict):
                    problems.append(f"{prefix}: not an object")
                    break
                values = {}
                invalid = None
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    try:
                        value = float(candle[field])
                    except (KeyError, TypeError, ValueError):
                        invalid = f"{prefix}: invalid {field}"
                        break
                    if not math.isfinite(value):
                        invalid = f"{prefix}: non-finite {field}"
                        break
                    values[field] = value
                if invalid:
                    problems.append(invalid)
                    break
                ts = int(values["timestamp"])
                span = pbt.TF_MS[tf]
                if values["timestamp"] != ts or ts % span:
                    problems.append(f"{prefix}: timestamp is not {tf}-aligned")
                    break
                if previous_ts is not None and ts - previous_ts != span:
                    problems.append(f"{prefix}: timestamp cadence is not {tf}")
                    break
                previous_ts = ts
                if (values["open"] <= 0 or values["high"] <= 0
                        or values["low"] <= 0 or values["close"] <= 0
                        or values["volume"] < 0
                        or values["high"] < max(values["open"], values["close"])
                        or values["low"] > min(values["open"], values["close"])
                        or values["high"] < values["low"]):
                    problems.append(f"{prefix}: invalid OHLCV range")
                    break
    return problems


def fetch_candles(symbols, limit: int, *, verbose: bool = True) -> Dict:
    """
    Pull closed candles for every symbol and timeframe. Raises on a hole.

    Imported lazily so `--candles` runs need no network, no API keys and no
    Flask app at all.
    """
    import app as appmod

    market: Dict[str, Dict[str, List[Dict]]] = {}
    problems: List[str] = []
    for sym in symbols:
        pair = appmod.SYMBOLS.get(sym)
        if not pair:
            problems.append(f"{sym}: not a browsable symbol")
            continue
        tfs: Dict[str, List[Dict]] = {}
        for tf in TIMEFRAMES:
            try:
                raw = appmod.client.get_spot_klines(
                    pair, appmod.TF_INTERVAL.get(tf, "2h"), limit)
                if tf in appmod.TF_AGG:
                    raw = appmod.client.aggregate_candles(raw, appmod.TF_AGG[tf])
                closed, _live = appmod._split_closed(
                    raw, appmod.TF_SECONDS.get(tf, 7200))
            except Exception as exc:                    # noqa: BLE001
                problems.append(f"{sym} {tf}: fetch failed ({exc})")
                closed = []
            if appmod.client.data_source == "demo":
                problems.append(f"{sym} {tf}: demo data, not real history")
                closed = []
            if len(closed) < MIN_BARS[tf]:
                problems.append(f"{sym} {tf}: {len(closed)} bars, need "
                                f"{MIN_BARS[tf]}")
            tfs[tf] = closed
        market[sym] = tfs
        if verbose:
            print(f"  {sym:<8} " + "  ".join(
                f"{tf}:{len(tfs[tf])}" for tf in TIMEFRAMES), file=sys.stderr)
    if problems:
        raise MissingHistory("; ".join(problems))
    return market


def load_market_caps(path: Optional[str]) -> Optional[Dict]:
    """
    Timestamped market-cap history: ``{symbol: [{available_at, market_cap}]}``.

    Deliberately file-only. There is no free API for a token's market cap as of
    a date last March, and inventing one by multiplying today's supply by a
    historical price would be a guess wearing a measurement's clothes.
    """
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="portfolio_backtest_cli",
        description="Full-universe walk-forward replay of the published strategy")
    ap.add_argument("--symbols", default="production",
                    help="'production' for the exact SCAN_SYMBOLS universe, or "
                         "a comma-separated list (labelled a subset)")
    ap.add_argument("--slots", type=int, default=100,
                    help="publication slots to replay, most recent first")
    ap.add_argument("--limit", type=int, default=1000,
                    help="candles to fetch per timeframe")
    ap.add_argument("--candles", help="replay from a saved candle file instead "
                                      "of fetching")
    ap.add_argument("--save-candles", help="write fetched candles here and exit")
    ap.add_argument("--market-caps", help="timestamped market-cap history JSON")
    ap.add_argument("--output", help="write the report here (default: stdout)")
    ap.add_argument("--fee-bps", type=float, default=pbt.DEFAULT_FEE_BPS)
    ap.add_argument("--slippage-bps", type=float, default=pbt.DEFAULT_SLIPPAGE_BPS)
    ap.add_argument("--trades", action="store_true",
                    help="include the per-trade ledger")
    ap.add_argument("--allow-missing", action="store_true",
                    help="continue when history is incomplete; the report is "
                         "then labelled a subset")
    args = ap.parse_args(argv)

    # The production universe, imported here so --candles runs stay offline-ish.
    import app as appmod
    production_universe = list(appmod.SCAN_SYMBOLS)

    if args.symbols == "production":
        wanted = list(production_universe)
    else:
        wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if "BTC" not in wanted:
            # Every candidate's strength is adjusted against BTC's own 2H
            # reading. A run without it measures a different policy.
            wanted = ["BTC"] + wanted

    if args.candles:
        try:
            market = load_candles(args.candles)
        except MissingHistory as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        problems = validate_market_history(market, wanted)
        if problems and not args.allow_missing:
            print("error: incomplete saved candle history, refusing to claim "
                  "full universe parity:\n  " + "; ".join(problems),
                  file=sys.stderr)
            print("re-run with --allow-missing to produce a subset report",
                  file=sys.stderr)
            return 2
        if problems:
            print("warning: incomplete saved history, continuing as a SUBSET:\n  "
                  + "; ".join(problems), file=sys.stderr)
            wanted = [s for s in wanted
                      if not validate_market_history(market, [s])]
            market = {s: market[s] for s in wanted}
    else:
        print(f"fetching {len(wanted)} symbols x {len(TIMEFRAMES)} timeframes "
              f"({args.limit} bars)...", file=sys.stderr)
        try:
            market = fetch_candles(wanted, args.limit)
        except MissingHistory as exc:
            if not args.allow_missing:
                print(f"error: incomplete history, refusing to claim full "
                      f"universe parity:\n  {exc}", file=sys.stderr)
                print("re-run with --allow-missing to produce a subset report",
                      file=sys.stderr)
                return 2
            print(f"warning: incomplete history, continuing as a SUBSET:\n  {exc}",
                  file=sys.stderr)
            market = _tolerant_fetch(wanted, args.limit)
            wanted = [s for s in wanted if market.get(s)]

    if args.save_candles:
        with open(args.save_candles, "w", encoding="utf-8") as fh:
            json.dump(market, fh)
        print(f"wrote {args.save_candles}", file=sys.stderr)
        return 0

    report = pbt.replay(
        market,
        symbols=wanted,
        correlations=appmod._BTC_CORR,
        production_universe=production_universe,
        market_cap_history=load_market_caps(args.market_caps),
        strategy_version=_strategy_version(),
        parity_mode="price_only",
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        max_slots=args.slots,
        keep_trades=args.trades,
    )

    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.output} — result_kind={report['result_kind']}",
              file=sys.stderr)
    else:
        print(text)
    return 0


def _tolerant_fetch(symbols, limit) -> Dict:
    """--allow-missing only. Keeps what arrived; the report says it is a subset."""
    import app as appmod

    market: Dict[str, Dict[str, List[Dict]]] = {}
    for sym in symbols:
        pair = appmod.SYMBOLS.get(sym)
        if not pair:
            continue
        tfs = {}
        ok = True
        for tf in TIMEFRAMES:
            try:
                raw = appmod.client.get_spot_klines(
                    pair, appmod.TF_INTERVAL.get(tf, "2h"), limit)
                if tf in appmod.TF_AGG:
                    raw = appmod.client.aggregate_candles(raw, appmod.TF_AGG[tf])
                closed, _live = appmod._split_closed(
                    raw, appmod.TF_SECONDS.get(tf, 7200))
            except Exception:                            # noqa: BLE001
                closed = []
            if len(closed) < MIN_BARS[tf]:
                ok = False
            tfs[tf] = closed
        if ok:
            market[sym] = tfs
    return market


def _strategy_version() -> Optional[str]:
    try:
        import signal_publish
        return signal_publish.STRATEGY_VERSION
    except Exception:                                    # noqa: BLE001
        return None


if __name__ == "__main__":
    raise SystemExit(main())
