"""
Publish cadence comparison: every 4H (production) vs every 2H vs every 1H.

Question it answers: would reading the 1H close and publishing/executing every
hour beat today's 4H cadence for the HL book?

Each cadence is replayed over the SAME window with the same strategy
(`portfolio_backtest.replay`, i.e. production's own screen, ranking and top-3
selection), then executed the way HL auto-exec does it:

  * only recs with strength >= --min-strength (HL_AUTO_MIN_STRENGTH, 62 now);
  * one position per coin: a rec is skipped while the coin already has a
    pending or open trade (HL's POSITION_EXISTS);
  * positions walked on 1H candles for every cadence, so a slot at an odd hour
    is managed from its first hour, and the cadences are judged alike.

Costs (fees + slippage per leg) are included in every R. Price-only: funding,
OI, news etc. are neutral, as in every offline replay.

    python -m cadence_compare --candles candles.json --days 30
    python -m cadence_compare --candles candles.json --json out.json

Reads only; writes nothing but the optional --json file.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional, Sequence

import portfolio_backtest as pbt

CADENCES = (4, 2, 1)
HOUR_MS = 3_600_000


# ── History download (longer than the app's live fetch, which stops ~40 days) ─

def _get(url: str, params: Dict, session=None):
    import requests
    r = (session or requests).get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _binance_1h(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    """Binance public market-data mirror (reachable from US runners)."""
    out, t = [], start_ms
    while t < end_ms:
        rows = _get("https://data-api.binance.vision/api/v3/klines",
                    {"symbol": pair, "interval": "1h", "startTime": t,
                     "endTime": end_ms, "limit": 1000}, session)
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in rows]
        t = int(rows[-1][0]) + HOUR_MS
        if len(rows) < 1000:
            break
    return out


def _okx_1h(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    inst = pair[:-4] + "-USDT" if pair.endswith("USDT") else pair
    out, after = [], end_ms
    for _ in range(200):
        d = _get("https://www.okx.com/api/v5/market/history-candles",
                 {"instId": inst, "bar": "1H", "after": str(after), "limit": 100}, session)
        rows = d.get("data") or []
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in rows]
        after = int(rows[-1][0])
        if after <= start_ms:
            break
    return out


def _gate_1h(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    cp = pair[:-4] + "_USDT" if pair.endswith("USDT") else pair
    out, t = [], start_ms // 1000
    end = end_ms // 1000
    while t < end:
        to = min(end, t + 999 * 3600)
        rows = _get("https://api.gateio.ws/api/v4/spot/candlesticks",
                    {"currency_pair": cp, "interval": "1h", "from": t, "to": to}, session)
        # [ts, quote_vol, close, high, low, open, base_vol, closed]
        out += [{"timestamp": int(k[0]) * 1000, "open": float(k[5]), "high": float(k[3]),
                 "low": float(k[4]), "close": float(k[2]), "volume": float(k[6])}
                for k in rows or []]
        t = to + 3600
    return out


def clean_1h(rows: Sequence[Dict], end_ms: int) -> List[Dict]:
    """Closed, de-duplicated, oldest-first 1H candles."""
    seen = {}
    for c in rows:
        ts = int(c["timestamp"])
        if ts % HOUR_MS == 0 and ts + HOUR_MS <= end_ms:
            seen[ts] = c
    return [seen[k] for k in sorted(seen)]


def aggregate(h1: Sequence[Dict], hours: int) -> List[Dict]:
    """UTC-aligned N-hour candles from 1H ones; incomplete buckets dropped
    (the same bars an exchange serves at that interval)."""
    span = hours * HOUR_MS
    buckets: Dict[int, List[Dict]] = {}
    for c in h1:
        buckets.setdefault(int(c["timestamp"]) // span * span, []).append(c)
    out = []
    for ts in sorted(buckets):
        b = buckets[ts]
        if len(b) != hours:
            continue
        out.append({"timestamp": ts, "open": b[0]["open"], "close": b[-1]["close"],
                    "high": max(x["high"] for x in b), "low": min(x["low"] for x in b),
                    "volume": sum(x["volume"] for x in b)})
    return out


def fetch_history(symbols: Dict[str, str], days: float, *, end_ms: Optional[int] = None,
                  session=None, log=print) -> Dict:
    """{symbol: {"1H", "2H", "4H"}} for `days` (+ indicator warm-up), from
    Binance, else OKX, else Gate. 2H/4H are built from the 1H bars. A symbol
    no source covers is left out and logged (the report names the subset)."""
    import time
    end_ms = end_ms or int(time.time() * 1000) // HOUR_MS * HOUR_MS
    start_ms = end_ms - int((days + 45) * 24 * HOUR_MS)     # + 4H lookback warm-up
    market = {}
    for sym, pair in symbols.items():
        got, src = [], None
        for name, fn in (("binance", _binance_1h), ("okx", _okx_1h), ("gate", _gate_1h)):
            try:
                rows = clean_1h(fn(pair, start_ms, end_ms, session), end_ms)
            except Exception:                            # noqa: BLE001
                rows = []
            if len(rows) > len(got):
                got, src = rows, name
            if got and int(got[0]["timestamp"]) <= start_ms + 24 * HOUR_MS:
                break                                    # full coverage
        if len(got) < 24 * 20:
            log(f"  {sym:<7} skipped (only {len(got)} 1H bars)")
            continue
        market[sym] = {"1H": got, "2H": aggregate(got, 2), "4H": aggregate(got, 4)}
        log(f"  {sym:<7} {src:<8} 1H:{len(got)}")
    return market


def max_concurrent(trades: Sequence[Dict]) -> int:
    """Most positions open at the same time (filled, not yet closed)."""
    ev = []
    for t in trades:
        if not t.get("filled_at"):
            continue
        end = t.get("closed_at") or float("inf")
        ev.append((t["filled_at"], 1))
        ev.append((end, -1))
    cur = best = 0
    for _, d in sorted(ev, key=lambda e: (e[0], e[1])):   # close before open at a tie
        cur += d
        best = max(best, cur)
    return best


def _r(v, n):
    return None if v is None else round(v, n)


def summarize(report: Dict, days: float) -> Dict:
    m, pop, ex = report["metrics"], report["population"], report["execution"]
    trades = report.get("trades") or []
    closed = m.get("trades", 0)
    return {
        "cadence_h": ex["interval_hours"],
        "slots": ex["slots"],
        "published": pop["recommendations_published"],
        "skipped_below_min": ex["skipped"]["below_min_strength"],
        "skipped_coin_busy": ex["skipped"]["symbol_busy"],
        "orders": len(trades),
        "filled": pop["orders_filled"],
        "closed_trades": closed,
        "trades_per_day": round(closed / days, 2) if days else None,
        "win_rate_pct": m.get("win_rate_pct"),
        "expectancy_R": _r(m.get("expectancy_R"), 3),
        "total_R": _r(m.get("total_R"), 2),
        "R_per_day": round((m.get("total_R") or 0) / days, 3) if days else None,
        "profit_factor": _r(m.get("profit_factor"), 2),
        "max_drawdown_R": _r(m.get("max_drawdown_R"), 2),
        "max_consecutive_losses": m.get("max_consecutive_losses"),
        "avg_hold_hours": _r(m.get("avg_hold_hours"), 1),
        "tp1_hit_rate_pct": m.get("tp1_hit_rate_pct"),
        "max_concurrent_positions": max_concurrent(trades),
        "open_at_end": pop["open_at_dataset_end"],
    }


def common_start(market: Dict, btc: str = "BTC", *, days: Optional[float] = None,
                 warmup_hours: int = 240) -> int:
    """First instant every cadence can be scored at: `warmup_hours` after the
    later of the 1H / 4H histories begins (240 = a full 1H lookback), or `days`
    before the last 1H close, whichever is later."""
    h1 = market[btc]["1H"]
    h4 = market[btc]["4H"]
    first = max(int(h1[0]["timestamp"]), int(h4[0]["timestamp"])) + warmup_hours * HOUR_MS
    last = int(h1[-1]["timestamp"]) + HOUR_MS
    if days:
        first = max(first, last - int(days * 24 * HOUR_MS))
    # align to a 4H boundary so the 4H cadence isn't handicapped by the cut
    first += (-first) % (4 * HOUR_MS)
    return first


def compare(market: Dict, *, cadences=CADENCES, min_strength: Optional[float] = 62,
            one_per_symbol: bool = True, days: Optional[float] = None,
            correlations=None, fee_bps=pbt.DEFAULT_FEE_BPS,
            slippage_bps=pbt.DEFAULT_SLIPPAGE_BPS, production_universe=None,
            warmup_hours: int = 240) -> Dict:
    start = common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span_days = (end - start) / (24 * HOUR_MS)
    rows, reports = [], {}
    cache: Dict = {}                   # readings shared across cadences
    for h in sorted(cadences):         # 1H first: it computes every reading
        rep = pbt.replay(market, correlations=correlations or {},
                         reading_cache=cache,
                         production_universe=production_universe,
                         fee_bps=fee_bps, slippage_bps=slippage_bps,
                         interval_hours=h, exec_tf="1H", min_strength=min_strength,
                         one_per_symbol=one_per_symbol, start_ms=start,
                         keep_trades=True)
        reports[h] = rep
        rows.append(summarize(rep, span_days))
    rows.sort(key=lambda r: -r["cadence_h"])      # 4h, 2h, 1h
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end),
                       "days": round(span_days, 1)},
            "settings": {"min_strength": min_strength, "one_per_symbol": one_per_symbol,
                         "exec_tf": "1H", "fee_bps": fee_bps,
                         "slippage_bps": slippage_bps, "parity_mode": "price_only"},
            "rows": rows, "reports": reports}


COLUMNS = (("cadence_h", "every"), ("slots", "slots"), ("published", "published"),
           ("skipped_below_min", "<min"), ("skipped_coin_busy", "busy"),
           ("closed_trades", "trades"), ("trades_per_day", "/day"),
           ("win_rate_pct", "win%"), ("expectancy_R", "exp R"),
           ("total_R", "total R"), ("R_per_day", "R/day"),
           ("profit_factor", "PF"), ("max_drawdown_R", "maxDD R"),
           ("max_consecutive_losses", "loss run"), ("avg_hold_hours", "hold h"),
           ("max_concurrent_positions", "max open"))


def render(result: Dict) -> str:
    w = result["window"]
    lines = [f"Window {w['start'][:16]} → {w['end'][:16]} UTC ({w['days']} days), "
             f"min strength {result['settings']['min_strength']}, "
             f"one position per coin: {result['settings']['one_per_symbol']}", ""]
    head = [label for _, label in COLUMNS]
    body = [[("%sh" % r[k]) if k == "cadence_h" else ("—" if r.get(k) is None else str(r[k]))
             for k, _ in COLUMNS] for r in result["rows"]]
    widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(head)]
    lines.append("  ".join(h.rjust(widths[i]) for i, h in enumerate(head)))
    for b in body:
        lines.append("  ".join(v.rjust(widths[i]) for i, v in enumerate(b)))
    return "\n".join(lines)


def verdict(rows: Sequence[Dict]) -> str:
    """One plain sentence: does a faster cadence beat 4H on edge AND on risk?"""
    by = {r["cadence_h"]: r for r in rows}
    base = by.get(4)
    if not base or not base.get("closed_trades"):
        return "Not enough 4H trades in this window to compare."
    out = []
    if (base["R_per_day"] or 0) <= 0:
        out.append("4h (today) is not profitable in this window.")
    for h in sorted(by):
        if h == 4:
            continue
        r = by[h]
        if not r.get("closed_trades"):
            out.append(f"{h}h: no trades.")
            continue
        better_edge = (r["expectancy_R"] or 0) >= (base["expectancy_R"] or 0)
        more_r = (r["R_per_day"] or 0) > (base["R_per_day"] or 0)
        worse_dd = (r["max_drawdown_R"] or 0) > (base["max_drawdown_R"] or 0) * 1.25
        if (r["R_per_day"] or 0) <= 0:
            out.append(f"{h}h is not profitable in this window"
                       + (" (it loses less than 4h)." if more_r else "."))
        elif more_r and better_edge and not worse_dd:
            out.append(f"{h}h beats 4h: more R per day with equal or better edge per trade.")
        elif more_r and not better_edge:
            out.append(f"{h}h makes more R per day only by taking more, weaker trades "
                       f"(edge {r['expectancy_R']:+.2f}R vs {base['expectancy_R']:+.2f}R).")
        elif more_r:
            out.append(f"{h}h makes more R per day but with a much deeper drawdown.")
        else:
            out.append(f"{h}h does not beat 4h.")
    return " ".join(out)


def render_telegram(result: Dict) -> str:
    """Phone-width version: one block per cadence."""
    w, st = result["window"], result["settings"]
    lines = ["📊 Cadence backtest (HL book)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"strength ≥ {st['min_strength']}, one position per coin, "
             f"fees {st['fee_bps']}+{st['slippage_bps']} bps/leg, price-only", ""]
    for r in result["rows"]:
        lines += [f"Every {r['cadence_h']}h",
                  f"  trades {r['closed_trades']} ({r['trades_per_day']}/day) · "
                  f"win {r['win_rate_pct']}% · TP1 {r['tp1_hit_rate_pct']}%",
                  f"  edge {r['expectancy_R']}R/trade · total {r['total_R']}R · "
                  f"{r['R_per_day']}R/day · PF {r['profit_factor']}",
                  f"  max DD {r['max_drawdown_R']}R · loss run {r['max_consecutive_losses']} · "
                  f"max open {r['max_concurrent_positions']} · skipped busy {r['skipped_coin_busy']}",
                  ""]
    lines.append(verdict(result["rows"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cadence_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--candles",
                    help="saved candles (JSON {symbol: {1H, 2H, 4H}}); else fetched")
    ap.add_argument("--fetch-days", type=float, default=90,
                    help="days of history to download when --candles is not given")
    ap.add_argument("--save-candles", help="write the downloaded candles here")
    ap.add_argument("--telegram", action="store_true",
                    help="send the summary to TELEGRAM_REPORT_CHAT_ID (private) "
                         "and print only whether it was sent")
    ap.add_argument("--days", type=float, help="only the last N days")
    ap.add_argument("--min-strength", type=float, default=62)
    ap.add_argument("--all-published", action="store_true",
                    help="every published rec, no strength filter, no one-per-coin")
    ap.add_argument("--fee-bps", type=float, default=pbt.DEFAULT_FEE_BPS)
    ap.add_argument("--slippage-bps", type=float, default=pbt.DEFAULT_SLIPPAGE_BPS)
    ap.add_argument("--json", help="also write rows + window here")
    args = ap.parse_args(argv)

    import app as appmod
    corr, universe = appmod._BTC_CORR, list(appmod.SCAN_SYMBOLS)
    if args.candles:
        import portfolio_backtest_cli as cli
        market = cli.load_candles(args.candles)
    else:
        print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
        market = fetch_history({s: appmod.SYMBOLS[s] for s in universe if s in appmod.SYMBOLS},
                               args.fetch_days,
                               log=lambda m: print(m, file=sys.stderr))
        if "BTC" not in market:
            print("error: no BTC history", file=sys.stderr)
            return 2
        if args.save_candles:
            with open(args.save_candles, "w", encoding="utf-8") as fh:
                json.dump(market, fh)
    res = compare(market, min_strength=None if args.all_published else args.min_strength,
                  one_per_symbol=not args.all_published, days=args.days,
                  correlations=corr, fee_bps=args.fee_bps,
                  slippage_bps=args.slippage_bps, production_universe=universe)
    if args.telegram:
        # The repo is public, so its Actions logs are too: never print results
        # there. They go to the owner's private chat only.
        import weekly_report
        try:
            sent = weekly_report.send_private(render_telegram(res))
        except Exception as exc:                         # noqa: BLE001
            # Never print the exception: a requests error carries the URL,
            # and the URL carries the bot token.
            sent = False
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render(res))
    print()
    print(verdict(res["rows"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in res.items() if k != "reports"}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
