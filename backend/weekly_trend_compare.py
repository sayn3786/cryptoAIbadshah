"""
The weekly trend lean as an HL filter, on the live HL book.

The market lean study (730 daily closes) found the 1W trend flips — EMA 50,
Ichimoku TK, MACD — added up across coins hold in both halves: a bullish
lean was followed by +1.6% to +3.8% over the next 7 days, a bearish one by
-1.3% to -1.8%, and the effect already shows the next day. HL trades last a
day or two; this asks whether trading WITH that lean (or not against it)
lifts the live book.

The lean at each 4h publish is the one the 8 AM update showed that day (the
coins' reads at the last daily close). Variants move the strength HL compares
to the 69 floor:

  live                       no change
  against -5 / -10           a long while the weekly trend leans bearish (a
                             short while it leans bullish) loses 5 / 10
  against: skip              HL doesn't trade against it at all
  aligned +5                 a trade with the lean gains 5
  against -10, aligned +5

Then the live book: traded coins, every signal at 69+, v54 exits, $25 a
trade, HL caps. Run on both periods (--end-days-ago 0 and 135); adopt only
what wins both.

    python -m weekly_trend_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import factor_study as fs
import market_lean as ml
import portfolio_backtest as pbt
import read_study as rs
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
DAY_MS = rs.DAY_MS
FLOOR = 69.0
SKIP = -1000.0

VARIANTS = (
    ("live (no change)", 0.0, 0.0),
    ("against the weekly trend -5", -5.0, 0.0),
    ("against the weekly trend -10", -10.0, 0.0),
    ("against the weekly trend: skip", SKIP, 0.0),
    ("with the weekly trend +5", 0.0, 5.0),
    ("against -10, with +5", -10.0, 5.0),
)


def relation(direction: str, lean_word: str) -> Optional[str]:
    """"with" / "against" the lean, or None when it's mixed."""
    if lean_word not in ("bullish", "bearish"):
        return None
    return "with" if (lean_word == "bullish") == (direction == "LONG") else "against"


def adjust(cands: Sequence[Dict], leans: Dict[int, str], against: float,
           aligned: float) -> List[Dict]:
    """Candidates with strength (and the ranking's 1H/2H average) moved by
    their relation to the day's weekly trend lean. Pure."""
    out = []
    for c in cands:
        rel = relation(c["direction"], leans.get(c["slot_ms"] // DAY_MS * DAY_MS, "mixed"))
        delta = against if rel == "against" else aligned if rel == "with" else 0.0
        if delta:
            c = {**c, "strength": (c.get("strength") or 0) + delta,
                 "avg_tf_strength": (c.get("avg_tf_strength") or c.get("strength") or 0) + delta}
        out.append(c)
    return out


def daily_leans(timelines: Dict[str, Dict], days: Sequence[int]) -> Dict[int, str]:
    """{day start ms: the weekly trend lean the 8 AM update showed that day}."""
    out = {}
    for d in days:
        present = [s for s, tl in timelines.items() if tl and tl["closes"]
                   and tl["closes"][0] <= d]
        reads = [{**r, "symbol": s} for s in present for r in fs.reads_at(timelines[s], d)]
        out[d] = ml.weekly_trend_lean(reads, len(present))["lean"]
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
    timelines = {s: fs.read_timeline(daily[s], reads_fn, since_ms=start - 2 * DAY_MS)
                 for s in daily}
    day_list = sorted({c["slot_ms"] // DAY_MS * DAY_MS for c in cands})
    leans = daily_leans(timelines, day_list)
    base = {(c["symbol"], c["slot_ms"]) for c in uc.all_floor(cands, universe, floor)}
    rows = []
    for label, against, aligned in VARIANTS:
        recs = uc.all_floor(adjust(cands, leans, against, aligned), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "added": len(keys - base), "removed": len(base - keys),
                     **rr.usd_metrics(b["trades"], span)})
    at_floor = [c for c in cands if (c["symbol"], c["slot_ms"]) in base]
    rel = {"with": 0, "against": 0, "mixed": 0}
    for c in at_floor:
        r = relation(c["direction"], leans.get(c["slot_ms"] // DAY_MS * DAY_MS, "mixed"))
        rel[r or "mixed"] += 1
    lean_days = {w: sum(1 for v in leans.values() if v == w)
                 for w in ("bullish", "mixed", "bearish")}
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base), "relation": rel, "lean_days": lean_days,
            "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No weekly-trend filter beats live here (${live['pnl_usd']:+.2f}). Check the "
                "other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w, rel, ld = result["window"], result["relation"], result["lean_days"]
    lines = ["🧭 Weekly trend lean as an HL filter (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"Weekly trend (1W EMA 50 · Ichimoku · MACD flips) leaned bullish {ld['bullish']} "
             f"days, bearish {ld['bearish']}, mixed {ld['mixed']}",
             f"{result['at_floor']} signals at {result['floor']:g}+ today: {rel['with']} with the "
             f"lean, {rel['against']} against it, {rel['mixed']} on mixed days",
             "$25 a trade, HL caps", ""]
    for r in result["rows"]:
        moved = " / ".join(x for x in (f"+{r['added']}" if r["added"] else "",
                                       f"−{r['removed']}" if r["removed"] else "") if x)
        head = f"• {r['label']}" + (f" ({moved} signals)" if moved else "")
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
    ap = argparse.ArgumentParser(prog="weekly_trend_compare",
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
