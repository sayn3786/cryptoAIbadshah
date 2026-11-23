"""
A TIME STOP for 1D trades: close a trade that has not moved +0.5R in its
favour within N days. A pre-registered test (written Nov 23 2026, before any
run).

Why: the quarterly edge check found the plain 1D signal thin in 2026
(52% wins, +0.025R) while the exit rules on top of it (8H early exit, size
cap) kept working. A trade that goes nowhere ties up risk and often ends at
its stop; a time stop is the same kind of rule as the early exit.

The trades: every 1D signal at strength >= 62 (the dashboard's 1D analysis +
real Fear & Greed, tao_case_1d_test.signal_at), all scan coins, 2018 -> now,
one per coin per direction per 20 days; 1:1 at 1.5 x 1D ATR from the close,
max 20 days, stop first inside a day, 0.13% costs (exit_rule_test).
  base   the opposite-signal exit: out at the daily close when the coin's 1D
         signal points the other way at 62+ (the daily version of the live
         early exit; the 8H one needs hourly data that does not reach 2018)
  ts2 / ts3 / ts4   base + a time stop: at the close of day N, if price has
         NOT touched +0.5R in the trade's favour (the day's high for a long,
         low for a short) since entry, the trade closes at that close
A TIME STOP IS ADOPTED only if, in BOTH eras (2018-2021, 2022-now): its
average R per trade is >= the base's AND its average losing trade is
smaller (less negative) than the base's. Of those that pass, the one with
the higher average R across the two eras. Coins move together.

    python -m time_stop_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import exit_rule_test as er
import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
PROGRESS_R = 0.5
VARIANTS = ("base", "ts2", "ts3", "ts4")


def manage(direction: str, entry: float, atr: float, after: Sequence[Dict],
           later_sigs: Sequence[Optional[Dict]], n_days: Optional[int]) -> Dict:
    sgn = 1 if direction == "LONG" else -1
    risk = rv.ATR_MULT * atr
    stop, tgt = entry - sgn * risk, entry + sgn * risk
    cost_r = rv.COST * entry / risk
    best = 0.0
    for k, c in enumerate(after[:rv.HOLD_DAYS]):
        hi, lo, close = float(c["high"]), float(c["low"]), float(c["close"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            return {"result": "stop", "r": -1 - cost_r, "days": k + 1}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r, "days": k + 1}
        best = max(best, sgn * ((hi if sgn > 0 else lo) - entry) / risk)
        s = later_sigs[k] if k < len(later_sigs) else None
        if s and s.get("direction") in ("LONG", "SHORT") and s["direction"] != direction \
                and float(s.get("strength") or 0) >= FLOOR:
            return {"result": "early", "r": sgn * (close - entry) / risk - cost_r, "days": k + 1}
        if n_days is not None and k + 1 == n_days and best < PROGRESS_R:
            return {"result": "timestop", "r": sgn * (close - entry) / risk - cost_r, "days": k + 1}
    if len(after) < rv.HOLD_DAYS:
        return {"result": "pending", "r": None}
    last = float(after[rv.HOLD_DAYS - 1]["close"])
    return {"result": "time", "r": sgn * (last - entry) / risk - cost_r, "days": rv.HOLD_DAYS}


N_OF = {"base": None, "ts2": 2, "ts3": 3, "ts4": 4}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    sigs: Dict[int, Dict] = {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sigs[i] = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
    out, last = [], {}
    for i, sig in sigs.items():
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        last[d] = i
        after = daily[i + 1:]
        later = [sigs.get(i + 1 + k) for k in range(min(len(after), rv.HOLD_DAYS))]
        entry = float(daily[i]["close"])
        out.append({"symbol": sym, "ts": int(daily[i]["timestamp"]), "direction": d,
                    **{v: manage(d, entry, atr, after, later, N_OF[v]) for v in VARIANTS}})
    return out


def era(evs: Sequence[Dict]) -> Dict:
    st = {v: er.stats(evs, v) for v in VARIANTS}
    for v in VARIANTS:
        st[v]["time_stops"] = sum(1 for e in evs if e[v]["result"] == "timestop")
    b = st["base"]
    passes = {v: (st[v]["avg_r"] is not None and b["avg_r"] is not None and st[v]["avg_r"] >= b["avg_r"]
                  and st[v]["avg_loss"] is not None and b["avg_loss"] is not None
                  and st[v]["avg_loss"] > b["avg_loss"]) for v in VARIANTS[1:]}
    return {"stats": st, "passes": passes}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    ok = [v for v in VARIANTS[1:] if a["passes"][v] and b["passes"][v]]
    best = max(ok, key=lambda v: a["stats"][v]["avg_r"] + b["stats"][v]["avg_r"]) if ok else None
    return {"eras": {"A": a, "B": b}, "adopt": best, "coins": sorted({e["symbol"] for e in evs})}


NAMES = {"base": "opposite-signal exit (base)", "ts2": "+ time stop day 2", "ts3": "+ time stop day 3",
         "ts4": "+ time stop day 4"}


def _s(st: Dict) -> str:
    if not st["n"]:
        return "no trades"
    return (f"{st['n']} · win {st['win_pct']}% · avg {st['avg_r']:+.3f}R · avg loss {st['avg_loss']:+.2f}R · "
            f"total {st['total_r']:+.0f}R" + (f" · {st['time_stops']} time-stopped" if st.get("time_stops") else ""))


def render_telegram(res: Dict) -> str:
    L = ["⏱️ A time stop for 1D trades: out if not +0.5R within N days? (pre-registered)",
         f"{len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5×ATR · 20 days max · costs", ""]
    for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
        e = res["eras"][k]
        L.append(f"━━ {nm}")
        for v in VARIANTS:
            mark = "" if v == "base" else (" ✅" if e["passes"][v] else " ❌")
            L.append(f"  {NAMES[v]}: {_s(e['stats'][v])}{mark}")
        L.append("")
    L.append(f"Verdict: {'ADOPT ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'no time stop passes ❌'}")
    L.append("Rule fixed before the run: avg R ≥ the base's AND a smaller average loss, in BOTH eras. "
             "Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="time_stop_test", description=__doc__.split("\n\n")[0])
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
