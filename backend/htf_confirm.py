"""
CONFIRMATION of the 4-hourly engine signal at strength >= 62 - a
pre-registered test (written Nov 10 2026, before any run of it).

Why: in htf_vs_random (end_days_ago 700 and 800) the pre-registered
">= 69" test failed, but the 4-hourly signal at ">= 62" beat every random
run in BOTH periods with PF 1.31 / 1.31 after costs. That row was picked
after seeing eight rows, so it needs its own test on periods nobody has
looked at. Both 2024 periods were mostly rising markets, so the signal must
also beat simply being LONG on the same entries.

The rule, fixed before running (nothing is tuned afterwards):
  signal   the live 4-hourly engine signal (1H + 2H agreeing, every 4 hours,
           portfolio_backtest.replay), every screened candidate at
           strength >= 62
  trades   exactly as htf_vs_random: entry at the slot, stop and target
           1.5 x ATR(14) of the 4H chart each (1:1), max 48h walked on 1H
           candles, $5 risk, costs as live, one position per coin
  PASS     on BOTH never-read periods (end_days_ago 900 and 1000): 30+
           trades, net profit after costs, PF >= 1.15, above the best 5% of
           the same entries with random directions (50 runs), AND more net $
           than "always LONG" on the same entries

Context rows (decide nothing): always LONG on the same entries, the longs
and the shorts on their own, and the long share of the signals.

    python -m htf_confirm --fetch-days 60 --end-days-ago 900
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional

import entry_exit_compare as ee
import htf_vs_random as hv
import scalp_baseline as sb
import scalp_score_vs_random as svr

FLOOR = 62.0
PERIODS = (900, 1000)
MIN_TRADES = 30
MIN_PF = 1.15
SIGNAL = ("4-hourly (live: 1H + 2H)", {"interval_hours": 4, "primary_tf": "2H"}, "4H", 48)
TEST = "4-hourly ≥ 62 (the test)"
ALWAYS_LONG = "always LONG, same entries (context)"


def as_long(c: Dict) -> Dict:
    """`c` traded LONG with the same stop / target distances."""
    if c["direction"] == "LONG":
        return dict(c)
    e = c["entry"]
    return {**c, "direction": "LONG", "sl": 2 * e - c["sl"], "target": 2 * e - c["target"]}


def compare(market: Dict, *, days: Optional[float] = None, correlations=None, log=print,
            seeds: int = svr.SEEDS, replay: Optional[Callable] = None,
            sim: Callable = ee.simulate_hl) -> Dict:
    import cadence_compare as cc
    import portfolio_backtest as pbt
    replay = replay or pbt.replay
    start = cc.common_start(market, days=days, warmup_hours=240)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + hv.HOUR_MS
    span = (end - start) / (24 * hv.HOUR_MS)
    label, cfg, atr_tf, hold = SIGNAL
    rep = replay(market, correlations=correlations or {}, start_ms=start, execute=False,
                 keep_candidates=True, keep_trades=False, reading_cache={}, **cfg)
    cands = [x for x in (hv.prepare(c, market, atr_tf) for c in rep.get("candidates") or [])
             if x and start <= x["slot_ms"] < end and x["strength"] >= FLOOR]
    log(f"  {label}: {len(cands)} candidates at {FLOOR:g}+")
    book = hv.make_book(market, hold, sim)
    variants = [(TEST, cands),
                (ALWAYS_LONG, [as_long(c) for c in cands]),
                ("its LONGS only (context)", [c for c in cands if c["direction"] == "LONG"]),
                ("its SHORTS only (context)", [c for c in cands if c["direction"] == "SHORT"])]
    rows = []
    for name, cs in variants:
        rows.append(svr.row(name, cs, book, span, seeds))
        log(f"    {name}: done")                         # no results in the public log
    longs = sum(1 for c in cands if c["direction"] == "LONG")
    return {"window": {"start": sb.sc._iso(start), "end": sb.sc._iso(end), "days": round(span, 1)},
            "coins": sorted(market), "rows": rows,
            "long_share_pct": round(100 * longs / len(cands)) if cands else None,
            "verdict": verdict(rows)}


def verdict(rows: List[Dict]) -> Dict:
    by = {r["label"]: r for r in rows}
    t, al = by[TEST], by[ALWAYS_LONG]
    checks = {
        f"{MIN_TRADES}+ trades": (t.get("trades") or 0) >= MIN_TRADES,
        "profit after costs": (t.get("pnl_usd") or 0) > 0,
        f"PF ≥ {MIN_PF:g}": (t.get("pf_usd") or 0) >= MIN_PF,
        "beats random": bool(t.get("beats_random")),
        "beats always-long": (t.get("pnl_usd") or 0) > (al.get("pnl_usd") or 0),
    }
    return {"checks": checks, "passes": all(checks.values())}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["✅❓ CONFIRMATION: 4-hourly engine signal at strength ≥ 62 (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             "Stop and target 1.5 × 4H ATR each (1:1), max 48h, $5 risk, costs as live, one per "
             "coin; 50 random-direction runs on the same entries",
             f"Signals that were LONG: {res['long_share_pct']}%", ""]
    for r in res["rows"]:
        lines += svr.render_row(r)
    v = res["verdict"]
    lines.append("Checks: " + " · ".join(f"{'✅' if ok else '❌'} {k}"
                                          for k, ok in v["checks"].items()))
    lines.append(f"This period: {'PASS ✅' if v['passes'] else 'FAIL ❌'}")
    lines.append(f"It counts only if it passes on BOTH end_days_ago {PERIODS[0]} and {PERIODS[1]}.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="htf_confirm", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * hv.HOUR_MS)) \
        // hv.HOUR_MS * hv.HOUR_MS
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
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
