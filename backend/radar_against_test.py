"""
Should a trade be skipped when the Reversal Radar fights it? A pre-registered
test (written Nov 15 2026, before any run).

Why: TAO showed SHORT 91/100 CONFIRMED while its Reversal Radar read
"bottoming ELEVATED 5/12" (RSI 26, Fear & Greed 10, capitulation volume,
-24.5% under the 50 EMA). Today the radar only nudges the strength by 4-8
points and the ranking by 8-15, so such trades still go out at full
strength. The question is whether they actually do worse.

Signal: the live 4-hourly engine signal (1H + 2H agreeing, every 4 hours,
portfolio_backtest.replay), every screened candidate at strength >= 62 - the
same set as the forward paper book. "Radar against" = the strongest Reversal
Radar level opposing the trade on the 2H or 4H chart (candidate field
reversal_against: a 'top' radar against a LONG, a 'bottom' one against a
SHORT) is ELEVATED or HIGH.

Trades exactly as htf_confirm: entry at the slot, stop and target 1.5 x
ATR(14) of the 4H chart each (1:1), max 48h walked on 1H candles, $5 risk,
costs as live, one position per coin; 50 random-direction runs per row.

Decided before running, on BOTH never-read periods (90 days each, end
days ago 0 and 180):
  SKIP RULE SUPPORTED if every check holds in both:
    - 30+ trades with the radar against
    - those trades lose money after costs
    - their profit factor is below the trades without the radar against
    - the book WITHOUT them makes more net $ than the book with everything
Context rows (decide nothing): HIGH only; the radar-against trades REVERSED
(if those beat random, the radar is a contrarian signal worth more than a
skip); longs and shorts split.

    python -m radar_against_test --fetch-days 90 --end-days-ago 0
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional

import entry_exit_compare as ee
import htf_vs_random as hv
import scalp_baseline as sb
import scalp_score_vs_random as svr

FLOOR = 62.0
PERIODS = (0, 180)
MIN_AGAINST = 30
AGAINST = ("elevated", "high")
SIGNAL = ({"interval_hours": 4, "primary_tf": "2H"}, "4H", 48)
ALL = "every trade at 62+ (as now)"
WITH = "radar AGAINST the trade (elevated / high)"
WITHOUT = "without radar against (the skip rule)"


def _rev(c: Dict) -> Dict:
    e = c["entry"]
    return {**svr.reverse(c), "sl": 2 * e - c["sl"], "target": 2 * e - c["target"]}


def candidates(rep: Dict, market: Dict, start: int, end: int) -> List[Dict]:
    out = []
    for c in rep.get("candidates") or []:
        x = hv.prepare(c, market, SIGNAL[1])
        if x and start <= x["slot_ms"] < end and x["strength"] >= FLOOR:
            x["rev"] = str(c.get("reversal_against") or "").lower() or None
            out.append(x)
    return out


def compare(market: Dict, *, days: Optional[float] = None, correlations=None, log=print,
            seeds: int = svr.SEEDS, replay: Optional[Callable] = None,
            sim: Callable = ee.simulate_hl) -> Dict:
    import cadence_compare as cc
    import portfolio_backtest as pbt
    replay = replay or pbt.replay
    start = cc.common_start(market, days=days, warmup_hours=240)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + hv.HOUR_MS
    span = (end - start) / (24 * hv.HOUR_MS)
    cfg, _tf, hold = SIGNAL
    rep = replay(market, correlations=correlations or {}, start_ms=start, execute=False,
                 keep_candidates=True, keep_trades=False, reading_cache={}, **cfg)
    cands = candidates(rep, market, start, end)
    against = [c for c in cands if c["rev"] in AGAINST]
    log(f"  {len(cands)} candidates at {FLOOR:g}+")
    book = hv.make_book(market, hold, sim)
    variants = [(ALL, cands), (WITH, against),
                (WITHOUT, [c for c in cands if c["rev"] not in AGAINST]),
                ("radar against HIGH only (context)", [c for c in cands if c["rev"] == "high"]),
                ("radar-against trades REVERSED (context)", [_rev(c) for c in against]),
                ("radar against, LONGS (context)", [c for c in against if c["direction"] == "LONG"]),
                ("radar against, SHORTS (context)", [c for c in against if c["direction"] == "SHORT"])]
    rows = []
    for name, cs in variants:
        rows.append(svr.row(name, cs, book, span, seeds))
        log(f"    {name}: done")                         # no results in the public log
    levels: Dict[str, int] = {}
    for c in cands:
        levels[c["rev"] or "none"] = levels.get(c["rev"] or "none", 0) + 1
    return {"window": {"start": sb.sc._iso(start), "end": sb.sc._iso(end), "days": round(span, 1)},
            "coins": sorted(market), "rows": rows, "levels": levels, "verdict": verdict(rows)}


def verdict(rows: List[Dict]) -> Dict:
    by = {r["label"]: r for r in rows}
    a, w, wo = by[ALL], by[WITH], by[WITHOUT]
    checks = {
        f"{MIN_AGAINST}+ radar-against trades": (w.get("trades") or 0) >= MIN_AGAINST,
        "radar-against trades lose money": (w.get("pnl_usd") or 0) < 0,
        "their PF below the rest": (w.get("pf_usd") or 0) < (wo.get("pf_usd") or 0),
        "skipping them makes more $": (wo.get("pnl_usd") or 0) > (a.get("pnl_usd") or 0),
    }
    return {"checks": checks, "passes": all(checks.values())}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lv = res["levels"]
    lines = ["🧭🛑 Reversal Radar AGAINST the trade: skip it? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             "Live 4-hourly signal at strength ≥ 62 · stop and target 1.5 × 4H ATR (1:1), max 48h, "
             "$5 risk, costs as live, one per coin; 50 random-direction runs per row",
             "Radar level against the trade: " + " · ".join(
                 f"{k} {lv.get(k, 0)}" for k in ("none", "low", "building", "elevated", "high")), ""]
    for r in res["rows"]:
        lines += svr.render_row(r)
    v = res["verdict"]
    lines.append("Checks: " + " · ".join(f"{'✅' if ok else '❌'} {k}" for k, ok in v["checks"].items()))
    lines.append(f"This period: {'SKIP RULE SUPPORTED ✅' if v['passes'] else 'NOT SUPPORTED ❌'}")
    lines.append(f"It counts only if supported on BOTH end_days_ago {PERIODS[0]} and {PERIODS[1]}.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="radar_against_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import htf_forward
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * hv.HOUR_MS)) \
        // hv.HOUR_MS * hv.HOUR_MS
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    core = [s for s in appmod.SCAN_SYMBOLS if s in htf_forward.UNIVERSE]   # the forward book's 30
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, days=args.fetch_days, correlations=appmod._BTC_CORR, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
