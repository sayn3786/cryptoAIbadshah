"""
15m: the full smart-money sequence — sweep -> CHoCH -> BOS -> FVG -> entry.

The setup the owner brought (Oct 31), SHORT shown (LONG mirrors):

  1  SWEEP   a candle's wick runs above the latest confirmed swing high and
             the candle closes back below it (the trap)
  2  CHoCH   within CHOCH_BARS, a close below the last confirmed swing low
             before the sweep (the uptrend's last higher low) — character
             changes
  3  BOS     within BOS_BARS after that, after at least one candle, a close
             below every low since the sweep — a new lower low, structure
             broken
  4  FVG     the BOS move leaves a bearish fair value gap (candle m+1's high
             below candle m-1's low), its middle candle within a candle of
             the BOS
  5  ENTRY   a limit at the gap's near edge (or its midpoint) for ENTRY_BARS;
             stop STOP_BUFFER above the sweep's high; target a fixed R
             multiple. Never filled, or the stop traded first = no trade.

Pure price action on closed candles: a swing counts only once SWING candles
after it have closed (sweep_fvg.pivots), so nothing looks ahead. Results in R
(the stop distance) and in dollars at $5 risk a trade (-10% of the $50 margin
the live book uses), HL fees, one position per coin. Reference: the plain
sweep -> FVG (no CHoCH / BOS) the live book already gives +10. A variant WORKS
with 30+ trades, a profit and PF >= 1.2; adopt it only if it works in both
periods (--end-days-ago 0 and 135).

    python -m scalp_smc_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
CHOCH_BARS = sf.SMC_CHOCH_BARS
BOS_BARS = sf.SMC_BOS_BARS
ENTRY_BARS = 8
STOP_BUFFER = 0.0005
MIN_STOP_PCT = sf.SMC_MIN_STOP_PCT
MAX_STOP_PCT = sf.SMC_MAX_STOP_PCT
MAX_HOLD_H = 8
RISK_USD = 5.0
LIMIT_FILL = {**cm.LIMIT_TPS, "entry": cm.MAKER_BPS}

VARIANTS = (   # (label, setup kind, entry at, target R)
    ("sweep -> CHoCH -> BOS -> FVG, 2R", "smc", "edge", 2.0),
    ("sweep -> CHoCH -> BOS -> FVG, 3R", "smc", "edge", 3.0),
    ("sweep -> CHoCH -> BOS -> FVG, 1.5R", "smc", "edge", 1.5),
    ("… entry at the gap's midpoint, 2R", "smc", "mid", 2.0),
    ("reference: sweep -> FVG only, 2R", "sweep_fvg", "edge", 2.0),
)


def _f(bar: Dict, k: str) -> float:
    return float(bar[k])


smc_setups = sf.smc_setups      # shared with the live scalp book (its +20 bonus)


def sweep_fvg_setups(bars: Sequence[Dict], *, start_ms: int, piv: Dict) -> List[Dict]:
    """The plain sweep -> FVG (the live book's +10), entry at the gap's edge."""
    return [{"direction": s["direction"], "ready": s["ready"], "limit": s["limit"],
             "sl": s["sl"], "slot_ms": s["slot_ms"]}
            for s in sf.setups(bars, at="top", min_rr=0.0, start_ms=start_ms, piv=piv)]


def fill(s: Dict, bars: Sequence[Dict]) -> Optional[int]:
    """The candle that fills the limit within ENTRY_BARS after the gap (None
    if never, or the stop traded first)."""
    long = s["direction"] == "LONG"
    for j in range(s["ready"] + 1, min(s["ready"] + 1 + ENTRY_BARS, len(bars))):
        lo, hi = _f(bars[j], "low"), _f(bars[j], "high")
        if (lo <= s["limit"]) if long else (hi >= s["limit"]):
            return j
        if (lo <= s["sl"]) if long else (hi >= s["sl"]):
            return None
    return None


def trade(s: Dict, sym: str, bars: Sequence[Dict], target_r: float, *,
          sim: Callable = ee.simulate_hl) -> Optional[Dict]:
    j = fill(s, bars)
    if j is None:
        return None
    long = s["direction"] == "LONG"
    o = _f(bars[j], "open")
    px = min(o, s["limit"]) if long else max(o, s["limit"])
    risk = abs(px - s["sl"])
    if not risk:
        return None
    tgt = px + target_r * risk if long else px - target_r * risk
    first = dict(bars[j])
    first.update({"open": px, "high": max(_f(first, "high"), px), "low": min(_f(first, "low"), px)})
    rec = {"symbol": sym, "direction": s["direction"], "entry": px, "sl": s["sl"],
           "tp_targets": [tgt], "slot_ms": int(bars[j]["timestamp"])}
    t = sim(rec, [first] + list(bars[j + 1:]), tp1_frac=1.0, be=None, costs=LIMIT_FILL,
            max_hold_hours=MAX_HOLD_H)
    if not t.get("taken"):
        return None
    stop_pct = risk / px
    notional = RISK_USD / stop_pct
    t.update(usd=notional, pnl_usd=round(t["net_pct"] / 100 * notional, 4),
             pnl_risk=round(t["net_pct"] / 100 * notional, 4), notional_risk=notional,
             lev_risk=round(notional / 50, 1))
    return t


def book(setups: Dict[str, List[Dict]], m15: Dict[str, List[Dict]], target_r: float, *,
         sim: Callable = ee.simulate_hl) -> Dict:
    """One position per coin. {"trades", "missed"} (missed = never filled)."""
    flat = sorted(((s, sym) for sym, ss_ in setups.items() for s in ss_),
                  key=lambda x: x[0]["slot_ms"])
    trades, missed, open_until = [], 0, {}
    for s, sym in flat:
        t0 = s["slot_ms"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until:
            continue
        t = trade(s, sym, m15[sym], target_r, sim=sim)
        if t is None:
            missed += 1
            continue
        trades.append(t)
        open_until[sym] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return {"trades": trades, "missed": missed}


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    found = {}
    for kind in ("smc", "sweep_fvg"):
        for at in ("edge", "mid"):
            if kind == "sweep_fvg" and at == "mid":
                continue
            found[(kind, at)] = {
                s: (smc_setups(b, start_ms=start_ms, piv=piv[s], entry_at=at) if kind == "smc"
                    else sweep_fvg_setups(b, start_ms=start_ms, piv=piv[s]))
                for s, b in m15.items()}
    log("  setups found")                                  # no results in the (public) log
    rows = []
    for label, kind, at, r in VARIANTS:
        got = book(found[(kind, at)], m15, r)
        m = ss.metrics(got["trades"], span)
        m["per_day"] = round(len(got["trades"]) / span, 1) if span else None
        m["setups"] = sum(len(v) for v in found[(kind, at)].values())
        rows.append({"label": label, "missed": got["missed"], **m})
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def works(r: Dict) -> bool:
    return sc.works(r)


def verdict(result: Dict) -> str:
    good = [r for r in result["rows"] if works(r)]
    if good:
        best = max(good, key=lambda r: r["r_total"])
        return (f"{len(good)} variant(s) work here; best: {best['label']} (PF {best['pf_usd']}, "
                f"{best['r_total']:+.1f}R). Adopt only if it also works in the other period.")
    traded = [r for r in result["rows"] if r.get("trades")]
    best = max(traded, key=lambda r: r.get("r_total") or -1e9) if traded else None
    return (f"No variant clears the bar; best: {best['label']} ({best['r_total']:+.1f}R)."
            if best else "No setups found.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🧠 15m smart-money: sweep → CHoCH → BOS → FVG → entry",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             f"Limit in the FVG for {ENTRY_BARS} candles, stop above / below the sweep, fixed R "
             f"target, max {MAX_HOLD_H}h, HL fees; $ at ${RISK_USD:g} risk a trade",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades ({r.get('setups', 0)} setups)", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['setups']} setups · {r['trades']} filled ({r['per_day']}/day), "
                  f"{r['missed']} not · win {r['win_rate_pct']}% · PF {r['pf_usd']} · "
                  f"{r['r_total']:+.1f}R",
                  f"  ${r['pnl_usd']} at ${RISK_USD:g} risk (max DD ${r['max_dd_usd']})", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_smc_compare", description=__doc__.split("\n\n")[0])
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
