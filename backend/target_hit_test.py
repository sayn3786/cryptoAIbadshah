"""
After a HIGH-CHANCE pattern reaches its target: does price reverse, or keep
going? A pre-registered test (written Nov 16 2026, before any run).

The idea to test: once a flag / pattern has hit its target, the move it
called is used up, so the signal should lose strength in that direction and
gain it in the opposite one. Only the high-chance patterns count (what the
dashboard now shows by default):
  - 1D reversal patterns (double / triple top and bottom, head & shoulders,
    inverse) - the tested edge
  - pattern types whose track record beat chance: bear flag 4H, triple top
    4H, head & shoulders 4H, double top 1D, falling wedge 1W

Replay with the live detectors (pattern_outcome_study.detect: closed
candles, live lookbacks) on 4H (720 days) and 1D / 1W (2000 days). Every
confirmed breakout is walked forward as in the pattern study (entry at the
breakout close, stop where the pattern is wrong, max 50 candles); the ones
that reach their TARGET give an event on the candle that reached it. At that
candle's close two trades: the SAME direction as the pattern (continuation)
and the OPPOSITE (reversal) - 1:1 at 1.5 x ATR(14) of that chart, max 20
candles, stop first, 0.13% costs. At most one event per coin per chart per
20 candles.

Decided before running, on BOTH periods (older / the last 365 days):
  REVERSAL      30+ resolved, the opposite trade wins >= 53% with z >= 2 and
                avg R > 0  -> the idea holds: weaken the same direction and
                strengthen the opposite after a target hit
  CONTINUATION  the same-direction trade wins >= 53% with z >= 2 and avg R > 0
                -> the opposite rule
  otherwise     no edge either way: leave the scoring alone
Context (decides nothing): every pattern type; flags only; each chart.
Coins move together, so z overstates certainty.

    python -m target_hit_test --days 2000 --h4-days 720
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import pattern_outcome_study as po
import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
COOLDOWN = 20
REV_1D = ("double top", "double bottom", "triple top", "triple bottom", "head & shoulders",
          "inverse head & shoulders")
BEAT_CHANCE = {("bear flag", "4H"), ("triple top", "4H"), ("head & shoulders", "4H"),
               ("double top", "1D"), ("falling wedge", "1W")}


def high_chance(ptype: str, tf: str) -> bool:
    return (tf == "1D" and ptype in REV_1D) or (ptype, tf) in BEAT_CHANCE


def scan(candles: Sequence[Dict], tf: str, lookback: int, sym: str = "", *,
         detect_fn: Callable = po.detect) -> List[Dict]:
    """Every target hit of a confirmed breakout on `candles`, with the
    continuation and the reversal trade from that candle's close."""
    out, seen, last_hit = [], set(), -10 ** 9
    for i in range(lookback // 2, len(candles)):
        window = list(candles[max(0, i + 1 - lookback):i + 1])
        for p in detect_fn(window, tf):
            ev = po.event_from(p, window, tf)
            if not ev or (ev["type"], ev["ts"]) in seen:
                continue
            seen.add((ev["type"], ev["ts"]))
            res = po.outcome(ev, candles[i + 1:])
            if res["result"] != "target":
                continue
            j = i + res["bars"]                      # the candle that reached the target
            if j >= len(candles) or j - last_hit < COOLDOWN:
                continue
            atr = rv._atr(candles[max(0, j - 15):j + 1])
            if not atr:
                continue
            last_hit = j
            same = "LONG" if ev["direction"] == "bullish" else "SHORT"
            opp = "SHORT" if same == "LONG" else "LONG"
            entry, after = float(candles[j]["close"]), candles[j + 1:]
            # the trend at the target hit: close vs the 200 EMA of this chart
            ema = po._ema([float(c["close"]) for c in candles[max(0, j - 599):j + 1]], 200)
            trend = None if ema is None else ("up" if entry > ema else "down")
            with_trend = None if trend is None else (trend == "up") == (same == "LONG")
            out.append({"symbol": sym, "tf": tf, "type": ev["type"], "ts": int(candles[j]["timestamp"]),
                        "trend": trend, "with_trend": with_trend,
                        "high_chance": high_chance(ev["type"], tf),
                        "flag": ev["type"].endswith("flag"),
                        "same": rv.walk(same, entry, atr, after),
                        "opp": rv.walk(opp, entry, atr, after)})
    return out


def judge(evs: Sequence[Dict]) -> Dict:
    s, o = rv.side_stats(evs, "same"), rv.side_stats(evs, "opp")

    def wins(x):
        return (x["resolved"] or 0) >= rv.MIN_N and (x["win_pct"] or 0) >= rv.WIN_HI \
            and (x["z"] or 0) >= rv.Z_MIN and (x["avg_r"] or 0) > 0
    v = "REVERSAL" if wins(o) else "CONTINUATION" if wins(s) else (
        "too few" if (s["resolved"] or 0) < rv.MIN_N else "no edge")
    return {"same": s, "opp": o, "verdict": v}


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    groups = {"HIGH-CHANCE patterns (decides)": [e for e in evs if e["high_chance"]],
              "every pattern (context)": list(evs),
              "flags only (context)": [e for e in evs if e["flag"]]}
    for tf in ("4H", "1D", "1W"):
        groups[f"high-chance on {tf} (context)"] = [e for e in evs if e["high_chance"] and e["tf"] == tf]
    out = {}
    for name, ge in groups.items():
        older = judge([e for e in ge if e["ts"] < cutoff_ms])
        recent = judge([e for e in ge if e["ts"] >= cutoff_ms])
        same = older["verdict"] == recent["verdict"] and older["verdict"] in ("REVERSAL", "CONTINUATION")
        out[name] = {"older": older, "recent": recent,
                     "answer": older["verdict"] if same else "no consistent edge"}
    return out


def render_telegram(res: Dict) -> str:
    lines = ["🎯↩️ After a pattern HITS ITS TARGET: reversal or continuation? (pre-registered)",
             f"{len(res['coins'])} coins · 4H {res['h4_days']}d, 1D/1W {res['days']}d · live detectors · "
             f"split at {res['cutoff'][:10]} (older / last 365 days)",
             "At the close of the candle that reached the target: the same-direction trade vs the "
             "opposite · 1:1 at 1.5 × ATR · max 20 candles · 0.13% costs", ""]
    for name, f in res["summary"].items():
        lines.append(f"━━ {name}  →  {f['answer']}")
        for per in ("older", "recent"):
            p = f[per]
            lines.append(f"  {per}: {p['verdict']}")
            lines.append(f"    continuation (same): {rv._s(p['same'])}")
            lines.append(f"    reversal (opposite): {rv._s(p['opp'])}")
        lines.append("")
    lines.append(f"Rule fixed before the run (the first row decides): {rv.MIN_N}+ resolved; a side wins "
                 f"with ≥ {rv.WIN_HI:g}%, z ≥ {rv.Z_MIN:g} and avg R > 0 in BOTH periods. REVERSAL → "
                 "weaken the same direction after a target hit and strengthen the opposite; "
                 "CONTINUATION → the opposite rule; otherwise leave the scoring alone.")
    return "\n".join(lines)


def run(series: Dict[str, Dict[str, List[Dict]]], *, end_ms: int, log=print, **kw) -> Dict:
    import app as appmod
    evs = []
    for sym, by_tf in series.items():
        for tf, candles in by_tf.items():
            evs += scan(candles, tf, appmod.TF_LIMIT.get(tf, 240), sym, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(series), "events": len(evs)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="target_hit_test", description=__doc__.split("\n\n")[0])
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
