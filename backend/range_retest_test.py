"""
Does a RETEST confirm a range break? A pre-registered test (written Nov 16
2026, before any run).

A range break (a close outside the box) can't be told apart from a fake one
by holding 7 closes, breakout volume or a big breakout candle - each helped in
one period and failed in the other (range_vs_random, pattern_outcome_study).
The classic confirmation not yet tested: the RETEST - price comes back to the
broken edge from the other side and is rejected there (old support acts as
resistance, or the reverse).

On every coin's 4H (720 days) and 1D (2000 days) chart, the dashboard's own
range boxes (ranges.boxes on the last 150 closed candles, as drawn), two
events, traded in the BREAK's direction:
  PLAIN BREAK    the close that breaks the box (what the chart marks today)
  RETEST HELD    within 10 candles after a break that is still outside, the
                 first candle that comes back to touch the broken edge (its
                 wick reaches within 0.25 x ATR of it) and still CLOSES
                 outside: the retest was rejected
Trades: 1:1 at 1.5 x ATR(14) of the chart, max 20 candles, stop first, 0.13%
costs; the same trade the other way is reported too; at most one per coin per
event type per 20 candles.

RETEST CONFIRMS a break on a chart only if, in BOTH periods (older / last 365
days):
  - 30+ resolved retest-held trades
  - they win >= 53% with z >= 2 and their avg R is above 0
  - their avg R beats the plain breaks' by at least 0.10
Coins move together, so z overstates certainty.

    python -m range_retest_test --days 2000 --h4-days 720
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Sequence

import ranges
import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
WINDOW = 150
RETEST_BARS = 10
TOUCH_ATR = 0.25
COOLDOWN = 20
R_GAP = 0.10
TF_MS = {"4H": 4 * 3_600_000, "1D": DAY_MS}
PLAIN, RETEST = "plain break", "retest held"


def events_at(window: List[Dict], tf_ms: int, seen: set) -> List[tuple]:
    """(family, direction) on the window's last candle."""
    last = len(window) - 1
    atr = ranges._atr(window, last)
    if not atr:
        return []
    c = window[-1]
    hi, lo, close = float(c["high"]), float(c["low"]), float(c["close"])
    out = []
    for b in ranges.boxes(window, tf_ms=tf_ms):
        ev = b.get("event")
        if ev not in ("broke_above", "broke_below"):
            continue
        up = ev == "broke_above"
        d = "LONG" if up else "SHORT"
        ago = b.get("event_ago")
        if ago == 0:
            out.append((PLAIN, d))
            continue
        if not (1 <= (ago or 0) <= RETEST_BARS):
            continue
        key = (b["start_ts"], b["event_ts"])
        if key in seen:
            continue
        edge = float(b["top"] if up else b["bottom"])
        touched = (lo <= edge + TOUCH_ATR * atr) if up else (hi >= edge - TOUCH_ATR * atr)
        held = close > edge if up else close < edge
        if touched and held:
            seen.add(key)
            out.append((RETEST, d))
    return out


def scan(bars: Sequence[Dict], tf: str, sym: str = "", *, events_fn: Callable = events_at) -> List[Dict]:
    out, last, seen = [], {}, set()
    for i in range(WINDOW - 1, len(bars)):
        window = list(bars[i - WINDOW + 1:i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        entry, after = float(window[-1]["close"]), bars[i + 1:]
        for fam, d in events_fn(window, TF_MS[tf], seen):
            if fam in last and i - last[fam] < COOLDOWN:
                continue
            last[fam] = i
            opp = "SHORT" if d == "LONG" else "LONG"
            out.append({"family": fam, "tf": tf, "symbol": sym, "ts": int(window[-1]["timestamp"]),
                        "direction": d, "with": rv.walk(d, entry, atr, after),
                        "opp": rv.walk(opp, entry, atr, after)})
    return out


def period(evs: Sequence[Dict]) -> Dict:
    r = rv.side_stats([e for e in evs if e["family"] == RETEST], "with")
    p = rv.side_stats([e for e in evs if e["family"] == PLAIN], "with")
    ro = rv.side_stats([e for e in evs if e["family"] == RETEST], "opp")
    checks = {
        f"{rv.MIN_N}+ retest trades": (r["resolved"] or 0) >= rv.MIN_N,
        "retest wins ≥ 53%, z ≥ 2, avg R > 0": ((r["win_pct"] or 0) >= rv.WIN_HI and (r["z"] or 0) >= rv.Z_MIN
                                                and (r["avg_r"] or 0) > 0),
        f"avg R ≥ {R_GAP:g} above plain breaks": ((r["avg_r"] or 0) - (p["avg_r"] or 0)) >= R_GAP,
    }
    return {"retest": r, "plain": p, "retest_opposite": ro, "checks": checks,
            "passes": all(checks.values())}


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    out = {}
    for tf in ("4H", "1D"):
        te = [e for e in evs if e["tf"] == tf]
        older = period([e for e in te if e["ts"] < cutoff_ms])
        recent = period([e for e in te if e["ts"] >= cutoff_ms])
        out[tf] = {"older": older, "recent": recent, "confirmed": older["passes"] and recent["passes"]}
    return out


def render_telegram(res: Dict) -> str:
    lines = ["📦🔁 Does a RETEST confirm a range break? (pre-registered)",
             f"{len(res['coins'])} coins · 4H {res['h4_days']}d, 1D {res['days']}d · the dashboard's "
             f"range boxes · split at {res['cutoff'][:10]} (older / last 365 days)",
             "Traded in the break's direction · 1:1 at 1.5 × ATR · max 20 candles · 0.13% costs · "
             "retest = within 10 candles, the wick touches the broken edge and the candle still "
             "closes outside", ""]
    for tf in ("4H", "1D"):
        f = res["summary"][tf]
        lines.append(f"━━ {tf}  →  {'RETEST CONFIRMS ✅' if f['confirmed'] else 'NOT confirmed ❌'}")
        for per in ("older", "recent"):
            p = f[per]
            lines.append(f"  {per}: {'holds' if p['passes'] else 'fails'}")
            lines.append(f"    retest held:  {rv._s(p['retest'])}")
            lines.append(f"    plain break:  {rv._s(p['plain'])}")
            lines.append(f"    retest, other way: {rv._s(p['retest_opposite'])}")
            lines.append("    " + " · ".join(f"{'✅' if ok else '❌'} {k}" for k, ok in p["checks"].items()))
        lines.append("")
    lines.append(f"Rule fixed before the run: per chart, in BOTH periods, {rv.MIN_N}+ retest trades winning "
                 f"≥ {rv.WIN_HI:g}% (z ≥ {rv.Z_MIN:g}) with avg R > 0 and {R_GAP:g}R+ better than plain "
                 "breaks. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def run(series: Dict[str, Dict[str, List[Dict]]], *, end_ms: int, log=print, **kw) -> Dict:
    evs = []
    for sym, by_tf in series.items():
        for tf, bars in by_tf.items():
            evs += scan(bars, tf, sym, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff), "coins": sorted(series)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="range_retest_test", description=__doc__.split("\n\n")[0])
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
