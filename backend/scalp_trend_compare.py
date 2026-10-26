"""
15m scalps on TRENDING coins only: what turns the ADX >= 25 book profitable?

The scalp-chop study found one filter that helped in both periods: no new
scalp unless the coin's own 1H ADX (14) is 25+ (-$42 vs -$296 and -$156 vs
-$394 risk-sized, PF 0.91 / 0.80). It is live now, but it still loses: the win
rate stays ~37-40% at a 1.5R target. On top of it, this tries:

  floor 80 / 85     a higher live score (engine + sweep +10 +/- 1H 5)
  ADX 30            a stronger trend
  1H must agree     the coin's 1H engine read the same way (a requirement,
                    not +/- 5 points)
  2R target         the swing target if 2R+ away, else 2R (was 1.5R)
  combinations      1H agrees + 2R, floor 80 + 1H agrees, ADX 30 + 1H agrees

Everything else is the live book (scalp_chop_compare.live_scalps): market
entry, swing / ATR stop, stop to entry at 1R, max 4h, one per coin, no open
cap, HL fees, risk-sized $1 at the stop. A variant WORKS with 30+ trades, a
profit and PF >= 1.2; adopt it only if it works in both periods
(--end-days-ago 0 and 135).

    python -m scalp_trend_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_live as sl
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
LIVE_FLOOR = cc.FLOOR

VARIANTS = (   # (label, min ADX, score floor, 1H must agree, target R)
    ("ADX ≥ 25 (live now)", 25.0, 75.0, False, None),
    ("ADX ≥ 30", 30.0, 75.0, False, None),
    ("ADX ≥ 25 · floor 80", 25.0, 80.0, False, None),
    ("ADX ≥ 25 · floor 85", 25.0, 85.0, False, None),
    ("ADX ≥ 25 · 1H must agree", 25.0, 75.0, True, None),
    ("ADX ≥ 25 · 2R target", 25.0, 75.0, False, 2.0),
    ("ADX ≥ 25 · 1H agrees · 2R", 25.0, 75.0, True, 2.0),
    ("ADX ≥ 25 · floor 80 · 1H agrees", 25.0, 80.0, True, None),
    ("ADX ≥ 30 · 1H agrees", 30.0, 75.0, True, None),
)


def with_target(c: Dict, target_r: Optional[float]) -> Dict:
    """The scalp with its target at least `target_r` x risk away (a farther
    swing target is kept). Unchanged when target_r is None."""
    if not target_r:
        return c
    entry, stop, tgt = float(c["entry"]), float(c["sl"]), float(c["tp_targets"][0])
    risk = abs(entry - stop)
    sgn = 1 if c["direction"] == "LONG" else -1
    if risk <= 0 or abs(tgt - entry) >= target_r * risk:
        return c
    return {**c, "tp_targets": [entry + sgn * target_r * risk]}


def book(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], regime: cc.Regime, *,
         min_adx: float, floor: float, need_h1: bool, target_r: Optional[float],
         simulate: Callable = ee.simulate_hl) -> Dict:
    """One position per coin, no cap on how many. {"trades", "skipped"}."""
    trades, skipped, open_until = [], 0, {}
    for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["strength"])):
        if c["strength"] < floor or (need_h1 and c.get("h1_dir") != c["direction"]):
            continue
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until:
            continue
        if cc.blocked(c, regime, None, min_adx):
            skipped += 1
            continue
        c = with_target(c, target_r)
        rec = {"symbol": sym, "direction": c["direction"], "entry": c["entry"],
               "sl": c["sl"], "tp_targets": c["tp_targets"], "slot_ms": t0}
        t = simulate(rec, m15[sym], tp1_frac=1.0, be=1.0, costs=cm.LIMIT_TPS,
                     max_hold_hours=cc.MAX_HOLD_H)
        if not t.get("taken"):
            continue
        plan = sl.size_plan(t["fill"], t["stop"], cc.MCAPS.get(sym))
        t.update(usd=sc.USD, pnl_usd=round(t["net_pct"] / 100 * sc.USD, 4),
                 pnl_risk=round(t["net_pct"] / 100 * plan["notional_usd"], 4),
                 notional_risk=plan["notional_usd"], lev_risk=plan["leverage"])
        trades.append(t)
        open_until[sym] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return {"trades": trades, "skipped": skipped}


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = sc.engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read_fn,
                                    keep_from=LIVE_FLOOR - sl.SWEEP_BONUS - sl.H1_POINTS)
        log(f"  {sym}: scored")                          # no results in the (public) log
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    setups = {s: sf.setups(b, at=sl.ENTRY_AT, start_ms=start_ms, piv=piv[s])
              for s, b in m15.items()}
    scalps = cc.live_scalps(cands, m15, piv, setups)
    regime = cc.Regime(m15)
    rows = []
    for label, min_adx, floor, need_h1, target_r in VARIANTS:
        got = book(scalps, m15, regime, min_adx=min_adx, floor=floor, need_h1=need_h1,
                   target_r=target_r)
        m = ss.metrics(got["trades"], span)
        m["per_day"] = round(len(got["trades"]) / span, 1) if span else None
        rows.append({"label": label, "skipped": got["skipped"], **m})
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def works(r: Dict) -> bool:
    return sc.works(r)


def verdict(result: Dict) -> str:
    base = result["rows"][0]
    good = [r for r in result["rows"] if works(r)]
    if good:
        best = max(good, key=lambda r: r["risk_pnl_usd"])
        return (f"{len(good)} variant(s) work here; best: {best['label']} (PF {best['pf_usd']}, "
                f"{best['r_total']:+.1f}R, ${best['risk_pnl_usd']:+.2f} risk-sized). Adopt only "
                "if it also works in the other period.")
    traded = [r for r in result["rows"] if r.get("trades")]
    best = max(traded, key=lambda r: r.get("risk_pnl_usd") or -1e9) if traded else None
    if best and best is not base and (best.get("risk_pnl_usd") or 0) > (base.get("risk_pnl_usd") or 0):
        return (f"No variant clears the bar; {best['label']} loses least "
                f"(${best['risk_pnl_usd']:+.2f} vs ${base.get('risk_pnl_usd', 0):+.2f} live). "
                "Check the other period.")
    return "No variant beats the live ADX ≥ 25 book here."


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["📈 15m scalps on trending coins (1H ADX): what makes it pay?",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             "Live rules (engine + sweep +10 ± 1H 5, market entry, swing / ATR stop, stop to entry "
             "at 1R, max 4h, one per coin, HL fees), risk-sized $1 at the stop",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · PF "
                  f"{r['pf_usd']} · {r['r_total']:+.1f}R",
                  f"  risk-sized: ${r['risk_pnl_usd']} (max DD ${r['risk_max_dd_usd']}) · "
                  f"$25 a trade: ${r['pnl_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_trend_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 15m history...", file=sys.stderr)
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms,
                  log=log)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
