"""
Do VALUE AREA edges make price reverse? A pre-registered test (written Nov 22
2026, before any run).

Why: volume-profile levels (volume_profile: the 30-day value area high / low)
are a popular support / resistance read. Every price-only level tested this
month (ranges, retests, order blocks) had no consistent edge; this is checked
the same way before it is trusted.

  PROFILE  each day, the 30-day volume profile of the PREVIOUS 30 closed daily
           candles (nothing from the day itself)
  EVENT    the day's candle reaches the value area LOW coming from above
           (yesterday's close above VAL, today's low <= VAL) -> LONG, or the
           value area HIGH coming from below (yesterday's close below VAH,
           today's high >= VAH) -> SHORT; days that CLOSE through the level
           (below VAL / above VAH) are skipped (it gave way)
  TRADE    at that close, the level's side and the opposite, 1:1 at 1.5 x 1D
           ATR, max 20 days, stop first, 0.13% costs; one per coin per side per
           20 days, in time order

Decided before running, on BOTH periods (older / the last 365 days):
  LEVEL HOLDS   30+ resolved, the level's side wins >= 53% with z >= 2, avg R > 0
  LEVEL FAILS   the level's side wins <= 47% with z <= -2 and the opposite avg R > 0
  otherwise     no consistent edge
Context: VAL (long) and VAH (short) separately. Coins move together, so z
overstates certainty.

4H BREAK CHECK (added Nov 22 2026, before its run; --tf 4H): the 1D run
above found the bounce LOSING and the break side ahead (51.7% / 54.1%). That
was not the question asked, so it is checked on data it has not seen - 4H
candles (720 days of 1H history, aggregated), the same events with a 30-bar
4H profile, the trade 1:1 at 1.5 x 4H ATR, max 20 bars. Decided now:
  EDGES BREAK   in BOTH periods (older / last 365 days) the BREAK side (the
                opposite of the level's side) wins >= 53% with z >= 2, avg R > 0
                and 30+ resolved
  otherwise     no edge - value areas stay context only
Only if it holds does a break lean go to a shadow paper test.

    python -m volume_profile_test --days 2000
    python -m volume_profile_test --tf 4H --h4-days 720
"""
from __future__ import annotations

import sys
import time
from typing import Dict, List, Sequence

import reversal_1d_test as rv
import volume_profile as vp

DAY_MS = rv.DAY_MS
ALL, VAL, VAH = ("all value-area edges (decides)", "value area LOW -> long (context)",
                 "value area HIGH -> short (context)")


def scan(daily: Sequence[Dict], sym: str = "") -> List[Dict]:
    out, last = [], {}
    for i in range(vp.WINDOW + 1, len(daily)):
        p = vp.profile(daily[i - vp.WINDOW:i])              # the previous 30 candles only
        if not p:
            continue
        prev_c = float(daily[i - 1]["close"])
        c = daily[i]
        lo, hi, close = float(c["low"]), float(c["high"]), float(c["close"])
        side = None
        if prev_c > p["val"] and lo <= p["val"] and close >= p["val"]:
            side, kind = "LONG", "val"
        elif prev_c < p["vah"] and hi >= p["vah"] and close <= p["vah"]:
            side, kind = "SHORT", "vah"
        if not side or (side in last and i - last[side] < rv.HOLD_DAYS):
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        last[side] = i
        opp = "SHORT" if side == "LONG" else "LONG"
        after = daily[i + 1:]
        out.append({"symbol": sym, "ts": int(c["timestamp"]), "kind": kind,
                    "with": rv.walk(side, close, atr, after), "opp": rv.walk(opp, close, atr, after)})
    return out


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    groups = {ALL: list(evs), VAL: [e for e in evs if e["kind"] == "val"],
              VAH: [e for e in evs if e["kind"] == "vah"]}
    out = {}
    for name, ge in groups.items():
        per = {}
        for p, sel in (("older", [e for e in ge if e["ts"] < cutoff_ms]),
                       ("recent", [e for e in ge if e["ts"] >= cutoff_ms])):
            w, o = rv.side_stats(sel, "with"), rv.side_stats(sel, "opp")
            per[p] = {"with": w, "opp": o, "verdict": rv.verdict(w, o).replace(
                "REVERSAL WINS", "LEVEL HOLDS").replace("OPPOSITE WINS", "LEVEL FAILS")}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        out[name] = {**per, "answer": per["older"]["verdict"] if same and per["older"]["verdict"] in (
            "LEVEL HOLDS", "LEVEL FAILS") else "no consistent edge"}
    return out


def run(daily_by_coin: Dict[str, List[Dict]], *, end_ms: int, log=print) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(daily_by_coin), "events": len(evs)}


def summarize_break(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    """The 4H question: does the BREAK side (the opposite) win?"""
    out = {}
    for p, sel in (("older", [e for e in evs if e["ts"] < cutoff_ms]),
                   ("recent", [e for e in evs if e["ts"] >= cutoff_ms])):
        brk, hold = rv.side_stats(sel, "opp"), rv.side_stats(sel, "with")
        ok = ((brk["resolved"] or 0) >= rv.MIN_N and (brk["win_pct"] or 0) >= rv.WIN_HI
              and (brk["z"] or 0) >= rv.Z_MIN and (brk["avg_r"] or 0) > 0)
        out[p] = {"break": brk, "hold": hold, "breaks": ok}
    out["answer"] = "EDGES BREAK" if out["older"]["breaks"] and out["recent"]["breaks"] else "no edge"
    return out


def render_break(res: Dict) -> str:
    b = res["break"]
    lines = ["📊 4H check: do VALUE AREA edges BREAK? (pre-registered, out-of-sample for the 1D result)",
             f"{len(res['coins'])} coins · 4H (720 days of 1H, aggregated) · 30-bar profile of the previous "
             f"bars · split at {res['cutoff'][:10]} (older / last 365 days)",
             "Event = a 4H bar reaches VAL from above / VAH from below and holds it at the close · trade the "
             "BREAK side vs the bounce · 1:1 at 1.5 × 4H ATR · max 20 bars · 0.13% costs", "",
             f"━━ answer  →  {b['answer']}"]
    for p in ("older", "recent"):
        q = b[p]
        lines.append(f"  {p}: {'BREAK WINS ✅' if q['breaks'] else 'no ❌'}")
        lines.append(f"    break side:  {rv._s(q['break'])}")
        lines.append(f"    bounce side: {rv._s(q['hold'])}")
    lines.append("")
    lines.append(f"Rule fixed before the run: the break side wins ≥ {rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g}, avg R > 0 "
                 f"and {rv.MIN_N}+ resolved, in BOTH periods. Only then a break lean goes to a shadow paper "
                 "test. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def render_telegram(res: Dict) -> str:
    lines = ["📊 Do VALUE AREA edges make price reverse? (pre-registered)",
             f"{len(res['coins'])} coins · 1D · 30-day volume profile of the previous days · split at "
             f"{res['cutoff'][:10]} (older / last 365 days)",
             "Event = the day reaches VAL from above (long) / VAH from below (short) and holds it at the "
             "close · 1:1 at 1.5 × 1D ATR · max 20 days · 0.13% costs", ""]
    for name, f in res["summary"].items():
        lines.append(f"━━ {name}  →  {f['answer']}")
        for p in ("older", "recent"):
            q = f[p]
            lines.append(f"  {p}: {q['verdict']}")
            lines.append(f"    level side: {rv._s(q['with'])}")
            lines.append(f"    opposite:   {rv._s(q['opp'])}")
        lines.append("")
    lines.append(f"Rule fixed before the run (the first row decides): {rv.MIN_N}+ resolved; the level's side "
                 f"wins ≥ {rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g} and avg R > 0 = LEVEL HOLDS; ≤ {rv.WIN_LO:g}% "
                 f"with z ≤ −{rv.Z_MIN:g} and the opposite's avg R > 0 = LEVEL FAILS; both periods must agree. "
                 "Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="volume_profile_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--tf", choices=("1D", "4H"), default="1D")
    ap.add_argument("--h4-days", type=int, default=720)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}
    if args.tf == "4H":
        import cadence_compare as cc
        h1 = cc.fetch_history(syms, args.h4_days, log=log)
        bars = {s: cc.aggregate(h1[s]["1H"], 4) for s in h1}
        res = run(bars, end_ms=end_ms, log=log)
        evs = []
        for s, b in bars.items():
            evs += scan(b, s)
        res["break"] = summarize_break(evs, end_ms - rv.HOLDOUT_DAYS * DAY_MS)
        text = render_break(res)
    else:
        daily = rs.fetch_daily(syms, args.days, log=log)
        res = run(daily, end_ms=end_ms, log=log)
        text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(res['coins'])}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
