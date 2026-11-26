"""
The 0.75 Fibonacci pullback: buy (sell) the bounce when price retraces ~75%
of a swing? A pre-registered test (written Nov 26 2026, before any run).

Why: an X post ("most underrated tool in trading, $BTC 0.75") showed BTC
bouncing off the 0.75 retracement of a swing. The engine already measures
the 0.618-0.786 zone but only as a score brake, never as a trade.

The swings: pivots on closed candles (a high / low that is the extreme of 5
bars either side), cleaned into alternating highs and lows; a leg counts
when it spans >= 3 x ATR(14). After an UP leg (low -> high) is confirmed,
for up to 40 bars, price pulls back; the depth is how far it has come back,
(high - lowest low since the high) / (high - low). The watch ends when price
makes a new high or breaks the leg's low. A DOWN leg is the mirror (shorts).

A depth ZONE trades the FIRST candle that closes in the trend's direction
(green after an up leg, red after a down leg) while the deepest pullback so
far sits inside the zone; entry at that close. Zones:
  0.38-0.5 · 0.5-0.618 · 0.618-0.705 · 0.705-0.786 (THE 0.75 ZONE) · 0.786-0.9
Every trade is the standard one (reversal_1d_test.walk): 1:1 at 1.5 x ATR,
20 bars max, stop first, 0.13% costs - so a deeper entry gets no better R
just from a tighter stop.
  1D   all scan coins, 2018 -> now, eras 2018-2021 / 2022-now
  4H   all scan coins, ~2.5 years of 1H history, older / recent halves

THE 0.75 ZONE IS AN EDGE (per timeframe) only if, in BOTH periods: 30+
trades, average R > 0, AND it beats the other zones pooled (the same bounce
at other depths) by z >= 2 on average R. Coins move together.

    python -m fib_075_test --days 3400 --h1-days 900
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence, Tuple

import regime_switch_test as rgs
import reversal_1d_test as rv

K = 5
MIN_LEG_ATR = 3.0
WATCH = 40
ZONES: Tuple[Tuple[str, float, float], ...] = (
    ("0.38-0.5", 0.382, 0.5), ("0.5-0.618", 0.5, 0.618), ("0.618-0.705", 0.618, 0.705),
    ("0.705-0.786", 0.705, 0.786), ("0.786-0.9", 0.786, 0.9))
TARGET = "0.705-0.786"
MIN_N, MIN_Z = 30, 2.0


def pivots(c: Sequence[Dict], k: int = K) -> List[Tuple[int, str, float]]:
    """Alternating [(index, "H"|"L", price)]; ties: the first bar wins."""
    raw = []
    for i in range(k, len(c) - k):
        hi, lo = float(c[i]["high"]), float(c[i]["low"])
        win = c[i - k:i + k + 1]
        if hi >= max(float(x["high"]) for x in win) and all(float(x["high"]) < hi for x in c[i - k:i]):
            raw.append((i, "H", hi))
        if lo <= min(float(x["low"]) for x in win) and all(float(x["low"]) > lo for x in c[i - k:i]):
            raw.append((i, "L", lo))
    out: List[Tuple[int, str, float]] = []
    for p in raw:
        if out and out[-1][1] == p[1]:
            better = p[2] > out[-1][2] if p[1] == "H" else p[2] < out[-1][2]
            if better:
                out[-1] = p
            continue
        out.append(p)
    return out


def scan(c: Sequence[Dict], sym: str = "") -> List[Dict]:
    """Every zone trade on these candles: {symbol, ts, zone, direction, r}."""
    out = []
    pv = pivots(c)
    for (i, t0, p0), (j, t1, p1) in zip(pv, pv[1:]):
        up = t0 == "L" and t1 == "H"
        if not up and not (t0 == "H" and t1 == "L"):
            continue
        atr = rv._atr(c[max(0, j - 20):j + 1])
        span = abs(p1 - p0)
        if not atr or span < MIN_LEG_ATR * atr:
            continue
        sgn = 1 if up else -1
        deepest, done = 0.0, set()
        start = j + K
        for t in range(j + 1, min(len(c), start + WATCH)):
            hi, lo = float(c[t]["high"]), float(c[t]["low"])
            if (hi > p1) if up else (lo < p1):
                break                                       # new extreme: the leg extends
            if (lo < p0) if up else (hi > p0):
                break                                       # the leg is broken
            deepest = max(deepest, ((p1 - lo) if up else (hi - p1)) / span)
            if t < start:
                continue                                    # the pivot is not confirmed yet
            o, cl = float(c[t]["open"]), float(c[t]["close"])
            if sgn * (cl - o) <= 0:
                continue
            for name, a, b in ZONES:
                if name not in done and a <= deepest < b:
                    done.add(name)
                    at = rv._atr(c[max(0, t - 20):t + 1])
                    if not at:
                        continue
                    res = rv.walk("LONG" if up else "SHORT", cl, at, c[t + 1:])
                    if res["r"] is not None:
                        out.append({"symbol": sym, "ts": int(c[t]["timestamp"]), "zone": name,
                                    "direction": "LONG" if up else "SHORT", "r": res["r"]})
    return out


def _st(rs: Sequence[float]) -> Dict:
    n = len(rs)
    if not n:
        return {"n": 0, "avg_r": None, "win_pct": None, "sd": None}
    m = sum(rs) / n
    sd = (sum((x - m) ** 2 for x in rs) / (n - 1)) ** 0.5 if n > 1 else None
    return {"n": n, "avg_r": round(m, 3), "win_pct": round(100 * sum(r > 0 for r in rs) / n, 1), "sd": sd}


def period(evs: Sequence[Dict]) -> Dict:
    zones = {z: _st([e["r"] for e in evs if e["zone"] == z]) for z, _, _ in ZONES}
    tgt = [e["r"] for e in evs if e["zone"] == TARGET]
    oth = [e["r"] for e in evs if e["zone"] != TARGET]
    a, b = _st(tgt), _st(oth)
    z = None
    if a["n"] > 1 and b["n"] > 1 and a["sd"] and b["sd"]:
        se = (a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"]) ** 0.5
        z = round((a["avg_r"] - b["avg_r"]) / se, 2) if se > 0 else None
    ok = a["n"] >= MIN_N and (a["avg_r"] or 0) > 0 and z is not None and z >= MIN_Z
    for v in zones.values():
        v.pop("sd", None)
    return {"zones": zones, "others": {"n": b["n"], "avg_r": b["avg_r"]}, "z": z, "passes": ok,
            "longs": _st([e["r"] for e in evs if e["zone"] == TARGET and e["direction"] == "LONG"])["avg_r"],
            "shorts": _st([e["r"] for e in evs if e["zone"] == TARGET and e["direction"] == "SHORT"])["avg_r"]}


def run_tf(series: Dict[str, List[Dict]], *, split: Optional[int] = None, log=print) -> Dict:
    evs = []
    for sym, c in series.items():
        evs += scan(c, sym)
        log(f"  {sym}: scanned")                           # no results in the public log
    if not evs:
        return {"periods": {}, "edge": False, "split": None, "n": 0}
    if split is None:                                       # halves at the middle trade
        ts = sorted(e["ts"] for e in evs)
        split = ts[len(ts) // 2]
    ps = {"older": period([e for e in evs if e["ts"] < split]),
          "recent": period([e for e in evs if e["ts"] >= split])}
    return {"periods": ps, "edge": all(p["passes"] for p in ps.values()), "split": split, "n": len(evs),
            "coins": len({e["symbol"] for e in evs})}


def _d(ms: Optional[int]) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "-"


def _z(st: Dict) -> str:
    return f"{st['n']} · avg {st['avg_r']:+.3f}R · win {st['win_pct']}%" if st["n"] else "none"


def render_telegram(res: Dict) -> str:
    L = ["📐 The 0.75 Fibonacci pullback: an edge? (pre-registered)",
         "Swings of 3+ ATR (5-bar pivots); first candle closing with the trend while the pullback sits in "
         "the zone; standard 1:1 at 1.5×ATR, 20 bars, costs", ""]
    for tf, nm in (("1D", "1D · eras split 2022-01-01"), ("4H", "4H · halves split {}")):
        r = res.get(tf) or {}
        if not r.get("periods"):
            L += [f"━━ {tf}: no trades", ""]
            continue
        L.append(f"━━ {nm.format(_d(r['split']))} · {r['coins']} coins")
        for k, p in r["periods"].items():
            L.append(f"  {k}:")
            for z, _, _ in ZONES:
                mark = " ◀" if z == TARGET else ""
                L.append(f"    {z}: {_z(p['zones'][z])}{mark}")
            L.append(f"    0.75 zone vs the others ({p['others']['avg_r']}R avg): z {p['z']} · longs "
                     f"{p['longs']}R / shorts {p['shorts']}R {'✅' if p['passes'] else '❌'}")
        L.append(f"  {tf} verdict: {'0.75 IS an edge ✅' if r['edge'] else 'no edge at 0.75 ❌'}")
        L.append("")
    L.append("Rule fixed before the run: the 0.705-0.786 zone needs 30+ trades, avg R > 0 and to beat the "
             "other depths by z ≥ 2, in BOTH periods. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="fib_075_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--h1-days", type=int, default=900)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}
    daily = cs.fetch_deep(syms, args.days, log=log)
    res = {"1D": run_tf(daily, split=rgs.ERA_SPLIT, log=log)}
    h = cc.fetch_history(syms, args.h1_days, log=log)
    res["4H"] = run_tf({s: v["4H"] for s, v in h.items() if v.get("4H")}, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"trades: 1D {res['1D'].get('n')} / 4H {res['4H'].get('n')}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
