"""
Combined backtest: the best pieces of the cadence / stops / entry-exit studies,
together, with realistic Hyperliquid costs, split by signal strength.

What earlier runs found (125 days, HL book, v53):
  * the stop-to-entry move after TP1 costs about 0.11%/trade; keeping the
    original stop (or moving it only at 1R) brought the book to break-even;
  * a stop wider by 1 ATR helped a little;
  * before costs the strategy was roughly break-even or slightly positive, so
    costs matter.

Rows (each executed the way HL auto-exec does; see entry_exit_compare):
  1. today: 50/50, stop to entry at TP1, published stop
  2. 50/50, no break-even, published stop
  3. 50/50, stop to entry at 1R, published stop
  4. 50/50, no break-even, stop +1 ATR
  5. 50/50, stop to entry at 1R, stop +1 ATR
  6. the best of 2-5 with LIMIT take-profits (maker fee, no slippage)
  7. that one again, but 69+ signals only (the old auto-exec threshold)

Costs per leg (HL base tier; check the current schedule): market orders
4.5 bps taker + 2 bps slippage; limit take-profits 1.5 bps maker. Today HL
places take-profits as market-trigger orders, so rows 1-5 pay taker on them.

Every row is also split into strength bands (62-68 and 69+) inside the same
book, to show where the losses come from.

    python -m combined_compare --fetch-days 90
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt

HOUR_MS = cc.HOUR_MS
TAKER_BPS, SLIP_BPS, MAKER_BPS = 4.5, 2.0, 1.5
MARKET_TPS = {"entry": TAKER_BPS + SLIP_BPS, "tp": TAKER_BPS + SLIP_BPS,
              "stop": TAKER_BPS + SLIP_BPS, "market": TAKER_BPS + SLIP_BPS}
LIMIT_TPS = {**MARKET_TPS, "tp": MAKER_BPS}
BAND_SPLIT = 69.0

CANDIDATES = (
    ("50/50, no break-even", {"tp1_frac": 0.5, "be": None}, None),
    ("50/50, stop to entry at 1R", {"tp1_frac": 0.5, "be": 1.0}, None),
    ("50/50, no break-even, stop +1 ATR", {"tp1_frac": 0.5, "be": None}, {"atr_add": 1.0}),
    ("50/50, stop to entry at 1R, stop +1 ATR", {"tp1_frac": 0.5, "be": 1.0},
     {"atr_add": 1.0}),
)
TODAY = ("today: 50/50, stop to entry at TP1", {"tp1_frac": 0.5, "be": "tp1"}, None)

# The v54 candidate, FIXED after the 2026-05-26 → 09-28 run picked it. The
# out-of-sample check runs exactly this on a different period, never re-picking.
V54 = ("v54: 50/50, stop to entry at 1R, stop +1 ATR", {"tp1_frac": 0.5, "be": 1.0},
       {"atr_add": 1.0})


def bands(trades: Sequence[Dict], days: float, split: float = BAND_SPLIT) -> Dict:
    lo = [t for t in trades if (t.get("strength") or 0) < split]
    hi = [t for t in trades if (t.get("strength") or 0) >= split]
    return {f"<{split:g}": ee.metrics(lo, days), f"{split:g}+": ee.metrics(hi, days)}


def _published(market: Dict, *, days, correlations, production_universe, warmup_hours):
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {},
                     production_universe=production_universe, interval_hours=4,
                     start_ms=start, execute=False, keep_published=True,
                     keep_trades=False, reading_cache={})
    return rep["published"], start, end, span


def _row_fn(pub, market, span, min_strength):
    def row(label, exit_cfg, stop_variant, costs, *, floor=min_strength, group="main"):
        b = ee.run_book(pub, market, skip=lambda r: False,
                        exit_cfg={**exit_cfg, "costs": costs}, min_strength=floor,
                        fee_bps=TAKER_BPS, slippage_bps=SLIP_BPS,
                        stop_variant=stop_variant)
        return {"group": group, "label": label, **ee.metrics(b["trades"], span),
                "bands": bands(b["trades"], span), "counts": b["counts"],
                "_cfg": (exit_cfg, stop_variant)}
    return row


def compare(market: Dict, *, min_strength: float = 62, days: Optional[float] = None,
            correlations=None, production_universe=None, warmup_hours: int = 240) -> Dict:
    pub, start, end, span = _published(market, days=days, correlations=correlations,
                                       production_universe=production_universe,
                                       warmup_hours=warmup_hours)
    row = _row_fn(pub, market, span, min_strength)

    rows = [row(*TODAY, MARKET_TPS)]
    cand_rows = [row(label, cfg, sv, MARKET_TPS) for label, cfg, sv in CANDIDATES]
    rows += cand_rows
    best = max((r for r in cand_rows if r.get("trades")),
               key=lambda r: r["total_net_pct"], default=None)
    if best:
        cfg, sv = best["_cfg"]
        rows.append(row(f"{best['label']} + limit TPs", cfg, sv, LIMIT_TPS, group="costs"))
        rows.append(row(f"{best['label']} + limit TPs, 69+ only", cfg, sv, LIMIT_TPS,
                        floor=BAND_SPLIT, group="policy"))
    for r in rows:
        r.pop("_cfg", None)
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "settings": {"min_strength": min_strength, "taker_bps": TAKER_BPS,
                         "slippage_bps": SLIP_BPS, "maker_bps": MAKER_BPS, "cadence_h": 4,
                         "parity_mode": "price_only"},
            "published": len(pub), "rows": rows}


def compare_fixed(market: Dict, *, min_strength: float = 62, days: Optional[float] = None,
                  correlations=None, production_universe=None,
                  warmup_hours: int = 240) -> Dict:
    """Out-of-sample: today vs the FIXED v54 candidate (market and limit TPs).
    Nothing is selected on this data."""
    pub, start, end, span = _published(market, days=days, correlations=correlations,
                                       production_universe=production_universe,
                                       warmup_hours=warmup_hours)
    row = _row_fn(pub, market, span, min_strength)
    label, cfg, sv = V54
    rows = [row(*TODAY, MARKET_TPS, group="oos"),
            row(label, cfg, sv, MARKET_TPS, group="oos"),
            row(f"{label} + limit TPs", cfg, sv, LIMIT_TPS, group="oos")]
    for r in rows:
        r.pop("_cfg", None)
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "settings": {"min_strength": min_strength, "taker_bps": TAKER_BPS,
                         "slippage_bps": SLIP_BPS, "maker_bps": MAKER_BPS, "cadence_h": 4,
                         "parity_mode": "price_only"},
            "published": len(pub), "rows": rows, "fixed": True}


def verdict_fixed(result: Dict) -> str:
    rows = result["rows"]
    today, v54 = rows[0], rows[-1]
    if not v54.get("trades"):
        return "Not enough trades in this period to judge."
    per, pf = v54["avg_net_pct"], v54.get("profit_factor") or 0
    beat = (today.get("trades") and v54["total_net_pct"] > today["total_net_pct"])
    vs = (f" ({v54['total_net_pct'] - today['total_net_pct']:+.1f}% vs today)"
          if today.get("trades") else "")
    if per >= 0.05 and pf >= 1.05 and beat:
        return f"HOLDS out of sample: {per:+.3f}%/trade, PF {pf}{vs}. Worth a v54 testnet trial."
    if per > 0 and beat:
        return (f"Weaker out of sample: {per:+.3f}%/trade, PF {pf}{vs}. Still better than "
                "today, but the edge is thin.")
    if beat:
        return (f"Does NOT hold: {per:+.3f}%/trade, PF {pf}{vs}. Better than today but still "
                "losing: the in-sample edge was mostly fitted.")
    return f"Does NOT hold: {per:+.3f}%/trade, PF {pf}{vs}. Not better than today."


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows:
        return "Not enough trades to compare."
    today = rows[0]
    best = max(rows, key=lambda r: r["total_net_pct"])
    per = best["avg_net_pct"]
    pf = best.get("profit_factor") or 0
    gain = best["total_net_pct"] - today["total_net_pct"]
    head = f"Best: {best['label']} ({best['total_net_pct']:+.1f}% total, {gain:+.1f}% vs today)."
    if per >= 0.05 and pf >= 1.1:
        return head + " A real, if modest, edge: worth a v54 candidate on testnet."
    if per > 0:
        return head + " Positive but thin: not an edge yet."
    return head + " Still no edge."


def render_telegram(result: Dict) -> str:
    w, st = result["window"], result["settings"]
    lines = ["🧪 Combined backtest (v53 → v54 candidates, HL book, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"strength ≥ {st['min_strength']}, one position per coin, market entry, "
             f"taker {st['taker_bps']}+{st['slippage_bps']} bps, maker {st['maker_bps']} bps, "
             "price-only", ""]
    if result.get("fixed"):
        lines[0] = "🔬 Out-of-sample check (v54 candidate fixed in advance, HL book, every 4h)"
        lines.insert(3, "Earlier period, no overlap with the run that picked v54; nothing "
                        "re-picked on this data.")
    heads = {"main": "EXIT + STOP (market TPs, as today)",
             "costs": "BEST + LIMIT TAKE-PROFITS", "policy": "SAME, 69+ SIGNALS ONLY",
             "oos": "TODAY vs v54 (fixed)"}
    last = None
    for r in result["rows"]:
        if r["group"] != last:
            lines.append(heads[r["group"]])
            last = r["group"]
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        lines += [f"• {r['label']}",
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win {r['avg_win_pct']}% / loss {r['avg_loss_pct']}%",
                  f"  {r['avg_net_pct']}%/trade · total {r['total_net_pct']}% · "
                  f"PF {r['profit_factor']} · max DD {r['max_dd_pct']}%"]
        for name, b in (r.get("bands") or {}).items():
            if b.get("trades"):
                lines.append(f"  {name}: {b['trades']} trades · {b['avg_net_pct']}%/trade · "
                             f"total {b['total_net_pct']}% · PF {b['profit_factor']}")
        lines.append("")
    lines.append(verdict_fixed(result) if result.get("fixed") else verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="combined_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--candles", help="saved candles (JSON {symbol: {1H, 2H, 4H}})")
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--days", type=float, help="only the last N days")
    ap.add_argument("--min-strength", type=float, default=62)
    ap.add_argument("--end-days-ago", type=float, default=0,
                    help="end the downloaded history this many days ago (an earlier, "
                         "out-of-sample period)")
    ap.add_argument("--fixed", action="store_true",
                    help="out-of-sample mode: only today vs the fixed v54 candidate")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    corr, universe = appmod._BTC_CORR, list(appmod.SCAN_SYMBOLS)
    if args.candles:
        import portfolio_backtest_cli as cli
        market = cli.load_candles(args.candles)
    else:
        print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
        end_ms = None
        if args.end_days_ago:
            import time
            end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
                // HOUR_MS * HOUR_MS
        market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in universe
                                   if s in appmod.SYMBOLS}, args.fetch_days, end_ms=end_ms,
                                  log=lambda m: print(m, file=sys.stderr))
        if "BTC" not in market:
            print("error: no BTC history", file=sys.stderr)
            return 2
    run = compare_fixed if args.fixed else compare
    res = run(market, min_strength=args.min_strength, days=args.days,
              correlations=corr, production_universe=universe)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = all([weekly_report.send_private(p)
                        for p in ee.split_message(render_telegram(res))])
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
