"""
15m scalp: trade the ASIA session too? A pre-registered test (written Nov 23
2026, before any run).

The live scalp trades Mon-Fri 13:00-21:00 UTC (London afternoon + New York),
where the daily money-lean filter passed in both periods
(scalp_lean_filter_compare). The owner asks to add Asia. Asia hours carry the
least BTC volume of the day, which tends to mean more chop for a 15m scalp,
so it is replayed first, exactly as the live scalp trades: 100+, the lean
filter (agree-only), the engine's exits, $10 risk, one position per coin.

  asia       entries Mon-Fri 00:00-08:00 UTC (Tokyo / Singapore day), flat at 08:00
  live       entries Mon-Fri 13:00-21:00 UTC, flat at 21:00 (reference)

ASIA IS ENABLED only if, in BOTH periods (--end-days-ago 0 and 135), the
asia book has 30+ trades and a profit factor >= 1.10. Otherwise the scalp
keeps trading only 13:00-21:00 UTC.

    python -m scalp_asia_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict

import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_div_gate_compare as sdg
import scalp_lean_filter_compare as slf
import scalp_live as sl
import scalp_score_compare as ssc
import scalp_struct_compare as ss

DAY_MS = slf.DAY_MS
ASIA = (0, 8)
LIVE = (sl.SESSION_START_H, sl.SESSION_END_H)
MIN_TRADES, MIN_PF = 30, 1.10


def compare(m15, leans, *, start_ms, end_ms, log=print) -> Dict:
    span = (end_ms - start_ms) / DAY_MS
    struct = ssc.structure_at(m15, start_ms=start_ms)
    cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, build=sdg._build_gated,
                            rev=sl.reversal_reads)
    pool = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        score = ssc.proposed_score(c["strength"], kind, c["reads"], h1)
        if score >= slf.THRESHOLD and slf.keep("agree-only", c["direction"], slf.lean_of(leans, c["slot_ms"])):
            pool.append({**c, "score": score})
    rows = {}
    for name, w in (("asia", ASIA), ("live", LIVE)):
        m = ss.metrics(slf.book(pool, m15, window=w), span)
        m["per_day"] = round((m.get("trades") or 0) / span, 1) if span else None
        rows[name] = m
    a = rows["asia"]
    ok = (a.get("trades") or 0) >= MIN_TRADES and (a.get("pf_usd") or 0) >= MIN_PF
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows, "passes": ok}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["🌏 15m scalp: trade the ASIA session too? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins · 100+, lean "
             "filter, engine exits, $10 risk, Mon–Fri", ""]
    lines.append(f"• Asia 00:00–08:00 UTC: {slf._row(res['rows']['asia'])}")
    lines.append(f"• Live 13:00–21:00 UTC (reference): {slf._row(res['rows']['live'])}")
    lines.append("")
    lines.append(f"This period: {'Asia passes ✅' if res['passes'] else 'Asia does not pass ❌'}")
    lines.append(f"Rule fixed before the run: Asia needs {MIN_TRADES}+ trades and PF ≥ {MIN_PF:g} in BOTH periods "
                 "(end_days_ago 0 and 135).")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_asia_compare", description=__doc__.split("\n\n")[0])
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
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    daily = cs.fetch_deep({"BTC": appmod.SYMBOLS["BTC"]}, int(args.fetch_days + args.end_days_ago + 60), log=log)
    leans = slf.leans_by_day(daily.get("BTC") or [])
    if not (leans["premium"] or leans["stable"]):
        print("error: no lean data - the comparison would be meaningless", file=sys.stderr)
        return 3
    res = compare(m15, leans, start_ms=end_ms - int(args.fetch_days * DAY_MS), end_ms=end_ms, log=log)
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
