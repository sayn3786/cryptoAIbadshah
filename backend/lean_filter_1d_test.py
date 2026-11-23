"""
The money leans as a FILTER for the 1D signal (skip signals against the
day's lean), instead of the live +/-5 points. A pre-registered test (written
Nov 23 2026, before any run).

Why: the quarterly edge check found the two money leans the strongest edges
in 2026 (Coinbase premium 67% / +0.33R, stablecoin supply 60% / +0.18R)
while the plain 1D signal was thin (52%, +0.025R). On the 15m scalp the
same leans worked as a filter (scalp_lean_filter_compare, agree-only).

Leans per day, from data known at that day's close (macro_lean_test):
  premium   Coinbase BTC-USD vs offshore BTC-USDT close, 3-day mean:
            >= +0.10% LONG, <= -0.10% SHORT
  stable    total stablecoin supply 30-day change: >= +2% LONG, < 0% SHORT
The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at,
no lean points), all scan coins, 2018 -> now, one per coin per direction per
20 days; 1:1 at 1.5 x 1D ATR, max 20 days, stop first, 0.13% costs.
  baseline     every signal
  no-against   skip a signal when a lean of that day points the other way
  agree-only   keep a signal only when a lean agrees and none is against
                 (the scalp's adopted book)
Yardstick: RANDOM skipping - the median average R of 200 random subsets of
the baseline the same size as the book (seeded); a filter must beat that,
not just the baseline.

A FILTER PASSES if, in BOTH eras (2018-2021, 2022-now): 30+ trades, win >=
53% with avg R > 0, AND its average R beats the random-skip median by 0.03+.
If both pass, the one keeping more trades (no-against) is preferred. The
stablecoin series starts ~2019, so the older era is thinner; coins move
together (a lean is the same for every coin on a day), so z overstates
certainty.

    python -m lean_filter_1d_test --days 3400
"""
from __future__ import annotations

import random
import sys
from statistics import median
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import scalp_lean_filter_compare as slf
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
BOOKS = ("baseline", "no-against", "agree-only")
LEANS = ("premium", "stable")
GAP, DRAWS = 0.03, 200


def lean_on(leans: Dict[str, Dict[int, str]], day_ms: int) -> Dict[str, Optional[str]]:
    """The leans of the signal's own (closed) day - known at its close."""
    d = int(day_ms) // DAY_MS * DAY_MS
    return {k: (leans.get(k) or {}).get(d) for k in LEANS}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], leans: Dict[str, Dict[int, str]], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        last[d] = i
        ln = lean_on(leans, ts)
        out.append({"symbol": sym, "ts": ts, "direction": d, "leans": ln,
                    "books": [b for b in BOOKS if slf.keep(b, d, ln)],
                    "trade": rv.walk(d, float(daily[i]["close"]), atr, daily[i + 1:])})
    return out


def random_avg(evs: Sequence[Dict], k: int, seed: int) -> Optional[float]:
    done = [e for e in evs if e["trade"]["r"] is not None]
    if k <= 0 or not done:
        return None
    if k >= len(done):
        return rv.side_stats(done, "trade")["avg_r"]
    rng = random.Random(seed)
    return round(median(rv.side_stats(rng.sample(done, k), "trade")["avg_r"] for _ in range(DRAWS)), 3)


def era(evs: Sequence[Dict], seed: int = 0) -> Dict:
    out = {}
    base = [e for e in evs if "baseline" in e["books"]]
    for b in BOOKS:
        sel = [e for e in evs if b in e["books"]]
        st = rv.side_stats(sel, "trade")
        rnd = None if b == "baseline" else random_avg(base, st["n"], 20261123 + seed + len(b))
        ok = (b != "baseline" and (st["resolved"] or 0) >= rv.MIN_N and (st["win_pct"] or 0) >= rv.WIN_HI
              and (st["avg_r"] or 0) > 0 and rnd is not None and (st["avg_r"] or -9) >= rnd + GAP)
        out[b] = {"stats": st, "random": rnd, "ok": ok}
    out["lean_days"] = sum(1 for e in evs if any(e["leans"].values()))
    return out


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], leans: Dict[str, Dict[int, str]], *,
        log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, leans, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT], 1)
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT], 2)
    passed = [x for x in ("no-against", "agree-only") if a[x]["ok"] and b[x]["ok"]]
    return {"eras": {"A": a, "B": b}, "adopt": passed[0] if passed else None,
            "coins": sorted({e["symbol"] for e in evs}),
            "lean_days": {k: len(leans.get(k) or {}) for k in LEANS}}


def _s(x: Dict) -> str:
    st = x["stats"]
    if not st["n"]:
        return "no trades"
    return (f"{st['resolved']} · win {st['win_pct']}% (z {st['z']:+.2f}) · avg {st['avg_r']:+.3f}R"
            + (f" · random skip {x['random']:+.3f}R" if x["random"] is not None else ""))


NAMES = {"baseline": "every 62+ signal", "no-against": "skip when a lean is against",
         "agree-only": "only when a lean agrees (none against)"}


def render_telegram(res: Dict) -> str:
    L = ["💵 Money leans as a FILTER for the 1D signal? (pre-registered)",
         f"{len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5×ATR, 20 days, costs · lean days: premium "
         f"{res['lean_days']['premium']}, stablecoins {res['lean_days']['stable']}", ""]
    for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
        e = res["eras"][k]
        L.append(f"━━ {nm} (signals on a lean day: {e['lean_days']})")
        for b in BOOKS:
            mark = "" if b == "baseline" else (" ✅" if e[b]["ok"] else " ❌")
            L.append(f"  {NAMES[b]}: {_s(e[b])}{mark}")
        L.append("")
    L.append(f"Verdict: {'ADOPT ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'no filter passes ❌ (the live ±5 points stay)'}")
    L.append(f"Rule fixed before the run: {rv.MIN_N}+ trades, win ≥ {rv.WIN_HI:g}%, avg R > 0 and +{GAP:g}R over random "
             "skipping of the same size, in BOTH eras. A lean is the same for every coin on a day, so certainty is "
             "overstated.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="lean_filter_1d_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
        fg = {}
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    if "BTC" not in daily:
        print("error: no BTC history", file=sys.stderr)
        return 2
    lv = slf.leans_by_day(daily["BTC"])
    log(f"  lean days: premium {len(lv['premium'])}, stable {len(lv['stable'])}")
    if not (lv["premium"] or lv["stable"]):
        print("error: no lean data - the comparison would be meaningless", file=sys.stderr)
        return 3
    res = run(daily, fg, lv, log=log)
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
