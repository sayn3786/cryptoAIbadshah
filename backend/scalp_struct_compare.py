"""
15m scalps: the engine's direction with STRUCTURE exits, sweep -> FVG as a
bonus, and risk-based sizing with market-cap leverage.

The engine as a 15m scalper lost in all 24 variants (scalp_compare), but it
traded the engine's own 15m ladder, whose first target was often half the
stop distance. This asks whether the engine's 15m DIRECTION is any good once
the exits come from structure, and whether the sweep -> FVG setup adds to it
instead of being required:

  A  engine 15m signal (75+): market entry at the next open; stop beyond the
     latest confirmed swing on the other side (+0.05%), target the nearest
     confirmed swing beyond, only at R:R 1.5+
  B  as A, but a sweep -> FVG setup the same way in the last 4 candles adds
     +10 strength (so a 65+ engine signal with a setup qualifies)
  C  A's trades OR a sweep -> FVG setup on its own (limit in the gap, as in
     sweep_fvg_compare) — either can trigger
  each also with the coin's 1H engine read agreeing; and, for reference, the
  engine's own ladder at 75+ (what lost before)

All out at the target, stop to entry at 1R, max 12h, one position per coin,
<= 3 open, HL fees. Two sizings: $25 a trade (the old way), and RISK sizing
(scalp_live.size_plan): $1 lost at the stop, the position = $1 / stop
distance, capped at $25 margin x the coin's market-cap leverage (mega 20x,
large 10x, mid 7x). Leverage never changes the R a trade makes — only how
many dollars ride on it — so a variant WORKS on R first: 30+ trades, a
profit and PF >= 1.2, in both periods (--end-days-ago 0 and 135).

    python -m scalp_struct_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import rr_compare as rr
import scalp_compare as sc
import scalp_live as sl
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
FLOOR = 75.0
SWEEP_BONUS = 10.0
SWEEP_RECENT = 4
MIN_RR = 1.5
MAX_HOLD_H = 12
LIMIT_FILL = {**cm.LIMIT_TPS, "entry": cm.MAKER_BPS}
# Approximate market caps (Oct 2026) for the leverage tier of each scalp coin.
MCAPS = {"BTC": 2e12, "ETH": 4e11, "XRP": 1.5e11, "BNB": 1.2e11, "SOL": 8e10,
         "TRX": 2.5e10, "ADA": 2e10, "HYPE": 1.5e10, "SUI": 1.2e10, "AVAX": 9e9,
         "LINK": 9e9, "AAVE": 4e9}

VARIANTS = (          # (label, kind, 1H must agree)
    ("reference: engine 75+, its own 15m ladder", "ladder", False),
    ("A engine 75+, structure exits", "A", False),
    ("A engine 75+, structure exits · 1H agrees", "A", True),
    ("B A + sweep/FVG bonus +10", "B", False),
    ("B A + sweep/FVG bonus +10 · 1H agrees", "B", True),
    ("C A or a sweep -> FVG setup", "C", False),
    ("C A or a sweep -> FVG setup · 1H agrees", "C", True),
)


def structure_exit(bars: Sequence[Dict], i: int, direction: str, piv: Dict,
                   min_rr: float = MIN_RR) -> Optional[Dict]:
    """Stop beyond the latest confirmed swing on the other side, target the
    nearest confirmed swing beyond, measured from bar i's close; None when
    either is missing or R:R < min_rr. Swings confirmed by bar i only."""
    close = float(bars[i]["close"])
    long = direction == "LONG"
    own = sf._known(piv["low"] if long else piv["high"], i + 1)
    far = sf._known(piv["high"] if long else piv["low"], i + 1)
    stops = [p[1] for p in own if (p[1] < close if long else p[1] > close)]
    targets = [p[1] for p in far if (p[1] > close if long else p[1] < close)]
    if not stops or not targets:
        return None
    sw = stops[-1]
    stop = sw * (1 - sf.STOP_BUFFER) if long else sw * (1 + sf.STOP_BUFFER)
    tgt = min(targets) if long else max(targets)
    risk, reward = abs(close - stop), abs(tgt - close)
    if risk <= 0 or reward / risk < min_rr:
        return None
    return {"sl": stop, "target": tgt, "rr": round(reward / risk, 2)}


def _sizes(t: Dict, symbol: str) -> Dict:
    """P&L at $25 and with risk sizing (market-cap leverage)."""
    plan = sl.size_plan(t["fill"], t["stop"], MCAPS.get(symbol))
    return {"usd": sc.USD, "pnl_usd": round(t["net_pct"] / 100 * sc.USD, 4),
            "pnl_risk": round(t["net_pct"] / 100 * plan["notional_usd"], 4),
            "notional_risk": plan["notional_usd"], "lev_risk": plan["leverage"]}


def trades_for(kind: str, confirm: bool, cands: Sequence[Dict], m15: Dict[str, List[Dict]],
               piv: Dict, setups: Dict[str, List[Dict]], *,
               simulate: Callable = ee.simulate_hl) -> List[Dict]:
    """The book for one variant: one position per coin, <= 3 open."""
    idx = {s: {int(c["timestamp"]): k for k, c in enumerate(b)} for s, b in m15.items()}
    entries = []
    for c in cands:
        if confirm and c.get("h1_dir") != c["direction"]:
            continue
        s_ = c["strength"]
        i = idx[c["symbol"]].get(c["slot_ms"] - M15)
        if i is None:
            continue
        if kind == "B" and any(x["direction"] == c["direction"] and 0 <= i - x["ready"] < SWEEP_RECENT
                               for x in setups.get(c["symbol"], [])):
            s_ += SWEEP_BONUS
        if s_ < FLOOR:
            continue
        if kind == "ladder":
            entries.append(("market", c, {"entry": c["entry"], "sl": c["sl"],
                                          "tp": list(c["tp_targets"]), "frac": 0.5}))
            continue
        ex = structure_exit(m15[c["symbol"]], i, c["direction"], piv[c["symbol"]])
        if ex:
            entries.append(("market", {**c, "strength": s_},
                            {"entry": float(m15[c["symbol"]][i]["close"]), "sl": ex["sl"],
                             "tp": [ex["target"]], "frac": 1.0}))
    if kind == "C":
        for sym, ss in setups.items():
            for x in ss:
                if confirm and x.get("h1_dir") != x["direction"]:
                    continue
                entries.append(("limit", {**x, "symbol": sym, "strength": 0.0}, None))
    trades, open_until = [], {}
    for how, c, ex in sorted(entries, key=lambda e: (e[1]["slot_ms"], -e[1]["strength"])):
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until or len(open_until) >= sc.MAX_OPEN:
            continue
        if how == "limit":
            import sweep_fvg_compare as sfc
            t = sfc.trade(c, m15[sym], simulate=simulate)
        else:
            rec = {"symbol": sym, "direction": c["direction"], "entry": ex["entry"],
                   "sl": ex["sl"], "tp_targets": ex["tp"], "slot_ms": t0}
            t = simulate(rec, m15[sym], tp1_frac=ex["frac"], be=1.0, costs=cm.LIMIT_TPS,
                         max_hold_hours=MAX_HOLD_H)
            t = t if t.get("taken") else None
        if not t:
            continue
        t.update(_sizes(t, sym))
        trades.append(t)
        open_until[sym] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return trades


def metrics(trades: Sequence[Dict], span: float) -> Dict:
    m = rr.usd_metrics(trades, span)
    done = [t for t in trades if t["outcome"] != "open_at_end"]
    risk = [t["pnl_risk"] for t in done]
    m["r_total"] = round(sum(t["r"] for t in done if t.get("r") is not None), 2)
    m["risk_pnl_usd"] = round(sum(risk), 2)
    eq = peak = dd = 0.0
    for t in sorted(done, key=lambda t: t["closed_at"] or 0):
        eq += t["pnl_risk"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    m["risk_max_dd_usd"] = round(dd, 2)
    m["avg_lev"] = round(sum(t["lev_risk"] for t in done) / len(done), 1) if done else None
    m["avg_notional"] = round(sum(t["notional_risk"] for t in done) / len(done)) if done else None
    return m


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = sc.engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read_fn,
                                    keep_from=FLOOR - SWEEP_BONUS)
        log(f"  {sym}: scored")                          # no results in the (public) log
    import sweep_fvg_compare as sfc
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    cache: Dict = {}
    setups = {}
    for sym, bars in m15.items():
        h1 = sc.hourly(bars)
        h1_ts = [int(c["timestamp"]) for c in h1]
        setups[sym] = []
        for x in sf.setups(bars, at="top", min_rr=MIN_RR, start_ms=start_ms, piv=piv[sym]):
            x = {**x, "symbol": sym}
            x["h1_dir"] = x["direction"] if sfc.h1_agrees(x, h1, h1_ts, read_fn, cache) else None
            setups[sym].append(x)
    rows = []
    for label, kind, confirm in VARIANTS:
        trades = trades_for(kind, confirm, cands, m15, piv, setups)
        m = metrics(trades, span)
        m["per_day"] = round(len(trades) / span, 1) if span else None
        rows.append({"label": label, **m})
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def works(r: Dict) -> bool:
    return sc.works(r)


def verdict(result: Dict) -> str:
    good = [r for r in result["rows"] if works(r)]
    if not good:
        return ("No variant works here (30+ trades, profit, PF ≥ 1.2). More leverage would only "
                "make the same R bigger in $; check the other period.")
    best = max(good, key=lambda r: r["pnl_usd"])
    return (f"{len(good)} variant(s) work here; best: {best['label']} (PF {best['pf_usd']}, "
            f"{best['r_total']:+.1f}R, ${best['risk_pnl_usd']:+.2f} risk-sized). Adopt only if "
            "it also works in the other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🧱 15m scalps: engine direction + structure exits (+ sweep/FVG), risk-sized",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             "Stop beyond the last swing, target the next swing (R:R 1.5+), stop to entry at 1R, "
             "max 12h, ≤3 open, HL fees",
             "Sizing: $25 a trade · and RISK: $1 at the stop, $25 margin × market-cap leverage "
             "(mega 20x, large 10x, mid 7x)",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · PF "
                  f"{r['pf_usd']} · {r['r_total']:+.1f}R",
                  f"  $25 a trade: ${r['pnl_usd']} (max DD ${r['max_dd_usd']})",
                  f"  risk-sized: ${r['risk_pnl_usd']} (max DD ${r['risk_max_dd_usd']}; avg "
                  f"${r['avg_notional']} at {r['avg_lev']}x)", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_struct_compare", description=__doc__.split("\n\n")[0])
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
    syms = {s: appmod.SYMBOLS[s] for s in sc.SCALP_SYMBOLS if s in appmod.SYMBOLS}
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
