"""
15m scalp: does the DAILY money lean help as a filter? A pre-registered test
(written Nov 22 2026, before any run).

Why: the 15m scalp lost money at every threshold in replay
(scalp_div_gate_compare). The money leans (Coinbase premium, stablecoin
supply) held in both eras on 1D (macro_lean_test). If the day's lean says
where money is flowing, skipping scalps against it might turn the book.

The live scalp, replayed: the 15m score as live (divergence only at extremes),
entry threshold 100, the live exits (swing / ATR stop, swing or 1.5R target),
the trading SESSION (entries Mon-Fri 13:00-21:00 UTC, flat at 21:00), $10 risk,
one position per coin. The lean is the PREVIOUS closed day's (as live: it is
computed after the daily close), from macro_lean_test's own functions.

Books (same candles, same scalps):
  baseline     every scalp (the book as it trades now)
  no-against   skip a scalp when either lean points the other way
  agree-only   only scalps with at least one lean agreeing and none against

THE FILTER IS ADOPTED only if, in BOTH periods (--end-days-ago 0 and 135),
the chosen book (no-against first, else agree-only) has 30+ trades, a profit
factor >= 1.10 AND at least 0.10 above the baseline's. Otherwise the scalp
stays as it is. Context: alts only.

THRESHOLD CHECK (--thresholds, added Nov 22 2026 before its run): with the
agree-only filter, the entry threshold at 90 and 95 vs 100. The threshold is
lowered to the LOWEST level that, in BOTH periods, keeps the filtered book's
PF >= 1.10, no more than 0.05 below the filtered book at 100, with more
trades. Otherwise it stays at 100.

    python -m scalp_lean_filter_compare --fetch-days 60 [--end-days-ago 135]
    python -m scalp_lean_filter_compare --thresholds --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_div_gate_compare as sdg
import scalp_live as sl
import scalp_reversal_compare as rc
import scalp_score_compare as ssc
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
DAY_MS = 24 * HOUR_MS
THRESHOLD = 100.0
MIN_TRADES, MIN_PF, MIN_PF_GAIN = 30, 1.10, 0.10
BOOKS = ("baseline", "no-against", "agree-only")


def lean_of(leans: Dict[str, Dict[int, str]], slot_ms: int) -> Dict[str, Optional[str]]:
    """The previous closed day's leans (known at the time of the scalp)."""
    day = int(slot_ms) // DAY_MS * DAY_MS - DAY_MS
    return {k: (v or {}).get(day) for k, v in leans.items()}


def keep(book: str, direction: str, ln: Dict[str, Optional[str]]) -> bool:
    sides = [s for s in ln.values() if s]
    against = any(s != direction for s in sides)
    agree = any(s == direction for s in sides)
    if book == "baseline":
        return True
    if book == "no-against":
        return not against
    return agree and not against


def _hours_to_close(slot_ms: int) -> float:
    t = datetime.fromtimestamp(slot_ms / 1000, tz=timezone.utc)
    close = t.replace(hour=sl.SESSION_END_H, minute=0, second=0, microsecond=0)
    return max(0.25, (close - t).total_seconds() / 3600)


def book(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], *, sim: Callable = ee.simulate_hl) -> List[Dict]:
    """sdg.book with the session: entries only in session, flat at 21:00 UTC."""
    trades, open_until = [], {}
    idx = {s: {int(b["timestamp"]) + M15: i for i, b in enumerate(bars)} for s, bars in m15.items()}
    piv = {s: sf.pivots(bars) for s, bars in m15.items()}
    for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["score"])):
        t0, sym = c["slot_ms"], c["symbol"]
        if not sl.in_session(t0):
            continue
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
                max_hold_hours=_hours_to_close(t0))
        if not t.get("taken"):
            continue
        notional = sl.DEFAULT_RISK_USD / stop_pct
        pnl = round(t["net_pct"] / 100 * notional, 4)
        t.update(usd=notional, pnl_usd=pnl, pnl_risk=pnl, notional_risk=notional,
                 lev_risk=sl.DEFAULT_SCALP_LEVERAGE)
        trades.append(t)
        open_until[sym] = sc.busy_until(t)
    return trades


def evaluate(scored: Sequence[Dict], m15, leans, span, *, sim: Callable = ee.simulate_hl,
             alts_only: bool = False) -> Dict:
    pool = [x for x in scored if x["score"] >= THRESHOLD and (not alts_only or x["symbol"] != "BTC")]
    out = {}
    for b in BOOKS:
        sel = [x for x in pool if keep(b, x["direction"], lean_of(leans, x["slot_ms"]))]
        m = ss.metrics(book(sel, m15, sim=sim), span)
        m["per_day"] = round((m.get("trades") or 0) / span, 1) if span else None
        out[b] = m
    return out


LOW_THRESHOLDS = (90.0, 95.0, 100.0)
MAX_PF_DROP = 0.05


def threshold_rows(scored: Sequence[Dict], m15, leans, span, *, sim: Callable = ee.simulate_hl) -> Dict:
    """The agree-only book at each threshold in LOW_THRESHOLDS."""
    out = {}
    for th in LOW_THRESHOLDS:
        sel = [x for x in scored if x["score"] >= th
               and keep("agree-only", x["direction"], lean_of(leans, x["slot_ms"]))]
        m = ss.metrics(book(sel, m15, sim=sim), span)
        m["per_day"] = round((m.get("trades") or 0) / span, 1) if span else None
        out[th] = m
    return out


def threshold_verdict(rows: Dict) -> Dict:
    """The lowest threshold that keeps PF >= MIN_PF, within MAX_PF_DROP of 100's,
    with more trades - in THIS period (both must agree)."""
    ref = rows[100.0]
    ref_pf, ref_n = ref.get("pf_usd") or 0, ref.get("trades") or 0
    for th in LOW_THRESHOLDS[:-1]:
        r = rows[th]
        pf = r.get("pf_usd") or 0
        if pf >= MIN_PF and pf >= ref_pf - MAX_PF_DROP and (r.get("trades") or 0) > ref_n:
            return {"lowest_ok": th}
    return {"lowest_ok": 100.0}


def verdict(rows: Dict) -> Dict:
    base_pf = rows["baseline"].get("pf_usd") or 0
    for b in ("no-against", "agree-only"):
        r = rows[b]
        pf = r.get("pf_usd") or 0
        if (r.get("trades") or 0) >= MIN_TRADES and pf >= MIN_PF and pf - base_pf >= MIN_PF_GAIN:
            return {"passes": True, "book": b}
    return {"passes": False, "book": None}


def compare(m15, leans, *, start_ms: int, end_ms: int, log=print, sim: Callable = ee.simulate_hl,
            thresholds: bool = False) -> Dict:
    span = (end_ms - start_ms) / DAY_MS
    struct = ssc.structure_at(m15, start_ms=start_ms)
    cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, build=sdg._build_gated,
                            rev=sl.reversal_reads)
    scored = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        scored.append({**c, "score": ssc.proposed_score(c["strength"], kind, c["reads"], h1)})
    if thresholds:
        trows = threshold_rows(scored, m15, leans, span, sim=sim)
        return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
                "coins": sorted(m15), "thresholds": trows, "threshold_verdict": threshold_verdict(trows)}
    rows = evaluate(scored, m15, leans, span, sim=sim)
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows, "alts": evaluate(scored, m15, leans, span, sim=sim,
                                                                 alts_only=True),
            "verdict": verdict(rows)}


def _row(r: Dict) -> str:
    if not r.get("trades"):
        return "no trades"
    return (f"{r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · PF {r['pf_usd']} · "
            f"${r['pnl_usd']} (max DD ${r['max_dd_usd']})")


def render_telegram(res: Dict) -> str:
    w = res["window"]
    v = res["verdict"]
    lines = ["🧭 15m scalp + the DAILY money lean as a filter (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins · score ≥ "
             f"{THRESHOLD:g} · session Mon–Fri {sl.SESSION_START_H}:00–{sl.SESSION_END_H}:00 UTC, flat at the "
             f"close · live exits · ${sl.DEFAULT_RISK_USD:g} risk",
             "Lean = the previous closed day's Coinbase premium / stablecoin supply lean", ""]
    for b in BOOKS:
        lines.append(f"• {b}: {_row(res['rows'][b])}")
    lines.append("")
    lines.append("Alts only (context):")
    for b in BOOKS:
        lines.append(f"  {b}: {_row(res['alts'][b])}")
    lines.append("")
    lines.append(f"This period: {'filter WORKS here ✅ (' + v['book'] + ')' if v['passes'] else 'filter does not pass ❌'}")
    lines.append(f"Rule fixed before the run: no-against (else agree-only) needs {MIN_TRADES}+ trades, PF ≥ "
                 f"{MIN_PF:g} and ≥ {MIN_PF_GAIN:g} above the baseline, in BOTH periods (end_days_ago 0 and 135).")
    return "\n".join(lines)


def render_thresholds(res: Dict) -> str:
    w = res["window"]
    lo = res["threshold_verdict"]["lowest_ok"]
    lines = ["🎚️ 15m scalp with the lean filter: can the threshold come down? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins · agree-only "
             f"filter · session · live exits · ${sl.DEFAULT_RISK_USD:g} risk", ""]
    for th, r in res["thresholds"].items():
        lines.append(f"• {th:g}+: {_row(r)}")
    lines.append("")
    lines.append(f"This period: lowest threshold that passes = {lo:g}")
    lines.append(f"Rule fixed before the run: the lowest of 90 / 95 with PF ≥ {MIN_PF:g}, no more than "
                 f"{MAX_PF_DROP:g} below 100's PF and more trades - in BOTH periods (end_days_ago 0 and 135); "
                 "else it stays at 100.")
    return "\n".join(lines)


def leans_by_day(btc_daily: Sequence[Dict]) -> Dict[str, Dict[int, str]]:
    import macro_lean_test as ml
    btc = {int(c["timestamp"]) // DAY_MS * DAY_MS: float(c["close"]) for c in btc_daily}
    out: Dict[str, Dict[int, str]] = {"premium": {}, "stable": {}}
    if btc:
        try:
            out["premium"] = ml.premium_lean(ml.fetch_coinbase_daily(min(btc), max(btc) + DAY_MS), btc)
        except Exception:
            pass
    try:                                    # needs no BTC candles
        out["stable"] = ml.stable_lean(ml.fetch_stable_supply())
    except Exception:
        pass
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_lean_filter_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--thresholds", action="store_true")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    import continuation_short_confirm as cs
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * DAY_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    # The same daily fetch macro_lean_test used (the first run's read_study
    # fetch came back empty: no lean days, so the books were all the same).
    daily = cs.fetch_deep({"BTC": appmod.SYMBOLS["BTC"]}, int(args.fetch_days + args.end_days_ago + 60), log=log)
    leans = leans_by_day(daily.get("BTC") or [])
    log(f"  lean days: premium {len(leans['premium'])}, stable {len(leans['stable'])}")
    if not (leans["premium"] or leans["stable"]):
        print("error: no lean data - the comparison would be meaningless", file=sys.stderr)
        return 3
    res = compare(m15, leans, start_ms=end_ms - int(args.fetch_days * DAY_MS), end_ms=end_ms, log=log,
                  thresholds=args.thresholds)
    render = render_thresholds if args.thresholds else render_telegram
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render(res)), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
