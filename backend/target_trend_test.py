"""
A WITH-TREND high-chance pattern reaches its target: does price now reverse?
A pre-registered test (written Nov 17 2026, before any run).

The idea: in a downtrend, a bearish high-chance pattern that has reached its
target has finished its move and is unlikely to go much further - so the
short side should lose strength and the long side gain it (and the mirror:
in an uptrend, a bullish one reaching its target should move strength from
long to short).

target_hit_test answered a broader question (every target hit, with and
against the trend mixed) and found continuation in the older years and
reversal in the last one. This asks the exact case: only the hits where the
pattern pointed WITH the trend.

Same replay as target_hit_test (live detectors, 4H 720 days, 1D / 1W 2000
days; high-chance = 1D reversal patterns + bear flag 4H, triple top 4H, H&S
4H, double top 1D, falling wedge 1W; at the close of the candle that reached
the target, the same-direction and the opposite trade, 1:1 at 1.5 x ATR, max
20 candles, 0.13% costs; one per coin per chart per 20 candles).
  TREND        that chart's close vs its 200 EMA at the target-hit candle
  WITH TREND   a bearish pattern below the 200 EMA, a bullish one above it

THE REVERSAL RULE IS SUPPORTED only if, for the with-trend high-chance target
hits, in BOTH periods (older / the last 365 days): 30+ resolved, the
OPPOSITE (reversal) trade wins >= 53% with z >= 2 and avg R > 0. Then the
points are sized from the measured edge. Otherwise the scoring stays.
Context: counter-trend hits; every pattern type with the trend; each chart.
Coins move together, so z overstates certainty.

    python -m target_trend_test --days 2000 --h4-days 720
"""
from __future__ import annotations

import sys
from typing import Dict, List, Sequence

import reversal_1d_test as rv
import target_hit_test as th

DAY_MS = th.DAY_MS
DECIDES = "WITH-TREND high-chance target hits (decides)"


def groups(evs: Sequence[Dict]) -> Dict[str, List[Dict]]:
    hc = [e for e in evs if e["high_chance"]]
    out = {DECIDES: [e for e in hc if e.get("with_trend") is True],
           "counter-trend high-chance hits (context)": [e for e in hc if e.get("with_trend") is False],
           "every pattern type, with the trend (context)": [e for e in evs if e.get("with_trend") is True]}
    for tf in ("4H", "1D"):
        out[f"with-trend high-chance on {tf} (context)"] = [e for e in hc if e.get("with_trend") is True
                                                             and e["tf"] == tf]
    for d, lbl in (("down", "bearish patterns in a downtrend"), ("up", "bullish patterns in an uptrend")):
        out[f"with-trend high-chance: {lbl} (context)"] = [e for e in hc if e.get("with_trend") is True
                                                            and e.get("trend") == d]
    return out


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    out = {}
    for name, ge in groups(evs).items():
        older = th.judge([e for e in ge if e["ts"] < cutoff_ms])
        recent = th.judge([e for e in ge if e["ts"] >= cutoff_ms])
        both = older["verdict"] == recent["verdict"] and older["verdict"] in ("REVERSAL", "CONTINUATION")
        out[name] = {"older": older, "recent": recent,
                     "answer": older["verdict"] if both else "no consistent edge"}
    return out


def render_telegram(res: Dict) -> str:
    s = res["summary"]
    lines = ["🎯📉 A WITH-TREND high-chance pattern hits its target: reverse now? (pre-registered)",
             f"{len(res['coins'])} coins · 4H {res['h4_days']}d, 1D/1W {res['days']}d · trend = the chart's "
             f"close vs its 200 EMA at the hit · split at {res['cutoff'][:10]}",
             "At the close of the target-hit candle: the reversal trade vs the continuation · 1:1 at "
             "1.5 × ATR · max 20 candles · 0.13% costs", ""]
    for name, f in s.items():
        lines.append(f"━━ {name}  →  {f['answer']}")
        for per in ("older", "recent"):
            p = f[per]
            lines.append(f"  {per}: {p['verdict']}")
            lines.append(f"    reversal (opposite):  {rv._s(p['opp'])}")
            lines.append(f"    continuation (same):  {rv._s(p['same'])}")
        lines.append("")
    ok = s[DECIDES]["answer"] == "REVERSAL"
    lines.append("Verdict: " + ("REVERSAL RULE SUPPORTED ✅ — after a with-trend high-chance target hit, move "
                                "strength from the trend side to the reversal side" if ok else
                                "NOT supported ❌ — leave the scoring alone"))
    lines.append(f"Rule fixed before the run: {rv.MIN_N}+ resolved; the reversal trade wins ≥ {rv.WIN_HI:g}% "
                 f"with z ≥ {rv.Z_MIN:g} and avg R > 0, in BOTH periods. Coins move together, so z "
                 "overstates certainty.")
    return "\n".join(lines)


def run(series: Dict[str, Dict[str, List[Dict]]], *, end_ms: int, log=print, **kw) -> Dict:
    import app as appmod
    evs = []
    for sym, by_tf in series.items():
        for tf, candles in by_tf.items():
            evs += th.scan(candles, tf, appmod.TF_LIMIT.get(tf, 240), sym, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff), "coins": sorted(series)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="target_trend_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--h4-days", type=int, default=720)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    daily = rs.fetch_daily(syms, args.days, log=log)
    h1 = cc.fetch_history(syms, args.h4_days, log=log) if args.h4_days else {}
    series = {}
    for s in syms:
        by = {}
        if s in h1:
            by["4H"] = cc.aggregate(h1[s]["1H"], 4)
        if s in daily:
            by["1D"] = daily[s]
            by["1W"] = rs.weekly_from_daily(daily[s])
        if by:
            series[s] = by
    res = run(series, end_ms=end_ms, log=log)
    res.update(days=args.days, h4_days=args.h4_days)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(series)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
