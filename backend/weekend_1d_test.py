"""
Weekend 1D picks: do the picks made at the Saturday / Sunday closes do
worse? A pre-registered test (written Nov 27 2026, before any run).

Why: weekend daily candles are thin and often reverse when the week opens;
if the picks made from them do worse, skipping them is a one-line rule.

The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at),
all scan coins, 2018 -> now, one per coin per direction per 20 days; the
standard trade (reversal_1d_test.walk): 1:1 at 1.5 x ATR from the close, 20
days max, stop first, 0.13% costs. Each is grouped by the day of the candle
it was read on (UTC): a SATURDAY or SUNDAY candle is a weekend pick (posted
at the Sunday / Monday 00:00 UTC close).

SKIP WEEKEND PICKS only if, in BOTH eras (2018-2021, 2022-now): 30+ weekend
picks AND their average R is below the weekday picks' by z <= -2 (Welch).
Otherwise no change. Per-day rows are context, not the rule. Coins move
together.

    python -m weekend_1d_test --days 3400
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MIN_N, MAX_Z = 30, -2.0


def weekday(ts: int) -> str:
    return DAYS[datetime.fromtimestamp(ts / 1000, tz=timezone.utc).weekday()]


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *, signal_fn: Callable = tc.signal_at) -> List[Dict]:
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
        res = rv.walk(d, float(daily[i]["close"]), atr, daily[i + 1:])
        if res["r"] is None:
            continue
        last[d] = i
        out.append({"symbol": sym, "ts": ts, "direction": d, "day": weekday(ts), "r": res["r"]})
    return out


def _st(rs: Sequence[float]) -> Dict:
    n = len(rs)
    if not n:
        return {"n": 0, "avg_r": None, "win_pct": None, "_sd": None, "_m": None}
    m = sum(rs) / n
    sd = (sum((x - m) ** 2 for x in rs) / (n - 1)) ** 0.5 if n > 1 else None
    return {"n": n, "avg_r": round(m, 3), "win_pct": round(100 * sum(r > 0 for r in rs) / n, 1), "_sd": sd, "_m": m}


def era(evs: Sequence[Dict]) -> Dict:
    we = _st([e["r"] for e in evs if e["day"] in ("Sat", "Sun")])
    wd = _st([e["r"] for e in evs if e["day"] not in ("Sat", "Sun")])
    z = None
    if we["n"] > 1 and wd["n"] > 1 and we["_sd"] and wd["_sd"]:
        se = (we["_sd"] ** 2 / we["n"] + wd["_sd"] ** 2 / wd["n"]) ** 0.5
        z = round((we["_m"] - wd["_m"]) / se, 2) if se > 0 else None
    by_day = {d: _st([e["r"] for e in evs if e["day"] == d]) for d in DAYS}
    for v in (we, wd, *by_day.values()):
        v.pop("_sd", None)
        v.pop("_m", None)
    return {"weekend": we, "weekday": wd, "z": z, "by_day": by_day,
            "worse": we["n"] >= MIN_N and z is not None and z <= MAX_Z}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    return {"eras": {"A": a, "B": b}, "skip_weekend": a["worse"] and b["worse"],
            "coins": sorted({e["symbol"] for e in evs}), "n": len(evs)}


def _s(st: Dict) -> str:
    return f"{st['n']} · avg {st['avg_r']:+.3f}R · win {st['win_pct']}%" if st["n"] else "none"


def render_telegram(res: Dict) -> str:
    L = ["📅 Weekend 1D picks: worse? (pre-registered)",
         f"{len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5×ATR · 20 days max · costs · grouped by the UTC "
         "day of the candle read", ""]
    for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
        e = res["eras"][k]
        L.append(f"━━ {nm}")
        L.append(f"  weekend (Sat/Sun candles): {_s(e['weekend'])}")
        L.append(f"  weekdays: {_s(e['weekday'])}")
        L.append(f"  weekend vs weekdays: z {e['z']} {'❌ worse' if e['worse'] else '(no clear difference)'}")
        L.append("  by day: " + " · ".join(f"{d} {v['n']}·{v['avg_r']:+.2f}R" if v["n"] else f"{d} -"
                                          for d, v in e["by_day"].items()))
        L.append("")
    L.append(f"Verdict: {'SKIP weekend 1D picks ✅' if res['skip_weekend'] else 'no change - weekend picks stay ❌'}")
    L.append("Rule fixed before the run: 30+ weekend picks and their avg R below the weekdays' by z ≤ −2, in BOTH "
             "eras. Per-day rows are context. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="weekend_1d_test", description=__doc__.split("\n\n")[0])
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
        print(f"picks replayed: {res['n']}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
