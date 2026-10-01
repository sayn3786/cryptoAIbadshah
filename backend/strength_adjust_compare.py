"""
Strength changes from the trade-outcome factor study, on the live HL book.

The factor study (2,469 trades, 2025-12 → 2026-09) found three factors that
held in BOTH halves:
  * a 1D bottom/top read WITH the trade (e.g. a long while 1D shows an
    oversold bottom / bullish divergence): +0.86%/trade, win 62-69% vs 53-55%;
  * 2H exhausted (a long into 2H overbought, a short into oversold): +0.39%
    — the engine docks it in the ranking quality score today;
  * a fresh 1D trend flip WITH the trade (chasing): -0.40%/trade.

Each becomes a STRENGTH adjustment, which decides which signals clear the
auto-exec floor (69) and which take the caps first. The live setup is
replayed — traded coins, every signal at the floor, v54 exits (stop +1 ATR,
stop to entry at 1R, limit TPs), $25 a trade, HL caps — with the reads the
Daily Market Update showed at the last daily close:

  live                              no adjustment
  bottom/top with the trade +5 / +10
  fresh 1D trend flip with -5 / -10
  2H exhausted +5
  bottom/top +10 and trend flip -5
  all three (+10 / -5 / +5)

Run on both periods (--end-days-ago 0 and 135); adopt only what helps in both.

    python -m strength_adjust_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import factor_study as fs
import portfolio_backtest as pbt
import read_study as rs
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
DAY_MS = rs.DAY_MS
FLOOR = 69.0

BOTTOM = "1D bottom/top read with the trade (counter-trend)"
CHASE = "1D trend flip with the trade"
EXHAUSTED = "2H exhausted"

VARIANTS = (
    ("live (no adjustment)", {}),
    ("bottom/top with the trade +5", {BOTTOM: 5}),
    ("bottom/top with the trade +10", {BOTTOM: 10}),
    ("fresh 1D trend flip with -5", {CHASE: -5}),
    ("fresh 1D trend flip with -10", {CHASE: -10}),
    ("2H exhausted +5", {EXHAUSTED: 5}),
    ("bottom/top +10 and trend flip -5", {BOTTOM: 10, CHASE: -5}),
    ("all three (+10 / -5 / +5)", {BOTTOM: 10, CHASE: -5, EXHAUSTED: 5}),
)


def adjust(cands: Sequence[Dict], adj: Dict[str, float]) -> List[Dict]:
    """Candidates with strength (and the ranking's 1H/2H average) moved by
    the adjustments whose factor is present. Pure."""
    out = []
    for c in cands:
        delta = sum(v for k, v in adj.items() if c["factors"].get(k))
        if delta:
            c = {**c, "strength": (c.get("strength") or 0) + delta,
                 "avg_tf_strength": (c.get("avg_tf_strength") or c.get("strength") or 0) + delta}
        out.append(c)
    return out


def compare(market: Dict, daily: Dict[str, Sequence[Dict]], reads_fn, *,
            core: Sequence[str], days: Optional[float] = None, correlations=None,
            floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    timelines = {s: fs.read_timeline(daily[s], reads_fn, since_ms=start - DAY_MS)
                 for s in {c["symbol"] for c in cands} if s in daily}
    tagged = [{**c, "factors": fs.factors(c, fs.reads_at(timelines.get(c["symbol"]),
                                                          c["slot_ms"]))} for c in cands]
    base_set = {(c["symbol"], c["slot_ms"]) for c in uc.all_floor(tagged, universe, floor)}
    rows = []
    for label, adj in VARIANTS:
        recs = uc.all_floor(adjust(tagged, adj), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "added": len(keys - base_set),
                     "removed": len(base_set - keys), **rr.usd_metrics(b["trades"], span)})
    flagged = {k: sum(1 for c in tagged if c["factors"].get(k) and
                      (c.get("strength") or 0) >= floor - 10)
               for k in (BOTTOM, CHASE, EXHAUSTED)}
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "candidates": len(tagged), "at_floor": len(base_set),
            "flagged_near_floor": flagged, "coverage": len(timelines), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows:
        return "Not enough trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No adjustment beats live here (${live['pnl_usd']:+.2f}). Check the other "
                "period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w, fl = result["window"], result["flagged_near_floor"]
    lines = ["🎚️ Strength adjustments on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps; {result['at_floor']} signals at the floor today of "
             f"{result['candidates']} candidates",
             f"Near the floor (59+): bottom/top with {fl[BOTTOM]} · fresh trend flip with "
             f"{fl[CHASE]} · 2H exhausted {fl[EXHAUSTED]}", ""]
    for r in result["rows"]:
        moved = []
        if r["added"]:
            moved.append(f"+{r['added']}")
        if r["removed"]:
            moved.append(f"−{r['removed']}")
        head = f"• {r['label']}" + (f" ({' / '.join(moved)} signals)" if moved else "")
        if not r.get("trades"):
            lines += [f"{head}: no trades", ""]
            continue
        lines += [head,
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="strength_adjust_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * DAY_MS)) // DAY_MS * DAY_MS
    pairs = {s: appmod.SYMBOLS[s] for s in core}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history(pairs, args.fetch_days, end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    print("downloading daily history for the reads...", file=sys.stderr)
    daily = rs.fetch_daily(pairs, fs.READ_HISTORY_DAYS + int(args.fetch_days) + 60,
                           end_ms=end_ms, log=log)
    res = compare(market, daily, appmod._daily_reads_for, core=core,
                  correlations=appmod._BTC_CORR, floor=args.min_strength)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
