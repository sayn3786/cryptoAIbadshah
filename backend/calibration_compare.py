"""
The v53 strength caps vs the HL floor: are they costing HL its trades?

v53 caps a CHASED entry (price in the top/bottom fifth of its range) and a
1H/2H SPLIT (strengths 20+ apart) at 68 — one point under the auto-exec floor
(69) — and docks a wide split further. It came from a v52 postmortem under the
old exits; the trade-outcome factor study under the v54 exits found the 1H/2H
split was NOT harmful (+0.20%/trade). Live, HL then saw no signal reach 69.

This replays the live HL setup — traded coins, every signal at the floor, v54
exits (stop +1 ATR, stop to entry at 1R, limit TPs), $25 a trade, HL caps —
with the strength HL compares to the floor computed four ways:

  today            the calibrated strength
  no chase cap     the split cap only
  no split cap     the chase cap only
  no caps          the strength before calibration

(The v55 bottom-read boost is left out so the caps are measured alone.)
Run on both periods (--end-days-ago 0 and 135); adopt only what helps in both.

    python -m calibration_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rec_policy
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0


def _raw(c: Dict) -> float:
    v = c.get("strength_before_calibration")
    return float(v if v is not None else c.get("strength") or 0)


def today(c: Dict) -> float:
    return float(c.get("strength") or 0)


def no_chase_cap(c: Dict) -> float:
    return rec_policy.apply_tier_calibration(
        _raw(c), h1_strength=c.get("h1_strength"), h2_strength=c.get("h2_strength"),
        chased=False)["strength"]


def no_split_cap(c: Dict) -> float:
    return rec_policy.apply_tier_calibration(
        _raw(c), h1_strength=None, h2_strength=None, chased=bool(c.get("chased")))["strength"]


def no_caps(c: Dict) -> float:
    return _raw(c)


VARIANTS = (("today (v53 caps)", today), ("no chase cap", no_chase_cap),
            ("no split cap", no_split_cap), ("no caps", no_caps))


def with_strength(cands: Sequence[Dict], fn: Callable[[Dict], float]) -> List[Dict]:
    """Candidates with strength (and the ranking's 1H/2H average) set by `fn`,
    moved by the same amount. Pure."""
    out = []
    for c in cands:
        s = fn(c)
        delta = s - float(c.get("strength") or 0)
        out.append({**c, "strength": s,
                    "avg_tf_strength": (c.get("avg_tf_strength") or c.get("strength") or 0) + delta}
                   if delta else c)
    return out


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    capped = [c for c in cands if _raw(c) >= floor and today(c) < floor]
    stats = {"candidates": len(cands),
             "raw_at_floor": sum(1 for c in cands if _raw(c) >= floor),
             "today_at_floor": sum(1 for c in cands if today(c) >= floor),
             "capped_below_floor": len(capped),
             "capped_chased": sum(1 for c in capped if c.get("chased")),
             "capped_split": sum(1 for c in capped if abs(float(c.get("h1_strength") or 0)
                                                          - float(c.get("h2_strength") or 0))
                                 >= rec_policy.TF_SPLIT_GAP)}
    base = {(c["symbol"], c["slot_ms"]) for c in uc.all_floor(cands, universe, floor)}
    rows = []
    for label, fn in VARIANTS:
        recs = uc.all_floor(with_strength(cands, fn), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(keys), "added": len(keys - base),
                     **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "stats": stats, "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows:
        return "Not enough trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]]
    if not better:
        return (f"Removing a cap doesn't beat today here (${live['pnl_usd']:+.2f}). Check "
                "the other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    dd = "" if best["max_dd_usd"] <= live["max_dd_usd"] * 1.25 else ", with a deeper drawdown"
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs today ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades{dd}). Adopt only if it also wins "
            "the other period.")


def render_telegram(result: Dict) -> str:
    w, st = result["window"], result["stats"]
    lines = ["🧢 v53 strength caps vs the HL floor (v54, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps, floor {result['floor']:g}, no v55 boost",
             f"{st['candidates']} candidates · {st['raw_at_floor']} reach {result['floor']:g} "
             f"before the caps · {st['today_at_floor']} after · capped below it: "
             f"{st['capped_below_floor']} (chased {st['capped_chased']}, 1H/2H split "
             f"{st['capped_split']})", ""]
    for r in result["rows"]:
        head = f"• {r['label']} ({r['signals']} signals" + \
            (f", +{r['added']} vs today)" if r["added"] else ")")
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
    ap = argparse.ArgumentParser(prog="calibration_compare",
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
    res = compare(market, core=core, correlations=appmod._BTC_CORR, floor=args.min_strength)
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
