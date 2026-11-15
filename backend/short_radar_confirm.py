"""
CONFIRMATION (pre-registered, written Nov 15 2026 before any run): are SHORTS
taken while the Reversal Radar reads "bottoming" (elevated / high) worse than
other shorts? And is the 4-hourly signal itself still worse than random?

Why: in radar_against_test (periods ending 0 and 180 days ago) the shorts
with a bottoming radar against them were among the worst trades in both
periods (PF 0.54 and 0.46, below every or almost every random run), while
the longs with a topping radar against them were fine. That split was read
AFTER seeing the data, so it is tested here on the two 90-day windows nobody
has looked at: ending 90 and 270 days ago. The same runs showed every trade
at 62+ worse than random (2% and 0% of random runs), against PF 1.12-1.31 in
the 2024 tests - check B measures that on the new windows.

Signal and trades exactly as radar_against_test / htf_confirm: the live
4-hourly signal (1H + 2H, every 4 hours) at strength >= 62, 30-coin forward
universe; stop and target 1.5 x 4H ATR (1:1), max 48h on 1H candles, $5
risk, costs as live, one position per coin, 50 random-direction runs.

  A  RULE "skip shorts into a bottoming radar" CONFIRMED if, in BOTH windows:
       - 30+ such shorts traded
       - they lose money after costs
       - their profit factor is at least 0.15 below the other shorts'
       - the book without them makes more net $ than the book with all
  B  SIGNAL WORSE THAN RANDOM in a window if every trade at 62+ makes less
     net $ than the worst 5% of the same entries with random directions.
     In both windows -> give the forward paper book its verdict early.

    python -m short_radar_confirm --fetch-days 90 --end-days-ago 90
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional

import entry_exit_compare as ee
import htf_vs_random as hv
import radar_against_test as rt
import scalp_baseline as sb
import scalp_score_vs_random as svr

PERIODS = (90, 270)
MIN_TRADES = 30
PF_GAP = 0.15
ALL = "every trade at 62+ (B: signal vs random)"
TEST = "SHORTS with a bottoming radar (A: the test)"
OTHER = "other SHORTS (no bottoming radar)"
SKIP = "every trade WITHOUT those shorts (the rule)"


def compare(market: Dict, *, days: Optional[float] = None, correlations=None, log=print,
            seeds: int = svr.SEEDS, replay: Optional[Callable] = None,
            sim: Callable = ee.simulate_hl) -> Dict:
    import cadence_compare as cc
    import portfolio_backtest as pbt
    replay = replay or pbt.replay
    start = cc.common_start(market, days=days, warmup_hours=240)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + hv.HOUR_MS
    span = (end - start) / (24 * hv.HOUR_MS)
    cfg, _tf, hold = rt.SIGNAL
    rep = replay(market, correlations=correlations or {}, start_ms=start, execute=False,
                 keep_candidates=True, keep_trades=False, reading_cache={}, **cfg)
    cands = rt.candidates(rep, market, start, end)
    shorts = [c for c in cands if c["direction"] == "SHORT"]
    bad = [c for c in shorts if c["rev"] in rt.AGAINST]
    log(f"  {len(cands)} candidates at {rt.FLOOR:g}+")
    book = hv.make_book(market, hold, sim)
    variants = [(ALL, cands), (TEST, bad),
                (OTHER, [c for c in shorts if c["rev"] not in rt.AGAINST]),
                (SKIP, [c for c in cands if not (c["direction"] == "SHORT" and c["rev"] in rt.AGAINST)]),
                ("all SHORTS (context)", shorts),
                ("all LONGS (context)", [c for c in cands if c["direction"] == "LONG"])]
    rows = []
    for name, cs in variants:
        rows.append(svr.row(name, cs, book, span, seeds))
        log(f"    {name}: done")                         # no results in the public log
    return {"window": {"start": sb.sc._iso(start), "end": sb.sc._iso(end), "days": round(span, 1)},
            "coins": sorted(market), "rows": rows, "verdict": verdict(rows)}


def verdict(rows: List[Dict]) -> Dict:
    by = {r["label"]: r for r in rows}
    a, t, o, s = by[ALL], by[TEST], by[OTHER], by[SKIP]
    checks = {
        f"{MIN_TRADES}+ such shorts": (t.get("trades") or 0) >= MIN_TRADES,
        "they lose money": (t.get("pnl_usd") or 0) < 0,
        f"PF ≥ {PF_GAP:g} below other shorts": (t.get("pf_usd") or 0) <= (o.get("pf_usd") or 0) - PF_GAP,
        "skipping them makes more $": (s.get("pnl_usd") or 0) > (a.get("pnl_usd") or 0),
    }
    rnd = a.get("random") or {}
    worse = bool(a.get("trades") and rnd.get("net_p05") is not None
                 and (a.get("pnl_usd") or 0) < rnd["net_p05"])
    return {"checks": checks, "rule": all(checks.values()), "signal_worse_than_random": worse}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["🧭🛑 CONFIRMATION: skip shorts into a bottoming radar? + is the 4-hourly signal "
             "still worse than random? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             "Live 4-hourly signal at strength ≥ 62 · stop and target 1.5 × 4H ATR (1:1), max 48h, "
             "$5 risk, costs as live, one per coin; 50 random-direction runs per row", ""]
    for r in res["rows"]:
        lines += svr.render_row(r)
    v = res["verdict"]
    lines.append("A checks: " + " · ".join(f"{'✅' if ok else '❌'} {k}" for k, ok in v["checks"].items()))
    lines.append(f"A this window: {'RULE HOLDS ✅' if v['rule'] else 'RULE FAILS ❌'}")
    lines.append(f"B this window: signal {'WORSE than random ⚠️' if v['signal_worse_than_random'] else 'not worse than random'}")
    lines.append(f"Each counts only if it holds in BOTH windows (end_days_ago {PERIODS[0]} and {PERIODS[1]}).")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="short_radar_confirm", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=90)
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
    core = [s for s in appmod.SCAN_SYMBOLS if s in htf_forward.UNIVERSE]
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
