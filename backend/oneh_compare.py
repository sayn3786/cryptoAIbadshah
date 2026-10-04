"""
1H-only signals, published every hour, vs the live setup.

The live strategy scores each coin on its 1H AND 2H charts (2H primary, the
two must agree; v53 caps a 1H/2H split) and publishes every 4H. Proposed:
score on the 1H chart ALONE — direction, strength, entry, stop and targets
from the 1H signal, BTC's 1H direction for the BTC adjustment — and publish
every hour.

Both are executed the way HL does with the live rules: production's screen
(R:R floor, chase cap, stale price), market entry with the stale guard, v54
exits (stop +1 ATR, stop to entry at 1R, limit TPs), $25 a trade, HL caps (3
open, 3 per slot), every signal at the floor. Each at a 69 and a 62 floor.
Run on both periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m oneh_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOORS = (69.0, 62.0)
SETUPS = (("live: 1H+2H, every 4h", {"interval_hours": 4, "primary_tf": "2H"}),
          ("1H only, every hour", {"interval_hours": 1, "primary_tf": "1H"}))


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floors=FLOORS, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    universe = [s for s in core if s in market]
    cache: Dict = {}
    rows = []
    for label, cfg in SETUPS:
        rep = pbt.replay(market, correlations=correlations or {}, start_ms=start,
                         execute=False, keep_candidates=True, keep_trades=False,
                         reading_cache=cache, **cfg)
        cands = [c for c in rep["candidates"] if c["symbol"] in universe]
        for floor in floors:
            recs = uc.all_floor(cands, universe, floor)
            b = rr.book(recs, market, floor=floor)
            rows.append({"label": f"{label} · floor {floor:g}", "setup": label,
                         "floor": floor, "signals": len(recs),
                         **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "rows": rows}


def verdict(result: Dict) -> str:
    rows = {r["label"]: r for r in result["rows"] if r.get("trades")}
    live = rows.get(f"{SETUPS[0][0]} · floor 69")
    if not live:
        return "Not enough live trades to compare."
    oneh = [r for r in result["rows"] if r["setup"] == SETUPS[1][0] and r.get("trades")]
    if not oneh:
        return "The 1H-only setup produced no trades."
    best = max(oneh, key=lambda r: r["pnl_usd"])
    if best["pnl_usd"] <= live["pnl_usd"]:
        return (f"1H-only does not beat live here (best {best['label']}: "
                f"${best['pnl_usd']:+.2f} vs ${live['pnl_usd']:+.2f}).")
    dd = "" if best["max_dd_usd"] <= live["max_dd_usd"] * 1.25 else ", with a deeper drawdown"
    return (f"1H-only beats live here: {best['label']} ${best['pnl_usd']:+.2f} vs "
            f"${live['pnl_usd']:+.2f} ({best['trades']} vs {live['trades']} trades{dd}). "
            "Adopt only if it also wins the other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["⏱️ 1H-only hourly vs live (HL book, v54 exits)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             "$25 a trade, HL caps (3 open, 3 per slot), every signal at the floor, "
             "price-only", ""]
    for r in result["rows"]:
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades ({r['signals']} signals)", ""]
            continue
        lines += [f"• {r['label']} ({r['signals']} signals)",
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oneh_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=69,
                    help="unused; both floors (69, 62) are always reported")
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
