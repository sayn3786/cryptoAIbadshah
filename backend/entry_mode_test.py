"""
How should a 1D pick be ENTERED: the engine's resting limit, or at the
close? A pre-registered test (written Nov 27 2026, before any run).

Why: the new Hyperliquid 1D book (hl_1d) places a resting limit at the
engine's entry and cancels it after 24 hours unfilled (the owner's first
test trade, RENDER short, sat unfilled above the market). Whether waiting
for that price beats simply taking the trade at the daily close was never
tested.

The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at on
UTC days built from 1H candles + Fear & Greed), all scan coins, one per coin
per direction per 7 days - be_rule_test's population. The engine's stop and
TP1/2/3, a third off at each target, stop to entry after TP1 (the live rule),
7 days max, walked on 1H candles, stop first inside an hour, 0.13% costs.
Only the ENTRY differs:
  limit   the engine's entry, filled within 24 hours (as live now)
  close   at the daily close (the first hour's open after it)
  mid     a limit halfway between the close and the engine's entry, 24 hours
A close / mid entry already beyond the stop or TP1 is not taken. R is per
signal: a trade not taken or not filled counts 0 (the money sat idle), so a
method that skips good trades pays for it.
Hourly history reaches ~2.5 years: OLDER and RECENT halves of the signals.

A METHOD REPLACES limit only if, in BOTH halves: R per signal >= limit's +
0.03 AND return / max drawdown >= limit's. Of those that pass, the higher
worse-half R per signal. Coins move together.

    python -m entry_mode_test --h1-days 900
"""
from __future__ import annotations

import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import be_rule_test as bt
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS, HOUR_MS = bt.DAY_MS, bt.HOUR_MS
MODES = ("limit", "close", "mid")
GAP_R = 0.03


def trade(direction: str, entry: float, stop: float, tps: Sequence[float], bars: Sequence[Dict]) -> Dict:
    """One entry, walked as live (be_rule_test.walk, variant A)."""
    sgn = 1 if direction == "LONG" else -1
    if sgn * (entry - stop) <= 0 or not tps or sgn * (tps[0] - entry) <= 0:
        return {"result": "skip"}
    return bt.walk(direction, entry, stop, tps, bars, "A")


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
            eng, stop = float(sig["entry"]), float(sig["sl"])
            tps = [float(x) for x in sig.get("tp_targets") or [] if x is not None]
        except (KeyError, TypeError, ValueError):
            continue
        if not tps:
            continue
        j0 = bisect.bisect_left(starts, ts + DAY_MS)
        bars = h1[j0:j0 + bt.FILL_H + bt.HOLD_H]
        if not bars:
            continue
        close = float(bars[0]["open"])
        res = {"limit": trade(d, eng, stop, tps, bars),
               "close": trade(d, close, stop, tps, bars),
               "mid": trade(d, (close + eng) / 2, stop, tps, bars)}
        if any(r["result"] == "pending" for r in res.values()):
            continue
        last[d] = i
        out.append({"symbol": sym, "ts": ts, "direction": d,
                    **{m: res[m] for m in MODES}})
    return out


def stats(evs: Sequence[Dict], m: str) -> Dict:
    pts = []
    for e in evs:
        r = e[m].get("r")
        pts.append((e["ts"] + DAY_MS + int(e[m].get("hours") or 0) * HOUR_MS, r if r is not None else 0.0))
    rs = [r for _, r in pts]
    traded = [e[m]["r"] for e in evs if e[m].get("r") is not None]
    eq = peak = dd = 0.0
    for _, r in sorted(pts):
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    n, total = len(rs), sum(rs)
    return {"signals": n, "traded": len(traded),
            "r_per_signal": round(total / n, 3) if n else None,
            "avg_r_traded": round(sum(traded) / len(traded), 3) if traded else None,
            "total_r": round(total, 1), "max_dd": round(dd, 1),
            "ratio": round(total / dd, 2) if dd > 0 else None}


def half_eval(evs: Sequence[Dict]) -> Dict:
    st = {m: stats(evs, m) for m in MODES}
    a = st["limit"]
    ok = {}
    for m in MODES[1:]:
        s = st[m]
        ok[m] = (s["r_per_signal"] is not None and a["r_per_signal"] is not None
                 and s["r_per_signal"] >= a["r_per_signal"] + GAP_R
                 and s["ratio"] is not None and (a["ratio"] is None or s["ratio"] >= a["ratio"]))
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
    passed = [m for m in MODES[1:] if halves["older"]["ok"][m] and halves["recent"]["ok"][m]]
    adopt = max(passed, key=lambda m: min(halves["older"]["stats"][m]["r_per_signal"],
                                          halves["recent"]["stats"][m]["r_per_signal"])) if passed else None
    return {"halves": halves, "adopt": adopt, "coins": sorted({e["symbol"] for e in evs}),
            "split": split, "from": ts[0]}


NAMES = {"limit": "engine limit, 24h (as now)", "close": "at the daily close", "mid": "limit halfway, 24h"}


def _s(st: Dict) -> str:
    if not st["signals"]:
        return "no signals"
    return (f"{st['r_per_signal']:+.3f}R per signal · {st['traded']}/{st['signals']} traded · avg "
            f"{st['avg_r_traded']:+.3f}R a trade · total {st['total_r']:+.0f}R · max DD {st['max_dd']}R · "
            f"return/DD {st['ratio']}")


def render_telegram(res: Dict) -> str:
    L = ["🎯 1D entry: wait at the engine's price, or take it at the close? (pre-registered, 1H candles)",
         f"1D 62+ engine trades, {len(res['coins'])} coins from {bt._d(res['from'])} · engine stop / TP1-3, thirds, "
         f"stop to entry after TP1, 7 days, costs · halves split {bt._d(res['split'])}", ""]
    for h in ("older", "recent"):
        e = res["halves"].get(h)
        if not e:
            continue
        L.append(f"━━ {h} half")
        for m in MODES:
            mark = "" if m == "limit" else (" ✅" if e["ok"][m] else " ❌")
            L.append(f"  {NAMES[m]}: {_s(e['stats'][m])}{mark}")
        L.append("")
    L.append(f"Verdict: {'SWITCH to ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'keep the engine limit ❌'}")
    L.append(f"Rule fixed before the run: R per signal ≥ the limit's + {GAP_R:g} AND return/DD ≥ the limit's, in "
             "BOTH halves (unfilled / not taken = 0R). Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="entry_mode_test", description=__doc__.split("\n\n")[0])
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
