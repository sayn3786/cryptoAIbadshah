"""
Score CLEANUP: does the 1D signal do as well without the factors that tested
as no edge? A pre-registered test (written Nov 17 2026, before any run).

Why: the strength score adds up dozens of sections, many reading the same
thing (RSI, Stoch, Bollinger, stretch = "oversold"), so it double-counts.
This month each of these, tested on its own on 1D across all coins, had no
consistent edge: the Reversal Radar rollup, RSI divergence, Stoch RSI, CHoCH,
liquidity grab and engulfing candles. If the score does as well without
them, the simpler score is the more honest one.

How: every 1D signal (the dashboard's 1D analysis + real Fear & Greed ->
signals.generate_signal, as regime_switch_test) records which section added
which points (score_breakdown). The CLEANED read removes the points of the
sections below and recomputes the way the engine does: direction from the
score vs the +/-35 threshold, strength moved by the change in |score| over
the 220-point ceiling (later adjustments kept as they were).
  REMOVED  reversal_radar, rsi_divergence, stoch_rsi, choch, liquidity_grab,
           engulfing
Both books trade every signal at strength >= 62, one per coin per direction
per 20 days, 1:1 at 1.5 x 1D ATR, max 20 days, 0.13% costs.

CLEANUP SUPPORTED only if, in BOTH eras (2018-2021 and 2022-now):
  - the cleaned book's avg R is at least the current book's (no worse)
  - the cleaned book still wins >= 53% with z >= 2 and avg R > 0
Then those sections' points come out of the 1D score - through a paper test
first, as with every scoring change. Coins move together, so z overstates
certainty.

    python -m score_cleanup_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
MAX_SCORE = 220.0
THRESHOLD = 35.0
REMOVE = ("reversal_radar", "rsi_divergence", "stoch_rsi", "choch", "liquidity_grab", "engulfing")


def cleaned(sig: Dict) -> Dict:
    """(direction, strength) with the REMOVE sections' points taken out."""
    bd = sig.get("score_breakdown") or {}
    score = sum(float(v) for v in bd.values())
    cut = sum(float(bd.get(k) or 0) for k in REMOVE)
    new = score - cut
    s0 = float(sig.get("strength") or 0)
    strength = max(0.0, min(100.0, s0 + (abs(new) - abs(score)) / MAX_SCORE * 100))
    d = "LONG" if new >= THRESHOLD else "SHORT" if new <= -THRESHOLD else "NEUTRAL"
    return {"direction": d, "strength": round(strength, 1), "removed_points": round(cut, 1)}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        reads = {"current": {"direction": sig.get("direction"), "strength": float(sig.get("strength") or 0)},
                 "cleaned": cleaned(sig)}
        entry, after = float(window[-1]["close"]), daily[i + 1:]
        for book, r in reads.items():
            d = r["direction"]
            if d not in ("LONG", "SHORT") or r["strength"] < FLOOR:
                continue
            key = (book, d)
            if key in last and i - last[key] < rv.HOLD_DAYS:
                continue
            last[key] = i
            out.append({"book": book, "symbol": sym, "ts": ts, "direction": d,
                        "trade": rv.walk(d, entry, atr, after)})
    return out


def _st(evs):
    return rv.side_stats([{"x": e["trade"]} for e in evs], "x")


def era(evs: Sequence[Dict]) -> Dict:
    c, k = _st([e for e in evs if e["book"] == "current"]), _st([e for e in evs if e["book"] == "cleaned"])
    checks = {
        "cleaned avg R ≥ current": (k["avg_r"] or -9) >= (c["avg_r"] or -9),
        "cleaned wins ≥ 53%, z ≥ 2, avg R > 0": ((k["win_pct"] or 0) >= rv.WIN_HI and (k["z"] or 0) >= rv.Z_MIN
                                                 and (k["avg_r"] or 0) > 0),
    }
    return {"current": c, "cleaned": k, "checks": checks, "passes": all(checks.values())}


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
    lines = ["🧹 Score CLEANUP: the 1D signal without the factors that tested as no edge (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · removed: " + ", ".join(REMOVE),
             "Both books: 1D signal at strength ≥ 62 · 1:1 at 1.5 × 1D ATR · max 20 days · 0.13% costs · "
             "one per coin per direction per 20 days", ""]
    for k, name in (("A", "ERA A: 2018 → 2021"), ("B", "ERA B: 2022 → now")):
        e = res["eras"][k]
        lines.append(f"━━ {name}  →  {'HOLDS ✅' if e['passes'] else 'FAILS ❌'}")
        lines.append(f"  current score: {rv._s(e['current'])}")
        lines.append(f"  cleaned score: {rv._s(e['cleaned'])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        lines.append("")
    lines.append(f"Verdict: {'CLEANUP SUPPORTED ✅' if res['supported'] else 'NOT supported ❌'} "
                 "(both eras must hold)")
    lines.append("Rule fixed before the run: the cleaned book is no worse per trade than the current one "
                 f"and still wins ≥ {rv.WIN_HI:g}% (z ≥ {rv.Z_MIN:g}) with avg R > 0, in BOTH eras. "
                 "Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="score_cleanup_test", description=__doc__.split("\n\n")[0])
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
