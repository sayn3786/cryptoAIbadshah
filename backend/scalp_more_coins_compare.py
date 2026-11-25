"""
15m scalp: add more coins? A pre-registered test (written Nov 25 2026, before
any run).

The live scalp scores a fixed list of 18 coins (scalp_live.SYMBOLS); every
scalp replay so far used the 14 of them Hyperliquid testnet prices sanely
(scalp_chop_compare.COINS). The owner asks to scan more. The candidates are
the 1D scan coins NOT on the scalp list (app.SCAN_SYMBOLS minus
scalp_live.SYMBOLS, minus PAXG - gold), whichever have 15m history.

Both groups are replayed exactly as the live scalp trades: score 100+, the
daily money-lean filter (agree-only), the engine's exits, $10 risk, one
position per coin, entries Mon-Fri 13:00-21:00 UTC, flat at 21:00.
  current   the scalp's tested 14 coins (reference)
  new       the candidate coins

THE NEW COINS ARE ADDED only if, in BOTH periods (--end-days-ago 0 and 135,
60 days each), the new-coins book has 30+ trades and a profit factor >= 1.10
(the bar the lean filter and the Asia test used). Then each added coin must
also be listed on Hyperliquid testnet at a sane price (live MAX_BASIS check
skips it otherwise). The live book's 4-open cap is unchanged, so added coins
compete for the same slots.

    python -m scalp_more_coins_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List

import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_div_gate_compare as sdg
import scalp_lean_filter_compare as slf
import scalp_live as sl
import scalp_score_compare as ssc
import scalp_struct_compare as ss

DAY_MS = slf.DAY_MS
LIVE = (sl.SESSION_START_H, sl.SESSION_END_H)
MIN_TRADES, MIN_PF = 30, 1.10
EXCLUDE = ("PAXG",)


def candidates(scan_symbols, scalp_symbols) -> List[str]:
    return [s for s in scan_symbols if s not in scalp_symbols and s not in EXCLUDE]


def pool(m15, leans, *, start_ms, log=print) -> List[Dict]:
    struct = ssc.structure_at(m15, start_ms=start_ms)
    cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, build=sdg._build_gated,
                            rev=sl.reversal_reads)
    out = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        score = ssc.proposed_score(c["strength"], kind, c["reads"], h1)
        if score >= slf.THRESHOLD and slf.keep("agree-only", c["direction"], slf.lean_of(leans, c["slot_ms"])):
            out.append({**c, "score": score})
    return out


def compare(groups: Dict[str, Dict[str, List[Dict]]], leans, *, start_ms, end_ms, log=print) -> Dict:
    span = (end_ms - start_ms) / DAY_MS
    rows, per_coin = {}, {}
    for name, m15 in groups.items():
        p = pool(m15, leans, start_ms=start_ms, log=log)
        trades = slf.book(p, m15, window=LIVE)
        m = ss.metrics(trades, span)
        m["per_day"] = round((m.get("trades") or 0) / span, 1) if span else None
        rows[name] = m
        if name == "new":
            for sym in sorted(m15):
                mine = [t for t in trades if t.get("symbol") == sym]
                if mine:
                    per_coin[sym] = ss.metrics(mine, span)
    n = rows.get("new") or {}
    ok = (n.get("trades") or 0) >= MIN_TRADES and (n.get("pf_usd") or 0) >= MIN_PF
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": {k: sorted(v) for k, v in groups.items()}, "rows": rows, "per_coin": per_coin, "passes": ok}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    L = ["➕ 15m scalp: add more coins? (pre-registered)",
         f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · 100+, lean filter, engine exits, $10 risk, "
         "Mon–Fri 13–21 UTC", ""]
    L.append(f"• current {len(res['coins']['current'])} coins: {slf._row(res['rows']['current'])}")
    L.append(f"• new {len(res['coins']['new'])} coins: {slf._row(res['rows']['new'])}")
    L.append(f"  new coins replayed: {', '.join(res['coins']['new'])}")
    if res["per_coin"]:
        best = sorted(res["per_coin"].items(), key=lambda kv: -(kv[1].get("trades") or 0))[:12]
        L.append("  by coin (trades · PF): " + " · ".join(
            f"{s} {m.get('trades')}·{m.get('pf_usd')}" for s, m in best))
    L.append("")
    L.append(f"This period: {'new coins pass ✅' if res['passes'] else 'new coins do not pass ❌'}")
    L.append(f"Rule fixed before the run: the new coins need {MIN_TRADES}+ trades and PF ≥ {MIN_PF:g} in BOTH "
             "periods (end_days_ago 0 and 135). Per-coin lines are context, not the rule.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_more_coins_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    import continuation_short_confirm as cs
    now = int(time.time() * 1000) // sc.M15 * sc.M15
    end_ms = now - int(args.end_days_ago * DAY_MS) // sc.M15 * sc.M15
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    cur = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    new = {s: appmod.SYMBOLS[s] for s in candidates(appmod.SCAN_SYMBOLS, sl.SYMBOLS) if s in appmod.SYMBOLS}
    groups = {"current": sc.fetch_15m(cur, args.fetch_days, end_ms=end_ms, log=log),
              "new": sc.fetch_15m(new, args.fetch_days, end_ms=end_ms, log=log)}
    if not groups["new"]:
        print("error: no 15m history for the new coins", file=sys.stderr)
        return 2
    daily = cs.fetch_deep({"BTC": appmod.SYMBOLS["BTC"]}, int(args.fetch_days + args.end_days_ago + 60), log=log)
    leans = slf.leans_by_day(daily.get("BTC") or [])
    if not (leans["premium"] or leans["stable"]):
        print("error: no lean data - the comparison would be meaningless", file=sys.stderr)
        return 3
    res = compare(groups, leans, start_ms=end_ms - int(args.fetch_days * DAY_MS), end_ms=end_ms, log=log)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {sum(len(v) for v in groups.values())}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
