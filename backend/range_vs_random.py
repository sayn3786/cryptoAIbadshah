"""
Do range deviations (and held breaks) predict the next move better than a
coin flip? A pre-registered test (written Nov 14 2026, before any run).

The dashboard draws range boxes (ranges.py). The textbook reading, both ways:
  IF   price closes back inside after 1-6 closes outside (a DEVIATION), the
       break failed: trade toward the OPPOSITE edge of the box
       (below -> LONG to the top edge, above -> SHORT to the bottom edge)
  ELSE the break holds (DEV_MAX + 1 closes in a row outside): trade WITH the
       break, a "measured move" of one box height past the broken edge

Signals: on every closed 4H and 1D candle, ranges.boxes() on the last 150
candles (exactly what the dashboard chart shows), closed candles only.
Charts are built from 1H candles (UTC-aligned, as the exchanges serve them).

Trades (fixed before running, nothing is tuned afterwards):
  deviation   entry the confirming candle's close; stop beyond the
              deviation's extreme (lowest low / highest high of the candles
              outside and the one back in) + 0.1 x ATR(14); target the
              opposite edge of the box
  break held  entry the close that makes it DEV_MAX + 1 closes outside;
              stop back inside the box (the broken edge - 0.25 x ATR);
              target the broken edge + one box height; skipped when price
              is already past that target
  both        max hold 30 candles of the chart; walked on 1H candles, the
              stop first inside a candle; $5 at risk; costs as live; one
              position per coin
Baseline: the same entries with random directions (stop and target mirrored
around the entry, same distances), 50 runs; "beats random" = above the best
5% of them. REVERSED rows: the same trades the other way.

Decided before any run, per chart (4H and 1D) and per play, on BOTH
never-read periods (end_days_ago 0 and 400, 365 days each):
  works   30+ trades, profit after costs, PF >= 1.15, beats random, and the
          REVERSED row does not beat random
Only a play that works on both periods gets the if/else text on the
dashboard and in Telegram; otherwise the boxes stay information only.

    python -m range_vs_random --fetch-days 365 --end-days-ago 0
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import entry_exit_compare as ee
import htf_vs_random as hv
import ranges
import scalp_baseline as sb
import scalp_score_vs_random as svr

HOUR_MS = 3_600_000
WINDOW = 150                      # candle_analysis.STRUCTURE_CHART_BARS
CHARTS = (("4H", 4), ("1D", 24))  # (label, hours)
HOLD_CANDLES = 30
DEV_STOP_ATR = 0.1
BREAK_STOP_ATR = 0.25
MIN_TRADES = 30
MIN_PF = 1.15
PERIODS = (0, 400)
PLAYS = ("deviation", "break held")


def _box_index(window: Sequence[Dict], b: Dict) -> Optional[Dict]:
    """The dashboard box `b` as indices into `window`."""
    ts = [int(c["timestamp"]) for c in window]
    try:
        i0 = ts.index(int(b["start_ts"]))
    except ValueError:
        return None
    i1 = i0 + int(b["bars"]) - 1
    return {"i0": i0, "i1": i1, "top": float(b["top"]), "bottom": float(b["bottom"])}


def signals_at(window: Sequence[Dict], tf_ms: int) -> List[Dict]:
    """Deviation / held-break signals on the LAST candle of `window`."""
    last = len(window) - 1
    atr = ranges._atr(window, last)
    if not atr:
        return []
    out = []
    for b in ranges.boxes(window, tf_ms=tf_ms):
        if b["current"]:
            continue
        r = _box_index(window, b)
        if r is None:
            continue
        top, bot = r["top"], r["bottom"]
        close = float(window[last]["close"])
        if b["event"] in ("deviation_below", "deviation_above") and b["event_ago"] == 0:
            n = int(b["outside_candles"] or 0)
            seg = window[last - n:last + 1]
            if b["event"] == "deviation_below":
                d, sl, tgt = "LONG", min(float(c["low"]) for c in seg) - DEV_STOP_ATR * atr, top
            else:
                d, sl, tgt = "SHORT", max(float(c["high"]) for c in seg) + DEV_STOP_ATR * atr, bot
            out.append({"play": "deviation", "direction": d, "entry": close, "sl": sl,
                        "target": tgt})
            continue
        # held break: exactly DEV_MAX + 1 closes outside, on one side, after inside
        k = ranges.DEV_MAX + 1
        if last - k < r["i1"]:
            continue
        sides = [ranges._side(float(c["close"]), r) for c in window[last - k:last + 1]]
        if sides[0] != 0 or len(set(sides[1:])) != 1 or sides[1] == 0:
            continue
        h = top - bot
        if sides[1] > 0:
            d, sl, tgt = "LONG", top - BREAK_STOP_ATR * atr, top + h
        else:
            d, sl, tgt = "SHORT", bot + BREAK_STOP_ATR * atr, bot - h
        out.append({"play": "break held", "direction": d, "entry": close, "sl": sl,
                    "target": tgt})
    good = []
    for s in out:
        sgn = 1 if s["direction"] == "LONG" else -1
        if sgn * (s["entry"] - s["sl"]) > 0 and sgn * (s["target"] - s["entry"]) > 0:
            good.append(s)
    return good


def candidates(market: Dict, tf: str, hours: int, *, start_ms: int, end_ms: int,
               log=print) -> List[Dict]:
    import cadence_compare as cc
    tf_ms = hours * HOUR_MS
    out = []
    for sym, v in market.items():
        bars = cc.aggregate(v["1H"], hours)
        for i in range(WINDOW - 1, len(bars)):
            slot = int(bars[i]["timestamp"]) + tf_ms          # the candle has closed
            if not (start_ms <= slot < end_ms):
                continue
            for s in signals_at(bars[i - WINDOW + 1:i + 1], tf_ms):
                out.append({**s, "symbol": sym, "slot_ms": slot, "strength": 0.0})
        log(f"  {sym} {tf}: scanned")                      # no results in the public log
    return out


def _reversed(c: Dict) -> Dict:
    e = c["entry"]
    return {**svr.reverse(c), "sl": 2 * e - c["sl"], "target": 2 * e - c["target"]}


def works(test: Dict, rev: Dict) -> bool:
    return ((test.get("trades") or 0) >= MIN_TRADES and (test.get("pnl_usd") or 0) > 0
            and (test.get("pf_usd") or 0) >= MIN_PF and bool(test.get("beats_random"))
            and not rev.get("beats_random"))


def compare(market: Dict, *, days: float, log=print, seeds: int = svr.SEEDS,
            sim: Callable = ee.simulate_hl) -> Dict:
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    start = end - int(days * 24 * HOUR_MS)
    span = (end - start) / (24 * HOUR_MS)
    charts = []
    for tf, hours in CHARTS:
        cands = candidates(market, tf, hours, start_ms=start, end_ms=end, log=log)
        book = hv.make_book(market, HOLD_CANDLES * hours, sim)
        plays = []
        for play in PLAYS:
            cs = [c for c in cands if c["play"] == play]
            test = svr.row(f"{play}", cs, book, span, seeds)
            rev = svr.row(f"{play}, REVERSED", [_reversed(c) for c in cs], book, span, seeds)
            rr = [abs(c["target"] - c["entry"]) / abs(c["entry"] - c["sl"]) for c in cs]
            plays.append({"play": play, "rows": [test, rev], "works": works(test, rev),
                          "longs": sum(1 for c in cs if c["direction"] == "LONG"),
                          "avg_rr": round(sum(rr) / len(rr), 2) if rr else None})
            log(f"    {tf} {play}: done")                  # no results in the public log
        charts.append({"tf": tf, "hold_h": HOLD_CANDLES * hours, "plays": plays})
    return {"window": {"start": sb.sc._iso(start), "end": sb.sc._iso(end), "days": round(span, 1)},
            "coins": sorted(market), "charts": charts}


PLAY_TEXT = {
    "deviation": "IF a deviation is confirmed → trade to the opposite edge of the box",
    "break held": f"ELSE the break holds ({ranges.DEV_MAX + 1} closes outside) → trade with it, "
                  "one box height",
}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["📦 Range boxes vs a coin flip: do deviations and held breaks predict? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             "$5 risk, costs as live, one per coin, max 30 candles; 50 random-direction runs on "
             "the same entries", ""]
    for ch in res["charts"]:
        lines.append(f"━━ {ch['tf']} chart (max {ch['hold_h']}h)")
        for p in ch["plays"]:
            lines.append(PLAY_TEXT[p["play"]])
            if p["avg_rr"] is not None:
                lines.append(f"  avg reward:risk {p['avg_rr']} · LONG {p['longs']} of "
                             f"{p['rows'][0]['signals']}")
            for r in p["rows"]:
                lines += svr.render_row(r)
            lines.append(f"  This period: {'WORKS ✅' if p['works'] else 'does not work ❌'}")
            lines.append("")
    lines.append(f"Decided before the runs, per chart and play, on BOTH end_days_ago {PERIODS[0]} "
                 f"and {PERIODS[1]}: {MIN_TRADES}+ trades, profit after costs, PF ≥ {MIN_PF:g}, "
                 "beats random, and REVERSED does not.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="range_vs_random", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=365)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) // HOUR_MS * HOUR_MS
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    # The 1D chart needs WINDOW days of warm-up before the first signal.
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS},
                              args.fetch_days + WINDOW, end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, days=args.fetch_days, log=log)
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
