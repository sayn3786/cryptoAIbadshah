"""
15m scalps: more weight on FRESH reversal-at-a-level reads?

The owner's idea (Oct 31): an oversold bottom / overbought top, the Fib
0.618-0.786 zone, an RSI divergence and a support / resistance zone should
count for more when they line up with the trade and just happened. Today they
count for little (the Fib is only a -5 brake against). On the live book
(engine + sweep +10 +/- 1H 5 at 75+, market entry, the owner's FIXED exits:
$50 margin at 11x, stop -10% / one target +15% of margin, max 4h, one per
coin, HL fees), a read counts for a LONG (mirror for a SHORT) when it is FRESH
(within FRESH_BARS closed candles):

  bottom       an oversold swing bottom (RSI marker) in the last FRESH_BARS
  divergence   a bullish / hidden-bullish RSI divergence whose second pivot is
               within FRESH_BARS + 3 candles (a pivot needs 3 to confirm)
  fib          price inside the 0.618-0.786 DISCOUNT zone of an up-leg
  support      price within AT_LEVEL_PCT of the support zone

Variants: +10 or +5 for each fresh read the trade's way; only trades with
1+ / 2+ of them; and 2+ fresh reads the same way (none against) as a setup of
its own (no engine signal needed). A variant WORKS with 30+ trades, a profit
and PF >= 1.2; adopt it only if it works in both periods (--end-days-ago 0 and
135).

    python -m scalp_reversal_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence, Set

import combined_compare as cm
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_live as sl
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
FLOOR = 75.0
FRESH_BARS = 3
AT_LEVEL_PCT = 0.4
MARGIN = 50.0
LEVERAGE = 11
MAX_HOLD_H = 4
READS = ("bottom", "divergence", "fib", "support")

VARIANTS = (   # (label, points per read, min reads, standalone setups)
    ("live (fixed exits)", 0, 0, False),
    ("+10 per fresh read the trade's way", 10, 0, False),
    ("+5 per fresh read", 5, 0, False),
    ("only with 1+ fresh read", 0, 1, False),
    ("only with 2+ fresh reads", 0, 2, False),
    ("2+ fresh reads as their own setup", 0, 0, True),
)


def fresh_reads(a: Dict, bars: Sequence[Dict]) -> Dict[str, Set[str]]:
    """{"LONG": {...}, "SHORT": {...}}: the fresh reversal-at-a-level reads on
    a 15m candle analysis `a` of `bars` (closed, oldest first). Pure."""
    out: Dict[str, Set[str]] = {"LONG": set(), "SHORT": set()}
    if not bars:
        return out
    last_ts = int(bars[-1]["timestamp"])
    for m in a.get("rsi_markers") or []:
        if (m.get("bars_ago") if m.get("bars_ago") is not None else 99) <= FRESH_BARS:
            if m.get("kind") == "oversold_bottom":
                out["LONG"].add("bottom")
            elif m.get("kind") == "overbought_top":
                out["SHORT"].add("bottom")
    div = a.get("rsi_divergence") or {}
    kind = div.get("type") or ""
    curr = ((div.get("points") or {}).get("curr") or {}).get("timestamp")
    age = (last_ts - int(curr)) // M15 if curr is not None else None
    if kind and (div.get("forming") or (age is not None and age <= FRESH_BARS + 3)):
        if "bullish" in kind:
            out["LONG"].add("divergence")
        elif "bearish" in kind:
            out["SHORT"].add("divergence")
    fib = a.get("fib") or {}
    if fib.get("in_entry_zone") or fib.get("in_golden_pocket"):
        out["LONG" if fib.get("bias") == "long" else "SHORT"].add("fib")
    sr = a.get("sr_zones") or {}
    sup, res = sr.get("support") or {}, sr.get("resistance") or {}
    if sup.get("dist_pct") is not None and abs(float(sup["dist_pct"])) <= AT_LEVEL_PCT:
        out["LONG"].add("support")
    if res.get("dist_pct") is not None and abs(float(res["dist_pct"])) <= AT_LEVEL_PCT:
        out["SHORT"].add("support")
    return out


def fixed_exits(entry: float, direction: str) -> Dict[str, float]:
    lv = sl.roe_levels(entry, direction, LEVERAGE)
    return {"sl": lv["sl"], "target": lv["tp1"]}


def book(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], *,
         sim: Callable = ee.simulate_hl) -> List[Dict]:
    """One position per coin, fixed exits, $50 x 11x."""
    trades, open_until = [], {}
    notional = MARGIN * LEVERAGE
    for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["strength"])):
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until:
            continue
        ex = fixed_exits(float(c["entry"]), c["direction"])
        rec = {"symbol": sym, "direction": c["direction"], "entry": c["entry"], "sl": ex["sl"],
               "tp_targets": [ex["target"]], "slot_ms": t0}
        t = sim(rec, m15[sym], tp1_frac=1.0, be=None, costs=cm.LIMIT_TPS,
                max_hold_hours=MAX_HOLD_H)
        if not t.get("taken"):
            continue
        pnl = round(t["net_pct"] / 100 * notional, 4)
        t.update(usd=notional, pnl_usd=pnl, pnl_risk=pnl, notional_risk=notional,
                 lev_risk=LEVERAGE)
        trades.append(t)
        open_until[sym] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return trades


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            build: Optional[Callable] = None, log=print) -> Dict:
    import candle_analysis
    import signals
    build = build or candle_analysis.build_candle_analysis
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    feats: Dict = {}

    def read(win, tf, sym):
        a = build(list(win), tf, sym)
        if tf == "15M":
            feats[(sym, int(win[-1]["timestamp"]) + M15)] = fresh_reads(a, win)
        return signals.generate_signal(a)

    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read,
                                    keep_from=FLOOR - sl.SWEEP_BONUS - sl.H1_POINTS
                                    - 10 * len(READS))
        log(f"  {sym}: scored")                          # no results in the (public) log
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    setups = {s: sf.setups(b, at=sl.ENTRY_AT, start_ms=start_ms, piv=piv[s])
              for s, b in m15.items()}

    def n_reads(c):
        return len((feats.get((c["symbol"], c["slot_ms"])) or {}).get(c["direction"], ()))

    # Standalone: 2+ fresh reads one way, none the other, at a closed candle.
    own = []
    idx = {s: {int(c["timestamp"]): k for k, c in enumerate(b)} for s, b in m15.items()}
    for (sym, close_ms), f in feats.items():
        if close_ms < start_ms:
            continue
        for d, o in (("LONG", "SHORT"), ("SHORT", "LONG")):
            if len(f[d]) >= 2 and not f[o]:
                i = idx[sym].get(close_ms - M15)
                if i is not None:
                    own.append({"symbol": sym, "slot_ms": close_ms, "direction": d,
                                "strength": 100.0, "entry": float(m15[sym][i]["close"])})
    rows = []
    for label, pts, need, standalone in VARIANTS:
        if standalone:
            pool = own
        else:
            use = [{**c, "strength": c["strength"] + pts * n_reads(c)}
                   for c in cands if n_reads(c) >= need]
            pool = cc.live_scalps(use, m15, piv, setups)
        trades = book(pool, m15)
        m = ss.metrics(trades, span)
        m["per_day"] = round(len(trades) / span, 1) if span else None
        rows.append({"label": label, **m})
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def works(r: Dict) -> bool:
    return sc.works(r)


def verdict(result: Dict) -> str:
    base = result["rows"][0]
    good = [r for r in result["rows"] if works(r)]
    if good:
        best = max(good, key=lambda r: r["pnl_usd"])
        return (f"{len(good)} variant(s) work here; best: {best['label']} (PF {best['pf_usd']}, "
                f"${best['pnl_usd']:+.2f}). Adopt only if it also works in the other period.")
    traded = [r for r in result["rows"][1:] if r.get("trades")]
    best = max(traded, key=lambda r: r.get("pnl_usd") or -1e9) if traded else None
    if best and (best.get("pnl_usd") or 0) > (base.get("pnl_usd") or 0):
        return (f"No variant clears the bar; {best['label']} beats the live book "
                f"(${best['pnl_usd']:+.2f} vs ${base.get('pnl_usd', 0):+.2f}). "
                "Check the other period.")
    return "Fresh reversal reads add nothing to the live book here."


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🔁 15m scalps: fresh bottoms / tops, Fib, divergence, S/R",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             f"Live signals, the owner's fixed exits: ${MARGIN:g} margin at {LEVERAGE}x, stop -10% / "
             "TP +15% of margin, max 4h, one per coin, HL fees",
             f"Fresh = within {FRESH_BARS} candles; at S/R = within {AT_LEVEL_PCT:g}%",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · PF "
                  f"{r['pf_usd']} · ${r['pnl_usd']} (max DD ${r['max_dd_usd']})", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_reversal_compare",
                                 description=__doc__.split("\n\n")[0])
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
