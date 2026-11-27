"""
The last third of a 1D pick: the fixed TP3, or a trailing stop after TP2?
A pre-registered test (written Nov 27 2026, before any run).

Why: TP3 is often far away (the owner's RENDER short had it ~50% below
entry), so the last third mostly ends at the 7-day limit or back at entry.
Trailing it after TP2 might keep more of a run.

The trades: be_rule_test's population - every 1D signal at strength >= 62,
all scan coins, one per coin per direction per 7 days; the ENGINE's entry
(filled within 24 hours), stop and TP1/2/3, a third off at each target, the
stop to entry after TP1 (the live rule), closed at market after 7 days;
walked on 1H candles, stop first inside an hour, 0.13% costs. After TP2:
  A       as now: the last third waits for TP3 (or the stop at entry / 7 days)
  T1      TP3 cancelled; the stop trails 1 x the 1D ATR behind the best
          price since TP2 (never loosened, updated after each closed hour)
  T2      the same at 2 x the 1D ATR
Hourly history reaches ~2.5 years: OLDER and RECENT halves of the signals.

A TRAIL REPLACES A only if, in BOTH halves: average R per trade >= A's +
0.03 AND return / max drawdown >= A's. Of those that pass, the higher
worse-half average R. Coins move together.

    python -m trail_test --h1-days 900
"""
from __future__ import annotations

import bisect
import sys
from typing import Callable, Dict, List, Sequence

import be_rule_test as bt
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS, HOUR_MS = bt.DAY_MS, bt.HOUR_MS
VARIANTS = ("A", "T1", "T2")
TRAIL = {"T1": 1.0, "T2": 2.0}
GAP_R = 0.03


def walk(direction: str, entry: float, stop: float, tps: Sequence[float], bars: Sequence[Dict],
         variant: str, atr: float) -> Dict:
    sgn = 1 if direction == "LONG" else -1
    risk = abs(entry - stop)
    if risk <= 0 or not tps or sgn * (entry - stop) <= 0:
        return {"result": "skip"}
    fill = next((k for k, c in enumerate(bars[:bt.FILL_H]) if float(c["low"]) <= entry <= float(c["high"])), None)
    if fill is None:
        return {"result": "not_filled"} if len(bars) >= bt.FILL_H else {"result": "pending"}
    life = bars[fill:fill + bt.HOLD_H]
    if len(life) < bt.HOLD_H:
        return {"result": "pending"}
    trail = TRAIL.get(variant)
    share = 1.0 / len(tps)
    pending = list(tps if trail is None or len(tps) < 3 else tps[:2])
    left, banked, cur, hits, best = 1.0, 0.0, stop, 0, None
    for k, c in enumerate(life):
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= cur) if sgn > 0 else (hi >= cur):
            banked += left * sgn * (cur - entry)
            return bt._done(banked, entry, risk, fill + k, "stop")
        now = [p for p in pending if ((hi >= p) if sgn > 0 else (lo <= p))]
        for p in now:
            banked += share * sgn * (p - entry)
            left -= share
            pending.remove(p)
            hits += 1
        if left <= 1e-9:
            return bt._done(banked, entry, risk, fill + k, "target")
        if now and hits >= 1:
            cur = entry if sgn * (entry - cur) > 0 else cur           # stop to entry after TP1
        if trail is not None and hits >= 2:                          # trailing the last third
            ext = hi if sgn > 0 else lo
            best = ext if best is None else (max(best, ext) if sgn > 0 else min(best, ext))
            cand = best - sgn * trail * atr
            if sgn * (cand - cur) > 0:
                cur = cand
    banked += left * sgn * (float(life[-1]["close"]) - entry)
    return bt._done(banked, entry, risk, fill + len(life) - 1, "expired")


def scan(h1: Sequence[Dict], daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    starts = [int(b["timestamp"]) for b in h1]
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < bt.FLOOR:
            continue
        if d in last and i - last[d] < bt.COOLDOWN_D:
            continue
        try:
            entry, stop = float(sig["entry"]), float(sig["sl"])
            tps = [float(x) for x in sig.get("tp_targets") or [] if x is not None]
        except (KeyError, TypeError, ValueError):
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not tps or not atr:
            continue
        j0 = bisect.bisect_left(starts, ts + DAY_MS)
        bars = h1[j0:j0 + bt.FILL_H + bt.HOLD_H]
        res = {v: walk(d, entry, stop, tps, bars, v, atr) for v in VARIANTS}
        if res["A"]["result"] in ("skip", "pending"):
            continue
        last[d] = i
        out.append({"symbol": sym, "ts": ts, "direction": d, **res})
    return out


def half_eval(evs: Sequence[Dict]) -> Dict:
    st = {v: bt.stats(evs, v) for v in VARIANTS}
    a = st["A"]
    ok = {v: (st[v]["avg_r"] is not None and a["avg_r"] is not None and st[v]["avg_r"] >= a["avg_r"] + GAP_R
              and st[v]["ratio"] is not None and (a["ratio"] is None or st[v]["ratio"] >= a["ratio"]))
          for v in VARIANTS[1:]}
    return {"stats": st, "ok": ok}


def run(series: Dict[str, Dict[str, List[Dict]]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, s in series.items():
        evs += scan(s["1H"], s["1D"], sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    if not evs:
        return {"halves": {}, "adopt": None, "coins": [], "split": None, "from": None}
    ts = sorted(e["ts"] for e in evs)
    split = ts[len(ts) // 2]
    halves = {"older": half_eval([e for e in evs if e["ts"] < split]),
              "recent": half_eval([e for e in evs if e["ts"] >= split])}
    passed = [v for v in VARIANTS[1:] if halves["older"]["ok"][v] and halves["recent"]["ok"][v]]
    adopt = max(passed, key=lambda v: min(halves["older"]["stats"][v]["avg_r"],
                                          halves["recent"]["stats"][v]["avg_r"])) if passed else None
    return {"halves": halves, "adopt": adopt, "coins": sorted({e["symbol"] for e in evs}),
            "split": split, "from": ts[0]}


NAMES = {"A": "A · fixed TP3 (as now)", "T1": "T1 · trail 1×ATR after TP2", "T2": "T2 · trail 2×ATR after TP2"}


def render_telegram(res: Dict) -> str:
    L = ["🪜 The last third: fixed TP3 or a trailing stop? (pre-registered, 1H candles)",
         f"1D 62+ engine trades, {len(res['coins'])} coins from {bt._d(res['from'])} · engine entry / stop / TP1-3, "
         f"thirds, stop to entry after TP1, 7 days, costs · halves split {bt._d(res['split'])}", ""]
    for h in ("older", "recent"):
        e = res["halves"].get(h)
        if not e:
            continue
        L.append(f"━━ {h} half")
        for v in VARIANTS:
            mark = "" if v == "A" else (" ✅" if e["ok"][v] else " ❌")
            L.append(f"  {NAMES[v]}: {bt._s(e['stats'][v])}{mark}")
        L.append("")
    L.append(f"Verdict: {'SWITCH to ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'keep the fixed TP3 ❌'}")
    L.append(f"Rule fixed before the run: avg R ≥ A's + {GAP_R:g} AND return/DD ≥ A's, in BOTH halves. "
             "Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="trail_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--h1-days", type=int, default=900)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
        fg = {}
    h1 = cc.fetch_history({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.h1_days, log=log)
    series = {s: {"1H": v["1H"], "1D": cc.aggregate(v["1H"], 24)} for s, v in h1.items()}
    res = run(series, fg, log=log)
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
