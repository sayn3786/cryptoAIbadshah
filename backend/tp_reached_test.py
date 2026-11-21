"""
After the targets were reached: is the NEXT same-direction 1D signal weaker?
A pre-registered test (written Nov 22 2026, before any run).

Why (the owner's idea): when a signal's own targets are reached before the
next close, that move may have run into support / resistance and be due a
bounce, so the next signal the same way should count for less.

How: every 1D signal (the dashboard's 1D analysis + real Fear & Greed ->
signals.generate_signal, as in regime_switch_test / score_cleanup_test).
  ALREADY RAN (TP1)   yesterday's signal was LONG / SHORT at 62+ and today's
                      candle reached its TP1 (a LONG's high >= TP1, a SHORT's
                      low <= TP1)
  ALREADY RAN (last)  the same with its LAST target (context)
  the trade           today's signal, when it points the SAME way at 62+:
                      1:1 at 1.5 x 1D ATR from today's close, max 20 days,
                      stop first, 0.13% costs
Groups (each one trade per coin per direction per 20 days):
  after TP1      today's 62+ signal after an ALREADY RAN (TP1) day
  other          every other 62+ signal (the comparison)
  after last TP  after an ALREADY RAN (last) day (context)

PENALTY SUPPORTED only if, in BOTH eras (2018-2021 and 2022-now):
  - "after TP1" wins >= 5 points less often than "other"
  - and makes less per trade (avg R)
  - with 30+ resolved "after TP1" trades in each era
Then a fixed -10 points for the same direction after its TP1 was reached -
through a paper test first, as with every scoring change. Coins move
together, so z overstates certainty.

    python -m tp_reached_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
MIN_GAP_PTS = 5.0
MIN_RESOLVED = 30
GROUPS = ("after TP1", "other", "after last TP")


def reached(sig: Optional[Dict], candle: Dict) -> Dict[str, bool]:
    """Did `candle` reach the signal's TP1 / last target?"""
    out = {"tp1": False, "last": False}
    if not sig or sig.get("direction") not in ("LONG", "SHORT") or float(sig.get("strength") or 0) < FLOOR:
        return out
    tps = [float(t) for t in (sig.get("tp_targets") or []) if t is not None]
    if not tps:
        return out
    long = sig["direction"] == "LONG"
    hit = (lambda t: float(candle["high"]) >= t) if long else (lambda t: float(candle["low"]) <= t)
    return {"tp1": hit(tps[0]), "last": hit(tps[-1])}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last, prev = [], {}, None
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        ran = reached(prev, window[-1])
        prev_dir = (prev or {}).get("direction")
        prev = sig
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        atr = rv._atr(window)
        if d not in ("LONG", "SHORT") or s < FLOOR or not atr:
            continue
        same = prev_dir == d
        groups = (["after TP1"] if same and ran["tp1"] else ["other"]) + \
                 (["after last TP"] if same and ran["last"] else [])
        entry, after = float(window[-1]["close"]), daily[i + 1:]
        trade = None
        for g in groups:
            key = (g, d)
            if key in last and i - last[key] < rv.HOLD_DAYS:
                continue
            last[key] = i
            trade = trade or rv.walk(d, entry, atr, after)
            out.append({"group": g, "symbol": sym, "ts": ts, "direction": d, "x": trade})
    return out


def era(evs: Sequence[Dict]) -> Dict:
    st = {g: rv.side_stats([e for e in evs if e["group"] == g], "x") for g in GROUPS}
    a, o = st["after TP1"], st["other"]
    checks = {
        f"{MIN_RESOLVED}+ resolved after TP1": (a["resolved"] or 0) >= MIN_RESOLVED,
        f"after TP1 wins ≥ {MIN_GAP_PTS:g} pts less": (a["win_pct"] is not None and o["win_pct"] is not None
                                                        and o["win_pct"] - a["win_pct"] >= MIN_GAP_PTS),
        "after TP1 makes less per trade": (a["avg_r"] is not None and o["avg_r"] is not None
                                           and a["avg_r"] < o["avg_r"]),
    }
    return {"stats": st, "checks": checks, "passes": all(checks.values())}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    import scalp_baseline as sb
    return {"eras": {"A": a, "B": b}, "supported": a["passes"] and b["passes"],
            "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(min(e["ts"] for e in evs)) if evs else "-"}


def render_telegram(res: Dict) -> str:
    lines = ["🎯 After the targets were reached: is the next same-way 1D signal weaker? (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · 1D signal at strength ≥ 62 · "
             "1:1 at 1.5 × 1D ATR · max 20 days · 0.13% costs · one per coin per direction per 20 days",
             "after TP1 = yesterday's 62+ signal saw its TP1 reached today, and today's signal points the "
             "same way", ""]
    for k, name in (("A", "ERA A: 2018 → 2021"), ("B", "ERA B: 2022 → now")):
        e = res["eras"][k]
        lines.append(f"━━ {name}  →  {'PENALTY HOLDS ✅' if e['passes'] else 'no ❌'}")
        for g in GROUPS:
            lines.append(f"  {g}: {rv._s(e['stats'][g])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        lines.append("")
    lines.append(f"Verdict: {'PENALTY SUPPORTED ✅ (-10 pts, paper test first)' if res['supported'] else 'NOT supported ❌ (the card banner stays information only)'}")
    lines.append(f"Rule fixed before the run: after TP1, the next same-way signal wins ≥ {MIN_GAP_PTS:g} points "
                 f"less than the others AND makes less per trade, with {MIN_RESOLVED}+ resolved, in BOTH eras. "
                 "Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="tp_reached_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed history unavailable: {type(exc).__name__}")
        fg = {}
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    res = run(daily, fg, log=log)
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
