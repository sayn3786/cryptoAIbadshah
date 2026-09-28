"""
More coins, and every 69+ signal: would either give HL more GOOD trades?

Replays the live strategy ONCE over the extended universe (the 30 traded coins
plus the browse-only ones; GOMINING stays out, it was removed for losses) and
keeps every screened candidate of every slot. Four trade sets come from it:

  traded coins, top 3     what HL trades today (the published top three, 69+)
  traded coins, all 69+   every 69+ candidate in a slot, not just the top three
  all coins, top 3        the top three chosen from every coin
  all coins, all 69+      both
(Coin counts include BTC, which is never traded itself.)

Each is executed the way HL does with the v54 rules (market entry with the
stale guard, stop +1 ATR, stop to entry at 1R, limit take-profits, one position
per coin), twice:

  HL caps today   at most 3 positions open (the $100 exposure cap with $25
                  orders and the slippage allowance) and 3 orders per slot
  no cap          what raising HL_MAX_EXPOSURE_USD / the order cap would allow

All-coin rows also say how many trades, and how much P&L, came from the new
coins. Run it on two periods (--end-days-ago 0 and 135) before enabling
anything: the new coins must earn their place out of sample too.

    python -m universe_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import combined_compare as cm
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rec_policy

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
V54_EXIT = {"tp1_frac": 0.5, "be": 1.0}
V54_STOP = {"atr_add": 1.0}
HL_CAPS = {"max_open": 3, "max_per_slot": 3}
EXCLUDED = ("GOMINING",)          # removed from trading for losses; never re-added here


def by_slot(cands: Sequence[Dict]) -> Dict[int, List[Dict]]:
    out: Dict[int, List[Dict]] = {}
    for c in cands:
        out.setdefault(c["slot_ms"], []).append(c)
    return out


def top3(cands: Sequence[Dict], universe: Sequence[str]) -> List[Dict]:
    """Per slot, the published top three chosen from `universe` only, ranked
    and correlation-deferred exactly as production does."""
    allowed = set(universe)
    out = []
    for slot, cs in sorted(by_slot(cands).items()):
        mine = [c for c in cs if c["symbol"] in allowed]
        ranked = rec_policy.rank_candidates(mine)
        for i, c in enumerate(rec_policy.select_publishable(ranked), start=1):
            if c.get("entry") and c.get("sl") and c.get("tp_targets"):
                out.append({**c, "rank": i})
    return out


def all_floor(cands: Sequence[Dict], universe: Sequence[str],
              floor: float = FLOOR) -> List[Dict]:
    """Every candidate at or above `floor` in `universe`, strongest first per slot."""
    allowed = set(universe)
    out = []
    for slot, cs in sorted(by_slot(cands).items()):
        mine = [c for c in cs if c["symbol"] in allowed and (c.get("strength") or 0) >= floor
                and c.get("entry") and c.get("sl") and c.get("tp_targets")]
        for i, c in enumerate(rec_policy.rank_candidates(mine), start=1):
            out.append({**c, "rank": i})
    return out


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    cands = rep["candidates"]
    core = [s for s in core if s in market]
    extended = [s for s in market if s not in EXCLUDED]
    new = set(extended) - set(core)
    nc, ne = len(core), len(extended)
    sets = ((f"{nc} coins, top 3 (today)", top3(cands, core)),
            (f"{nc} coins, all {floor:g}+", all_floor(cands, core, floor)),
            (f"{ne} coins, top 3", top3(cands, extended)),
            (f"{ne} coins, all {floor:g}+", all_floor(cands, extended, floor)))
    rows = []
    for caps_label, caps in (("HL caps today (3 open)", HL_CAPS), ("no cap", {})):
        for label, recs in sets:
            b = ee.run_book(recs, market, skip=lambda r: False,
                            exit_cfg={**V54_EXIT, "costs": cm.LIMIT_TPS},
                            min_strength=floor, fee_bps=cm.TAKER_BPS,
                            slippage_bps=cm.SLIP_BPS, stop_variant=V54_STOP, **caps)
            m = ee.metrics(b["trades"], span)
            new_tr = [t for t in b["trades"] if t["symbol"] in new]
            rows.append({"group": caps_label, "label": label, **m,
                         "max_open": cc.max_concurrent(
                             [{"filled_at": t["slot_ms"], "closed_at": t["closed_at"]}
                              for t in b["trades"]]),
                         "capped": b["counts"]["capped"],
                         "new_coins": (ee.metrics(new_tr, span) if new else None)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "coins": {"core": len(core), "extended": len(extended), "new": sorted(new)},
            "candidates": len(cands), "floor": floor, "rows": rows}


def verdict(result: Dict) -> str:
    base = next(r for r in result["rows"] if r["group"].startswith("HL caps")
                and r["label"].endswith("top 3 (today)"))
    out = []
    capped = [r for r in result["rows"] if r["group"].startswith("HL caps") and r.get("trades")]
    best_capped = max(capped, key=lambda r: r["total_net_pct"]) if capped else None
    if best_capped and best_capped is not base and base.get("trades"):
        out.append(f"Within today's caps, best: {best_capped['label']} "
                   f"({best_capped['trades']} vs {base['trades']} trades, "
                   f"{best_capped['total_net_pct']:+.1f}% vs {base['total_net_pct']:+.1f}%).")
    elif base.get("trades"):
        out.append("Within today's caps nothing beats today's set.")
    uncapped = [r for r in result["rows"] if r["group"] == "no cap" and r.get("trades")]
    if uncapped and best_capped:
        u = max(uncapped, key=lambda r: r["total_net_pct"])
        if u["total_net_pct"] > best_capped["total_net_pct"] + 1:
            out.append(f"Raising the cap would add most with {u['label']} "
                       f"({u['total_net_pct']:+.1f}%, up to {u['max_open']} open at once).")
    newc = [r["new_coins"] for r in result["rows"] if r.get("new_coins") and
            r["new_coins"].get("trades")]
    if newc:
        worst = min(n["avg_net_pct"] for n in newc)
        best = max(n["avg_net_pct"] for n in newc)
        out.append("The new coins' own trades were "
                   + ("profitable" if worst > 0 else "losing" if best <= 0 else "mixed")
                   + f" ({worst:+.3f}% to {best:+.3f}%/trade across rows).")
    return " ".join(out) or "Not enough trades to compare."


def render_telegram(result: Dict) -> str:
    w, co = result["window"], result["coins"]
    lines = ["🪙 More coins / all 69+ backtest (v54 rules, HL book, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"strength ≥ {result['floor']:g}, stop +1 ATR, break-even at 1R, limit TPs, "
             "one position per coin, price-only",
             f"{co['core']} traded coins + {len(co['new'])} new: {', '.join(co['new'])}", ""]
    last = None
    for r in result["rows"]:
        if r["group"] != last:
            lines.append(r["group"].upper())
            last = r["group"]
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        lines += [f"• {r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · "
                  f"{r['avg_net_pct']}%/trade",
                  f"  total {r['total_net_pct']}% · PF {r['profit_factor']} · "
                  f"max DD {r['max_dd_pct']}% · max open {r['max_open']}"
                  + (f" · skipped by cap {r['capped']}" if r.get("capped") else "")]
        n = r.get("new_coins")
        if n and n.get("trades"):
            lines.append(f"  new coins: {n['trades']} trades · {n['avg_net_pct']}%/trade · "
                         f"total {n['total_net_pct']}%")
        lines.append("")
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="universe_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--candles", help="saved candles (JSON {symbol: {1H, 2H, 4H}})")
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--days", type=float, help="only the last N days")
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR,
                    help="the auto-exec floor (HL_AUTO_MIN_STRENGTH)")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    extended = [s for s in appmod.SYMBOLS if s not in EXCLUDED]
    if args.candles:
        import portfolio_backtest_cli as cli
        market = cli.load_candles(args.candles)
    else:
        end_ms = None
        if args.end_days_ago:
            import time
            end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
                // HOUR_MS * HOUR_MS
        print(f"downloading {args.fetch_days:g} days of 1H history for "
              f"{len(extended)} coins...", file=sys.stderr)
        market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in extended}, args.fetch_days,
                                  end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
        if "BTC" not in market:
            print("error: no BTC history", file=sys.stderr)
            return 2
    res = compare(market, core=core, days=args.days, correlations=appmod._BTC_CORR,
                  floor=args.min_strength)
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
