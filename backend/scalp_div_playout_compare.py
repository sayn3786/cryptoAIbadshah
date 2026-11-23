"""
15m scalp: should a PLAYED-OUT confirmed RSI divergence stop scoring? A
pre-registered test (written Nov 23 2026, before any run).

On 1D, a played-out confirmed divergence scoring 0 passed both eras
(divergence_playout_test) and is live. The scalp only trades 100+ scores,
so taking points out changes which scalps qualify - checked on the replay
that passed (scalp_lean_filter_compare): 100+, the daily money-lean filter
(agree-only), the session, the engine's exits, $10 risk.

  current   the 15m score as live
  zeroed    the same with signals' div_zero_played_out switch on

ADOPTED for the scalp only if, in BOTH periods (--end-days-ago 0 and 135),
the zeroed book has 30+ trades, a profit factor >= 1.10 AND at least the
current book's. Otherwise the scalp keeps its score.

    python -m scalp_div_playout_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict

import candle_analysis
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_lean_filter_compare as slf
import scalp_live as sl
import scalp_score_compare as ssc
import scalp_struct_compare as ss

DAY_MS = slf.DAY_MS
MIN_TRADES, MIN_PF = 30, 1.10


def _build_current(win, tf, sym):
    a = candle_analysis.build_candle_analysis(list(win), tf, sym)
    if tf == "15M":
        a["div_need_extreme"] = True
    return a


def _build_zeroed(win, tf, sym):
    a = _build_current(win, tf, sym)
    if tf == "15M":
        a["div_zero_played_out"] = True
    return a


def book_for(m15, leans, struct, *, start_ms, span, build, log=print) -> Dict:
    cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, build=build, rev=sl.reversal_reads)
    pool = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        score = ssc.proposed_score(c["strength"], kind, c["reads"], h1)
        if score >= slf.THRESHOLD and slf.keep("agree-only", c["direction"], slf.lean_of(leans, c["slot_ms"])):
            pool.append({**c, "score": score})
    m = ss.metrics(slf.book(pool, m15), span)
    m["per_day"] = round((m.get("trades") or 0) / span, 1) if span else None
    return m


def compare(m15, leans, *, start_ms, end_ms, log=print) -> Dict:
    span = (end_ms - start_ms) / DAY_MS
    struct = ssc.structure_at(m15, start_ms=start_ms)
    rows = {name: book_for(m15, leans, struct, start_ms=start_ms, span=span, build=b, log=log)
            for name, b in (("current", _build_current), ("zeroed", _build_zeroed))}
    c, z = rows["current"], rows["zeroed"]
    ok = ((z.get("trades") or 0) >= MIN_TRADES and (z.get("pf_usd") or 0) >= MIN_PF
          and (z.get("pf_usd") or 0) >= (c.get("pf_usd") or 0))
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows, "passes": ok}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["🔁 15m scalp: zero played-out confirmed divergences? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins · the live scalp: "
             "100+, lean filter, session, engine exits, $10 risk", ""]
    for name in ("current", "zeroed"):
        lines.append(f"• {name}: {slf._row(res['rows'][name])}")
    lines.append("")
    lines.append(f"This period: {'zeroed passes ✅' if res['passes'] else 'zeroed does not pass ❌'}")
    lines.append(f"Rule fixed before the run: zeroed needs {MIN_TRADES}+ trades, PF ≥ {MIN_PF:g} and ≥ the current "
                 "book's, in BOTH periods (end_days_ago 0 and 135).")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_div_playout_compare", description=__doc__.split("\n\n")[0])
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
