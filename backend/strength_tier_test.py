"""
Confirmed (69+) only, or 62+? A pre-registered test (written Nov 22 2026,
before any run).

Why: the dashboard's per-device accuracy panel showed Confirmed (69+) at 52%
win / +1.8% vs Strong 44% (58 / 48 signals, mixed timeframes, not
independent). The 1D paper book trades every 62+ signal. Does restricting it
to 69+ do better per trade, on the full replay?

How: every 1D signal (the dashboard's 1D analysis + real Fear & Greed ->
signals.generate_signal, as regime_switch_test / tp_reached_test), all coins,
2018 -> now. Books (each one trade per coin per direction per 20 days):
  62-68    strength 62 to under 69
  69+      strength 69 and up (the Confirmed tier)
  62+      every 62+ signal (the book as it trades now; context)
Trade: 1:1 at 1.5 x 1D ATR from the close, max 20 days, stop first, 0.13% costs.

SWITCH THE BOOK TO 69+ only if, in BOTH eras (2018-2021 and 2022-now):
  - 69+ makes at least 0.03R a trade more than 62-68
  - 69+ itself wins >= 53% with z >= 2, avg R > 0, 30+ resolved
Otherwise the book stays at 62+. Coins move together, so z overstates
certainty.

    python -m strength_tier_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
LOW, HIGH = 62.0, 69.0
MIN_EDGE_R = 0.03
MIN_RESOLVED = 30
BOOKS = ("62-68", "69+", "62+")


def books_for(strength: float) -> List[str]:
    if strength < LOW:
        return []
    return (["69+"] if strength >= HIGH else ["62-68"]) + ["62+"]


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        atr = rv._atr(window)
        if d not in ("LONG", "SHORT") or not atr:
            continue
        trade = None
        for b in books_for(s):
            key = (b, d)
            if key in last and i - last[key] < rv.HOLD_DAYS:
                continue
            last[key] = i
            trade = trade or rv.walk(d, float(window[-1]["close"]), atr, daily[i + 1:])
            out.append({"book": b, "symbol": sym, "ts": ts, "direction": d, "x": trade})
    return out


def era(evs: Sequence[Dict]) -> Dict:
    st = {b: rv.side_stats([e for e in evs if e["book"] == b], "x") for b in BOOKS}
    hi, mid = st["69+"], st["62-68"]
    checks = {
        f"69+ ≥ 62-68 by {MIN_EDGE_R:g}R a trade": (hi["avg_r"] is not None and mid["avg_r"] is not None
                                                   and hi["avg_r"] - mid["avg_r"] >= MIN_EDGE_R),
        f"69+ wins ≥ {rv.WIN_HI:g}%, z ≥ {rv.Z_MIN:g}, avg R > 0, {MIN_RESOLVED}+": (
            (hi["resolved"] or 0) >= MIN_RESOLVED and (hi["win_pct"] or 0) >= rv.WIN_HI
            and (hi["z"] or 0) >= rv.Z_MIN and (hi["avg_r"] or 0) > 0),
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
    return {"eras": {"A": a, "B": b}, "switch": a["passes"] and b["passes"],
            "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(min(e["ts"] for e in evs)) if evs else "-"}


def render_telegram(res: Dict) -> str:
    lines = ["🏅 Confirmed (69+) only, or every 62+ 1D signal? (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · 1:1 at 1.5 × 1D ATR · max 20 days · "
             "0.13% costs · one per coin per direction per 20 days (per book)", ""]
    for k, name in (("A", "ERA A: 2018 → 2021"), ("B", "ERA B: 2022 → now")):
        e = res["eras"][k]
        lines.append(f"━━ {name}  →  {'69+ BETTER ✅' if e['passes'] else 'no ❌'}")
        for b in BOOKS:
            lines.append(f"  {b}{' (as now)' if b == '62+' else ''}: {rv._s(e['stats'][b])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        lines.append("")
    lines.append(f"Verdict: {'SWITCH the 1D book to 69+ ✅' if res['switch'] else 'KEEP 62+ ❌'} (both eras must hold)")
    lines.append(f"Rule fixed before the run: 69+ makes ≥ {MIN_EDGE_R:g}R a trade more than 62-68 AND wins "
                 f"≥ {rv.WIN_HI:g}% (z ≥ {rv.Z_MIN:g}) with avg R > 0 and {MIN_RESOLVED}+ resolved, in BOTH "
                 "eras. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="strength_tier_test", description=__doc__.split("\n\n")[0])
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
