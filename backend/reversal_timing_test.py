"""
Do reversals start near the weekly / monthly close, and how late are the
indicators? A pre-registered test (written Nov 23 2026, before any run).

The owner's question: "reversals generally start near the weekly or monthly
close, or after it - and only once all the indicators have changed?"

PART A - WHEN do major turns happen? (1D, every scan coin)
  A major bottom is a daily candle whose low is the lowest of the 10 days
  either side, after which price rises 4 x ATR(14) above that low within 20
  days without breaking it. A major top is the mirror. Weekly close = Monday
  00:00 UTC (the 1W candle). A turn counts once per DAY and kind, however many
  coins turn that day (coins move together; counting each coin would fake
  certainty).
    NEAR WEEKLY CLOSE   the turn candle is Sunday, Monday or Tuesday
                        (chance: 3 of 7 days = 42.9%)
    NEAR MONTHLY CLOSE  the last 2 days of a month or the first 3 of the next
                        (chance: 5 / days in that month, ~16.4%)
  EDGE if the share beats chance by 5+ points with z >= 2 (binomial), in BOTH
  eras (before 2022 and 2022-now). Otherwise: turns are spread evenly - the
  close is not special.

PART B - HOW LATE are the indicators? (descriptive, same turns, per coin)
  Days from the turn until each flips the reversal's way: RSI(14) crosses 50,
  MACD(12,26,9) crosses its signal, close crosses EMA(20); "all flipped" =
  the last of the three; and the first weekly close after the turn. For each,
  the share of the move (turn -> best price in the next 20 days) already made.

PART C - TRADE IT? The "all three flipped" signal (RSI 50, MACD cross and
  EMA(20) cross all within 3 days, same way), traded as the standard replay:
  entry at that day's close, 1.5 x ATR stop and target (1:1), 20 days max,
  0.13% costs (reversal_1d_test.walk). 21-day cooldown per coin and side.
    FLIP EDGE           30+ resolved, win >= 53%, z >= 2, avg R > 0
    WEEKLY-CLOSE EDGE   flips completing Sun/Mon/Tue beat the other days by
                        3+ win points AND 0.05+ avg R
  Each must hold in BOTH eras. Coins move together, so z overstates certainty.

    python -m reversal_timing_test --days 3400
"""
from __future__ import annotations

import calendar
import sys
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional, Sequence

import regime_switch_test as rst
import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
SIDE = 10                  # lowest/highest of 10 days either side
MOVE_ATR = 4.0             # then a 4 x ATR move
MOVE_DAYS = 20
NEAR_WEEK = (6, 0, 1)      # Sun, Mon, Tue (Python weekday: Mon=0)
EDGE_PTS, Z_MIN = 5.0, 2.0
FLIP_WINDOW, COOLDOWN = 3, 21
WIN_HI, MIN_N, GAP_WIN, GAP_R = 53.0, 30, 3.0, 0.05
ERA_SPLIT = rst.ERA_SPLIT


# ── indicators (plain, on closes) ────────────────────────────────────────────
def ema(vals: Sequence[float], n: int) -> List[Optional[float]]:
    out, k, e = [], 2 / (n + 1), None
    for i, v in enumerate(vals):
        if i + 1 < n:
            out.append(None)
            continue
        e = sum(vals[i + 1 - n:i + 1]) / n if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(vals: Sequence[float], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(vals)
    if len(vals) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = vals[i] - vals[i - 1]
        g, l = g + max(d, 0), l + max(-d, 0)
    g, l = g / n, l / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        g, l = (g * (n - 1) + max(d, 0)) / n, (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd(vals: Sequence[float]):
    f, s = ema(vals, 12), ema(vals, 26)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    start = next((i for i, x in enumerate(line) if x is not None), len(line))
    sig_part = ema([x for x in line[start:]], 9)
    return line, [None] * start + sig_part


def _atr_at(c: Sequence[Dict], i: int) -> Optional[float]:
    return rv._atr(c[:i + 1]) if i >= 15 else None


def _wd(ts: int) -> int:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).weekday()


def near_month(ts: int) -> bool:
    d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    last = calendar.monthrange(d.year, d.month)[1]
    return d.day >= last - 1 or d.day <= 3


def month_chance(ts: int) -> float:
    d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return 5 / calendar.monthrange(d.year, d.month)[1]


# ── Part A + B: major turns ──────────────────────────────────────────────────
def turns(c: Sequence[Dict]) -> List[Dict]:
    out = []
    for i in range(max(SIDE, 15), len(c) - max(SIDE, MOVE_DAYS)):
        atr = _atr_at(c, i)
        if not atr:
            continue
        lo, hi = float(c[i]["low"]), float(c[i]["high"])
        left, right = c[i - SIDE:i], c[i + 1:i + SIDE + 1]      # a tie counts once: the first wins
        after = c[i + 1:i + 1 + MOVE_DAYS]
        if lo < min(float(x["low"]) for x in left) and lo <= min(float(x["low"]) for x in right):
            best = 0.0
            for x in after:
                if float(x["low"]) < lo:
                    break
                best = max(best, float(x["high"]))
            if best >= lo + MOVE_ATR * atr:
                out.append({"i": i, "ts": int(c[i]["timestamp"]), "kind": "bottom", "price": lo, "best": best})
        if hi > max(float(x["high"]) for x in left) and hi >= max(float(x["high"]) for x in right):
            best = float("inf")
            for x in after:
                if float(x["high"]) > hi:
                    break
                best = min(best, float(x["low"]))
            if best <= hi - MOVE_ATR * atr:
                out.append({"i": i, "ts": int(c[i]["timestamp"]), "kind": "top", "price": hi, "best": best})
    return out


def _binom(hits: int, n: int, p: float) -> Dict:
    if not n:
        return {"n": 0, "pct": None, "chance": round(100 * p, 1), "z": None}
    z = (hits - n * p) / (n * p * (1 - p)) ** 0.5
    return {"n": n, "hits": hits, "pct": round(100 * hits / n, 1), "chance": round(100 * p, 1), "z": round(z, 2)}


def part_a(all_turns: Sequence[Dict]) -> Dict:
    uniq = {(t["ts"], t["kind"]) for t in all_turns}
    res = {}
    for era, keep in (("older", lambda ts: ts < ERA_SPLIT), ("recent", lambda ts: ts >= ERA_SPLIT)):
        days = [ts for ts, _ in uniq if keep(ts)]
        wk = _binom(sum(_wd(ts) in NEAR_WEEK for ts in days), len(days), 3 / 7)
        p_m = sum(month_chance(ts) for ts in days) / len(days) if days else 5 / 30.44
        mo = _binom(sum(near_month(ts) for ts in days), len(days), p_m)
        by_day = [sum(_wd(ts) == d for ts in days) for d in range(7)]
        res[era] = {"week": wk, "month": mo, "by_day": by_day}
    for key in ("week", "month"):
        res[key + "_edge"] = all(
            (res[e][key]["pct"] or 0) >= res[e][key]["chance"] + EDGE_PTS and (res[e][key]["z"] or 0) >= Z_MIN
            for e in ("older", "recent"))
    return res


def lags(c: Sequence[Dict], t: Dict, ind: Dict) -> Dict:
    """Days from the turn until each indicator flips, and the share of the move made by then."""
    i, up = t["i"], t["kind"] == "bottom"
    move = abs(t["best"] - t["price"])
    out = {}

    def first(cond):
        for k in range(i, min(len(c), i + 1 + MOVE_DAYS)):
            if k > 0 and cond(k):
                return k
        return None
    r, ml, ms, e20 = ind["rsi"], ind["macd"], ind["sig"], ind["ema20"]
    ok = lambda *xs: all(x is not None for x in xs)              # noqa: E731
    ks = {
        "RSI 50": first(lambda k: ok(r[k]) and (r[k] > 50 if up else r[k] < 50)),
        "MACD cross": first(lambda k: ok(ml[k], ms[k]) and (ml[k] > ms[k] if up else ml[k] < ms[k])),
        "EMA20 cross": first(lambda k: ok(e20[k]) and (float(c[k]["close"]) > e20[k] if up
                                                       else float(c[k]["close"]) < e20[k])),
    }
    ks["all three"] = max(ks.values()) if all(v is not None for v in ks.values()) else None
    ks["weekly close"] = next((k for k in range(i + 1, min(len(c), i + 8)) if _wd(int(c[k]["timestamp"])) == 0), None)
    for name, k in ks.items():
        if k is None or not move:
            out[name] = None
            continue
        made = (float(c[k]["close"]) - t["price"]) / (t["best"] - t["price"])
        out[name] = {"days": k - i, "made": made}
    return out


def part_b(rows: Sequence[Dict]) -> Dict:
    names = ("RSI 50", "MACD cross", "EMA20 cross", "all three", "weekly close")
    res = {}
    for nm in names:
        got = [r[nm] for r in rows if r.get(nm)]
        res[nm] = {"n": len(got), "never": len(rows) - len(got),
                   "days": median(g["days"] for g in got) if got else None,
                   "made": round(100 * median(g["made"] for g in got)) if got else None}
    return res


# ── Part C: trade the all-three flip ─────────────────────────────────────────
def flips(c: Sequence[Dict], ind: Dict) -> List[Dict]:
    r, ml, ms, e20 = ind["rsi"], ind["macd"], ind["sig"], ind["ema20"]
    last_cross = {"LONG": {}, "SHORT": {}}
    out, cool = [], {"LONG": -10 ** 9, "SHORT": -10 ** 9}
    for k in range(30, len(c)):
        if None in (r[k], r[k - 1], ml[k], ms[k], ml[k - 1], ms[k - 1], e20[k], e20[k - 1]):
            continue
        cl, pc = float(c[k]["close"]), float(c[k - 1]["close"])
        for d, sgn in (("LONG", 1), ("SHORT", -1)):
            if sgn * (r[k] - 50) > 0 >= sgn * (r[k - 1] - 50):
                last_cross[d]["rsi"] = k
            if sgn * (ml[k] - ms[k]) > 0 >= sgn * (ml[k - 1] - ms[k - 1]):
                last_cross[d]["macd"] = k
            if sgn * (cl - e20[k]) > 0 >= sgn * (pc - e20[k - 1]):
                last_cross[d]["ema"] = k
            lc = last_cross[d]
            if len(lc) == 3 and k in lc.values() and max(lc.values()) - min(lc.values()) < FLIP_WINDOW \
                    and k - cool[d] >= COOLDOWN \
                    and sgn * (r[k] - 50) > 0 and sgn * (ml[k] - ms[k]) > 0 and sgn * (cl - e20[k]) > 0:
                atr = _atr_at(c, k)
                if atr:
                    tr = rv.walk(d, cl, atr, c[k + 1:])
                    if tr["result"] != "pending":
                        out.append({"ts": int(c[k]["timestamp"]), "dir": d, "trade": tr,
                                    "near_week": _wd(int(c[k]["timestamp"])) in NEAR_WEEK})
                    cool[d] = k
    return out


def _stats(evs: Sequence[Dict]) -> Dict:
    return rv.side_stats(evs, "trade")


def part_c(evs: Sequence[Dict]) -> Dict:
    res = {}
    for era, keep in (("older", lambda ts: ts < ERA_SPLIT), ("recent", lambda ts: ts >= ERA_SPLIT)):
        e = [x for x in evs if keep(x["ts"])]
        res[era] = {"all": _stats(e), "near": _stats([x for x in e if x["near_week"]]),
                    "other": _stats([x for x in e if not x["near_week"]]),
                    "long": _stats([x for x in e if x["dir"] == "LONG"]),
                    "short": _stats([x for x in e if x["dir"] == "SHORT"])}

    def flip_ok(s):
        return (s["resolved"] or 0) >= MIN_N and (s["win_pct"] or 0) >= WIN_HI and (s["z"] or 0) >= Z_MIN \
            and (s["avg_r"] or 0) > 0

    def near_ok(n, o):
        return (n["resolved"] or 0) >= MIN_N and (o["resolved"] or 0) >= MIN_N \
            and (n["win_pct"] or 0) - (o["win_pct"] or 0) >= GAP_WIN \
            and (n["avg_r"] or 0) - (o["avg_r"] or 0) >= GAP_R
    res["flip_edge"] = all(flip_ok(res[e]["all"]) for e in ("older", "recent"))
    res["near_edge"] = all(near_ok(res[e]["near"], res[e]["other"]) for e in ("older", "recent"))
    return res


def indicators(c: Sequence[Dict]) -> Dict:
    closes = [float(x["close"]) for x in c]
    ml, ms = macd(closes)
    return {"rsi": rsi(closes), "macd": ml, "sig": ms, "ema20": ema(closes, 20)}


def run(daily_by_coin: Dict[str, List[Dict]], *, log=print) -> Dict:
    all_turns, lag_rows, flip_evs = [], [], []
    for sym, c in daily_by_coin.items():
        if len(c) < 120:
            continue
        ind = indicators(c)
        ts = turns(c)
        all_turns += ts
        lag_rows += [lags(c, t, ind) for t in ts]
        flip_evs += flips(c, ind)
        log(f"  {sym}: {len(ts)} turns, {sum(1 for x in flip_evs)} flips so far")
    first = min((int(c[0]["timestamp"]) for c in daily_by_coin.values() if c), default=0)
    return {"coins": sorted(daily_by_coin), "since": first, "turns": len(all_turns),
            "a": part_a(all_turns), "b": part_b(lag_rows), "c": part_c(flip_evs)}


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _bn(x: Dict) -> str:
    if not x["n"]:
        return "no data"
    return f"{x['pct']}% vs chance {x['chance']}% (n {x['n']}, z {x['z']:+.2f})"


def _st(s: Dict) -> str:
    if not s["resolved"]:
        return "no trades"
    return f"{s['resolved']} · win {s['win_pct']}% (z {s['z']:+.2f}) · avg {s['avg_r']:+.3f}R"


def render_telegram(res: Dict) -> str:
    a, b, c = res["a"], res["b"], res["c"]
    since = datetime.fromtimestamp(res["since"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    L = ["⏳ Do reversals start near the weekly / monthly close? (pre-registered)",
         f"1D, {len(res['coins'])} coins since {since} · {res['turns']} major turns "
         f"(10-day extreme, then a {MOVE_ATR:g}×ATR move in 20 days) · each day counted once", ""]
    L.append("━━ A. When do the turns happen?")
    for era in ("older", "recent"):
        e = a[era]
        nm = "before 2022" if era == "older" else "2022 → now"
        L.append(f"{nm}:")
        L.append(f"  Sun/Mon/Tue (weekly close): {_bn(e['week'])}")
        L.append(f"  month turn (last 2 / first 3 days): {_bn(e['month'])}")
        tot = sum(e["by_day"]) or 1
        L.append("  by day: " + " · ".join(f"{DAYS[d]} {round(100 * e['by_day'][d] / tot)}%" for d in range(7)))
    L.append(f"Weekly close: {'EDGE ✅ - turns cluster there' if a['week_edge'] else 'no edge ❌ - turns are spread evenly'}")
    L.append(f"Monthly close: {'EDGE ✅ - turns cluster there' if a['month_edge'] else 'no edge ❌ - turns are spread evenly'}")
    L.append("")
    L.append("━━ B. How late are the indicators? (median, from the turn candle)")
    for nm, x in b.items():
        if not x["n"]:
            L.append(f"  {nm}: never within 20 days")
            continue
        L.append(f"  {nm}: {x['days']:g} days · {x['made']}% of the move already made"
                 + (f" · never flipped in {x['never']}" if x["never"] else ""))
    L.append("")
    L.append("━━ C. Trade the 'all three flipped' signal (1:1 at 1.5×ATR, 20 days, costs)")
    for era in ("older", "recent"):
        e = c[era]
        nm = "before 2022" if era == "older" else "2022 → now"
        L.append(f"{nm}: all {_st(e['all'])}")
        L.append(f"  long {_st(e['long'])}")
        L.append(f"  short {_st(e['short'])}")
        L.append(f"  flip on Sun/Mon/Tue {_st(e['near'])}")
        L.append(f"  flip other days {_st(e['other'])}")
    L.append(f"Flip signal: {'EDGE ✅' if c['flip_edge'] else 'no edge ❌'} (needs win ≥ {WIN_HI:g}%, z ≥ 2, avg R > 0 "
             "in both eras)")
    L.append(f"Flip near the weekly close: {'EDGE ✅' if c['near_edge'] else 'no edge ❌'} (needs +{GAP_WIN:g} win pts "
             f"and +{GAP_R:g}R over other days, both eras)")
    L.append("")
    L.append("Coins move together, so z overstates certainty. Rules fixed Nov 23 2026 before the run.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="reversal_timing_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    if "BTC" not in daily:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = run(daily, log=log)
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
