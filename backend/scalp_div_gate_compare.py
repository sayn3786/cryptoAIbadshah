"""
15m scalp: the divergence rule (Nov 21 2026) at entry thresholds 90 / 95 / 100.

  before  the live 15m scalp score (scalp_live.compose_score on the reviewed
          15M engine): every RSI divergence, regular / hidden / forming, adds
          its points
  after   the owner's rule: a bullish divergence counts only with a pivot at an
          oversold bottom (RSI <= 40), a bearish one only at an overbought top
          (RSI >= 60) - in the engine score and the scalp's reversal reads

Same candles, same score otherwise, the LIVE exits (scalp_live.exits: swing /
ATR stop, swing or 1.5R target; max hold scalp_live.max_hold_ms, one position
per coin, $RISK at risk) and costs (entry / stop taker + slippage, target
maker). Closed candles only. For each threshold: trades, trades a day, win %,
PF, net $, max drawdown. Run it on two periods (--end-days-ago 0 and 135):
one period is not evidence.

    python -m scalp_div_gate_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Sequence

import candle_analysis
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_live as sl
import scalp_reversal_compare as rc
import scalp_score_compare as ssc
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
THRESHOLDS = (90.0, 95.0, 100.0)
RISK = sl.DEFAULT_RISK_USD


def book(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], *,
         sim: Callable = ee.simulate_hl) -> List[Dict]:
    """One position per coin, the live structure exits, RISK dollars at risk."""
    trades, open_until = [], {}
    idx = {s: {int(b["timestamp"]) + M15: i for i, b in enumerate(bars)} for s, bars in m15.items()}
    piv = {s: sf.pivots(bars) for s, bars in m15.items()}
    hold_h = sl.max_hold_ms() / 3_600_000
    for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["score"])):
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until:
            continue
        i = idx[sym].get(t0)
        if i is None:
            continue
        ex = sl.exits(m15[sym], i, c["direction"], piv[sym])
        if not ex:
            continue
        entry = float(m15[sym][i]["close"])
        stop_pct = abs(entry - ex["sl"]) / entry
        if stop_pct <= 0:
            continue
        rec = {"symbol": sym, "direction": c["direction"], "entry": entry, "sl": ex["sl"],
               "tp_targets": [ex["target"]], "slot_ms": t0}
        t = sim(rec, m15[sym], tp1_frac=1.0, be=None, costs=rc.cm.LIMIT_TPS,
                max_hold_hours=hold_h)
        if not t.get("taken"):
            continue
        notional = RISK / stop_pct
        pnl = round(t["net_pct"] / 100 * notional, 4)
        t.update(usd=notional, pnl_usd=pnl, pnl_risk=pnl, notional_risk=notional,
                 lev_risk=sl.DEFAULT_SCALP_LEVERAGE)
        trades.append(t)
        open_until[sym] = sc.busy_until(t)
    return trades


def evaluate(cands: Sequence[Dict], struct: Dict, m15: Dict[str, List[Dict]], span: float, *,
             sim: Callable = ee.simulate_hl) -> Dict:
    scored = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        scored.append({**c, "score": ssc.proposed_score(c["strength"], kind, c["reads"], h1)})
    rows = []
    for th in THRESHOLDS:
        pool = [x for x in scored if x["score"] >= th]
        trades = book(pool, m15, sim=sim)
        m = ss.metrics(trades, span)
        m["per_day"] = round(len(trades) / span, 1) if span else None
        rows.append({"threshold": th, "signals": len(pool), **m})
    return {"reads": len(scored), "rows": rows}


def _build_gated(win, tf, sym):
    a = candle_analysis.build_candle_analysis(list(win), tf, sym)
    if tf == "15M":
        a["div_need_extreme"] = True
    return a


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            sim: Callable = ee.simulate_hl) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    struct = ssc.structure_at(m15, start_ms=start_ms)
    out = {}
    for name, build, rev in (
            ("before", candle_analysis.build_candle_analysis,
             lambda a, w: sl.reversal_reads(a, w, need_extreme=False)),
            ("after", _build_gated, sl.reversal_reads)):
        cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, build=build, rev=rev)
        out[name] = evaluate(cands, struct, m15, span, sim=sim)
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), **out}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["🧪 15m scalp: the divergence rule (only at an oversold bottom / overbought top)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             f"Live exits: swing / ATR stop, swing or {sl.target_r():g}R target, max "
             f"{sl.max_hold_ms() / 3_600_000:g}h, ${RISK:g} risk, one per coin · costs: entry / stop "
             "6.5 bps, target 1.5 bps",
             "✅ = 30+ trades, profit, PF ≥ 1.2 · scores are scores, not probabilities", ""]
    for name, label in (("before", "BEFORE (every divergence counts)"),
                        ("after", "AFTER (divergence only at an oversold bottom / overbought top)")):
        r = res[name]
        lines.append(f"• {label}")
        for row in r["rows"]:
            mark = "✅ " if sc.works(row) else ""
            if not row.get("trades"):
                lines.append(f"  {mark}{row['threshold']:g}+: no trades")
                continue
            lines.append(f"  {mark}{row['threshold']:g}+: {row['trades']} trades ({row['per_day']}/day) · "
                         f"win {row['win_rate_pct']}% · PF {row['pf_usd']} · ${row['pnl_usd']} "
                         f"(max DD ${row['max_dd_usd']})")
        lines.append("")
    lines.append("One period is not evidence: compare with the other run (end_days_ago 0 and 135).")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_div_gate_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms, log=log)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
