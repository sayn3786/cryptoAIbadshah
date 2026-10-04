"""
Hourly HL top-up: the live engine checked every hour, extras opened on HL.

Today the 1H+2H engine scores coins only at the 4h publish, so a coin that
publishes at 51 and reads 80 two hours later is never traded. Proposed: keep
the channel on 4h, but score every hour with the SAME engine and let HL open a
coin that clears the bar between publishes.

Replayed the way HL trades today: production's screen (R:R floor, chase cap,
stale price), market entry with the stale guard, v54 exits (stop +1 ATR, stop
to entry at 1R, limit TPs), $25 a trade, HL caps (one position per coin, 3 per
slot, exposure cap), every 4h signal at the 69 floor:

  live only                       the 4h publishes
  + hourly extras at 69           any off-slot hour, same floor
  + hourly extras at 75           a stricter bar between publishes
  + hourly extras, jump >= 15     69+ AND at least 15 above the coin's reading
                                  (same direction) at the last publish

Run on both periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m hourly_topup_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
SLOT_MS = 4 * HOUR_MS
FLOOR = 69.0
VARIANTS = (
    ("live only (4h)", None),
    ("+ hourly extras at 69", {"floor": 69.0, "jump": 0.0}),
    ("+ hourly extras at 75", {"floor": 75.0, "jump": 0.0}),
    ("+ hourly extras, jump >= 15", {"floor": 69.0, "jump": 15.0}),
)


def on_slot(c: Dict) -> bool:
    return c["slot_ms"] % SLOT_MS == 0


def topup_recs(cands: Sequence[Dict], universe: Sequence[str], *, floor: float = FLOOR,
               extra: Optional[Dict] = None) -> List[Dict]:
    """The live 4h recs plus, when `extra` is given, the off-slot hourly
    candidates at extra["floor"]+ that rose at least extra["jump"] above the
    coin's same-direction reading at the last publish (no reading = a jump).
    Pure; each rec carries "extra": bool."""
    live = [{**c, "extra": False} for c in uc.all_floor([c for c in cands if on_slot(c)],
                                                           universe, floor)]
    if not extra:
        return live
    at_publish = {(c["symbol"], c["slot_ms"]): c for c in cands if on_slot(c)}

    def jumped(c):
        prev = at_publish.get((c["symbol"], c["slot_ms"] // SLOT_MS * SLOT_MS))
        base = (prev.get("strength") or 0) if prev and prev.get("direction") == c.get("direction") else 0
        return (c.get("strength") or 0) - base >= extra["jump"]

    off = [c for c in cands if not on_slot(c)]
    extras = [{**c, "extra": True} for c in uc.all_floor(off, universe, extra["floor"])
              if jumped(c)]
    return sorted(live + extras, key=lambda r: (r["slot_ms"], r.get("rank", 0)))


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    # One hourly replay: its 4h-aligned hours are the live publishes
    # (publication_slots_every gives production's instants at 4h boundaries).
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=1,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    rows = []
    for label, extra in VARIANTS:
        recs = topup_recs(cands, universe, floor=floor, extra=extra)
        b = rr.book(recs, market, floor=floor)
        extras = [t for t in b["trades"] if t["slot_ms"] % SLOT_MS]
        done_x = [t for t in extras if t["outcome"] != "open_at_end"]
        rows.append({"label": label, "signals": len(recs),
                     "extra_signals": sum(1 for r in recs if r["extra"]),
                     "extra_trades": len(done_x),
                     "extra_pnl_usd": round(sum(t["pnl_usd"] for t in done_x), 2),
                     **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No hourly top-up beats live here (${live['pnl_usd']:+.2f}). Check the "
                "other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}; "
            f"the extras alone ${best['extra_pnl_usd']:+.2f} over {best['extra_trades']} "
            "trades). Adopt only if it also wins the other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🕐 Hourly HL top-up vs live 4h (HL book, v54 exits)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps, 4h signals at {result['floor']:g}+, same 1H+2H engine "
             "checked hourly for the extras, price-only", ""]
    for r in result["rows"]:
        head = f"• {r['label']} ({r['signals']} signals"
        head += f", {r['extra_signals']} off-slot)" if r["extra_signals"] else ")"
        if not r.get("trades"):
            lines += [f"{head}: no trades", ""]
            continue
        lines += [head,
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}"]
        if r["extra_signals"]:
            lines.append(f"  of which hourly extras: {r['extra_trades']} trades, "
                         f"${r['extra_pnl_usd']}")
        lines.append("")
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hourly_topup_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR,
                    help="unused; the 4h floor is fixed at 69 like HL today")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = None
    if args.end_days_ago:
        end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
            // HOUR_MS * HOUR_MS
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, core=core, correlations=appmod._BTC_CORR)
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
