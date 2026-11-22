"""
Do the money leans help WEEKLY signals? A pre-registered test (written Nov 22
2026, before any run).

Why: the Coinbase premium and stablecoin-supply leans held on 1D in both eras
(macro_lean_test) and now move alt 1D signals +/-5. A weekly trade lasts up to
20 weeks - a different horizon - so it is tested before 1W gets the same.

How: every alt's 1W signal (the dashboard's 1W analysis on Monday-to-Sunday
weeks built from daily history, as tp_reached_test --tf 1W) at strength >= 62.
The lean at each weekly close is the lean of the week's last day (macro_lean_
test's own functions). Each signal is grouped:
  agree     at least one lean points its way and none against
  against   at least one lean points the other way and none its way
  none      no lean (or one each way)
Trade: 1:1 at 1.5 x 1W ATR, max 20 weeks, stop first, 0.13% costs; one per
coin per direction per 20 weeks.

+/-5 ON 1W ALTS only if, in BOTH eras (2018-2021 and 2022-now): "agree" wins
>= 5 points more than "against" AND makes more per trade, with 20+ resolved
in each group. Otherwise 1W stays without the leans. Coins move together, so
z overstates certainty.

    python -m weekly_lean_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tp_reached_test as tt

DAY_MS = rv.DAY_MS
FLOOR = 62.0
MIN_GAP, MIN_RESOLVED = 5.0, 20
GROUPS = ("agree", "against", "none")


def group(direction: str, ln: Dict[str, str]) -> str:
    sides = [s for s in ln.values() if s]
    agree = any(s == direction for s in sides)
    against = any(s != direction for s in sides)
    return "agree" if agree and not against else "against" if against and not agree else "none"


def scan(weeks: Sequence[Dict], sym: str, leans: Dict[str, Dict[int, str]], fg: Dict[int, int], *,
         signal_fn: Callable = tt.signal_at_1w) -> List[Dict]:
    out, last = [], {}
    for i in range(tt.W_WARMUP - 1, len(weeks)):
        window = list(weeks[max(0, i - tt.W_LOOKBACK + 1):i + 1])
        ts = int(window[-1]["timestamp"])
        last_day = ts + 6 * DAY_MS
        sig = signal_fn(window, sym, fg.get(last_day)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(window)
        if not atr:
            continue
        last[d] = i
        g = group(d, {k: (v or {}).get(last_day) for k, v in leans.items()})
        out.append({"group": g, "symbol": sym, "ts": ts, "direction": d,
                    "x": rv.walk(d, float(window[-1]["close"]), atr, weeks[i + 1:])})
    return out


def era(evs: Sequence[Dict]) -> Dict:
    st = {g: rv.side_stats([e for e in evs if e["group"] == g], "x") for g in GROUPS}
    a, b = st["agree"], st["against"]
    checks = {
        f"{MIN_RESOLVED}+ resolved in agree and against": ((a["resolved"] or 0) >= MIN_RESOLVED
                                                          and (b["resolved"] or 0) >= MIN_RESOLVED),
        f"agree wins ≥ {MIN_GAP:g} pts more": (a["win_pct"] is not None and b["win_pct"] is not None
                                               and a["win_pct"] - b["win_pct"] >= MIN_GAP),
        "agree makes more per trade": (a["avg_r"] is not None and b["avg_r"] is not None
                                       and a["avg_r"] > b["avg_r"]),
    }
    return {"stats": st, "checks": checks, "passes": all(checks.values())}


def run(daily_by_coin: Dict[str, List[Dict]], leans, fg, *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        if sym == "BTC":
            continue                                       # the leans are an alt edge
        evs += scan(tt.weekly(daily), sym, leans, fg, **kw)
        log(f"  {sym}: scanned")                           # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    return {"eras": {"A": a, "B": b}, "supported": a["passes"] and b["passes"],
            "coins": sorted({e["symbol"] for e in evs})}


def render_telegram(res: Dict) -> str:
    lines = ["🗓️ Money leans on WEEKLY alt signals (pre-registered)",
             f"{len(res['coins'])} alts · 1W signal ≥ 62 · 1:1 at 1.5 × 1W ATR · max 20 weeks · 0.13% costs",
             "agree = Coinbase premium / stablecoin lean points the signal's way (none against) at the weekly close",
             ""]
    for k, name in (("A", "ERA A: 2018 → 2021"), ("B", "ERA B: 2022 → now")):
        e = res["eras"][k]
        lines.append(f"━━ {name}  →  {'HOLDS ✅' if e['passes'] else 'no ❌'}")
        for g in GROUPS:
            lines.append(f"  {g}: {rv._s(e['stats'][g])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        lines.append("")
    lines.append(f"Verdict: {'±5 ON 1W ALTS ✅' if res['supported'] else 'NOT supported ❌ (1W stays without the leans)'}")
    lines.append(f"Rule fixed before the run: agree wins ≥ {MIN_GAP:g} pts more than against AND makes more per "
                 f"trade, {MIN_RESOLVED}+ resolved each, in BOTH eras. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="weekly_lean_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    import macro_lean_test as ml
    import tao_case_1d_test as tc
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    btc = {int(c["timestamp"]) // DAY_MS * DAY_MS: float(c["close"]) for c in daily.get("BTC") or []}
    leans = {"premium": {}, "stable": {}}
    try:
        leans["premium"] = ml.premium_lean(ml.fetch_coinbase_daily(min(btc), max(btc) + DAY_MS), btc)
    except Exception as exc:                             # noqa: BLE001
        log(f"  coinbase unavailable: {type(exc).__name__}")
    try:
        leans["stable"] = ml.stable_lean(ml.fetch_stable_supply())
    except Exception as exc:                             # noqa: BLE001
        log(f"  stablecoin supply unavailable: {type(exc).__name__}")
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
        fg = {}
    res = run(daily, leans, fg, log=log)
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
