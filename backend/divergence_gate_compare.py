"""
RSI divergence by the book: "a warning, not an entry".

Today a confirmed RSI divergence adds 12-18 points (forming: 8) on its own,
at any RSI level. The indicator study found those points paid losers in both
periods (-0.18 / -0.22). The textbook rule:

  A  works best near 70 / 30   a regular divergence counts only when one of
                               its RSI pivots is at 35 or under (bullish) /
                               65 or over (bearish); hidden ones unchanged
  B  wait for a break of       a divergence counts only once a break of
     structure                 structure the same way closed at or after its
                               second pivot and still holds (forming ones
                               never count)
  A+B                          both
  none                         the divergence points removed outright (reference)

A divergence that fails the gate loses its points on that timeframe (1H and
2H judged separately); the 1H / 2H strengths move by points / 220 x 100 and
the v53 caps are re-applied (reweight_compare.reweighted). Then the live
book: traded coins, every signal at 69+, v54 exits, $25 a trade, HL caps.
Run on both periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m divergence_gate_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rr_compare as rr
import reweight_compare as rw
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
OVERSOLD = 35.0
OVERBOUGHT = 65.0
SECTION = "rsi_divergence"

VARIANTS = (          # (label, zone rule, break-of-structure rule, drop all)
    ("live (divergence counts on its own)", False, False, False),
    ("A only near 30 / 70 (RSI pivot <=35 / >=65)", True, False, False),
    ("B only after a break of structure", False, True, False),
    ("A+B", True, True, False),
    ("no divergence points at all", False, False, True),
)


def _bullish(t: Optional[str]) -> bool:
    return t in ("bullish", "hidden_bullish")


def in_zone(d: Dict) -> bool:
    """A regular divergence with an RSI pivot near 30 (bullish) / 70 (bearish);
    hidden divergences (trend continuation) always pass."""
    t = d.get("type")
    if t in ("hidden_bullish", "hidden_bearish"):
        return True
    rsis = [x for x in (d.get("prev_rsi"), d.get("curr_rsi")) if x is not None]
    if not rsis:
        return False
    return min(rsis) <= OVERSOLD if t == "bullish" else max(rsis) >= OVERBOUGHT


def structure_broke(d: Dict) -> bool:
    """A break of structure the divergence's way, at or after its second
    pivot, still holding. A forming divergence has no confirmed pivot yet."""
    if d.get("status") == "forming" or d.get("curr_ts") is None or d.get("bos_ts") is None:
        return False
    return (d.get("bos_dir") == ("bullish" if _bullish(d.get("type")) else "bearish")
            and bool(d.get("bos_held")) and int(d["bos_ts"]) >= int(d["curr_ts"]))


def passes(d: Dict, zone: bool, bos: bool, drop: bool = False) -> bool:
    """Does this timeframe's divergence keep its points? No divergence: yes."""
    if not (d or {}).get("type"):
        return True
    if drop:
        return False
    return (not zone or in_zone(d)) and (not bos or structure_broke(d))


def gated(c: Dict, zone: bool, bos: bool, drop: bool = False) -> float:
    """The candidate's strength with failing divergences' points removed. Pure."""
    m2 = {} if passes(c.get("h2_div") or {}, zone, bos, drop) else {SECTION: 0.0}
    m1 = {} if passes(c.get("h1_div") or {}, zone, bos, drop) else {SECTION: 0.0}
    if not m1 and not m2:
        return float(c.get("strength") or 0)
    return rw.reweighted(c, m2, h1_mults=m1)


def apply(cands: Sequence[Dict], zone: bool, bos: bool, drop: bool = False) -> List[Dict]:
    out = []
    for c in cands:
        s = gated(c, zone, bos, drop)
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
    base_recs = uc.all_floor(cands, universe, floor)
    base = {(c["symbol"], c["slot_ms"]) for c in base_recs}
    rows = []
    for label, zone, bos, drop in VARIANTS:
        recs = uc.all_floor(apply(cands, zone, bos, drop), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), "added": len(keys - base),
                     "removed": len(base - keys), **rr.usd_metrics(b["trades"], span)})
    divs = [d for c in base_recs for d in (c.get("h2_div") or {},) if d.get("type")]
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base), "with_div": len(divs),
            "in_zone": sum(1 for d in divs if in_zone(d)),
            "after_bos": sum(1 for d in divs if structure_broke(d)), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No divergence rule beats live here (${live['pnl_usd']:+.2f}). Check the "
                "other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["〽️ RSI divergence by the book on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps; {result['at_floor']} signals at {result['floor']:g}+ today, "
             f"{result['with_div']} with a 2H divergence: {result['in_zone']} near 30/70, "
             f"{result['after_bos']} after a break of structure", ""]
    for r in result["rows"]:
        moved = [f"+{r['added']}" if r["added"] else "", f"−{r['removed']}" if r["removed"] else ""]
        moved = " / ".join(m for m in moved if m)
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
    ap = argparse.ArgumentParser(prog="divergence_gate_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR,
                    help="unused; the HL floor is fixed at 69 like today")
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
