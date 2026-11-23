"""
The stop-to-entry move after TP1 on 1D picks: keep it, drop it, or move it
later? A pre-registered test (written Nov 23 2026, before any run).

Why: record_gap_check found the live 1D record's whole gap between the
tracker (+0.06R) and the 4H re-settle (+0.34R) in 28 trades that hit TP1,
came back to entry within hours (seen on the tracker's 1H candles) and were
stopped there - while most of them went on to a profit. So the rule may cut
winners. 28 trades in one market is not a test, so this is.

The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at on
UTC days built from 1H candles + Fear & Greed), all scan coins, one per coin
per direction per 7 days; the ENGINE's own entry, stop and TP1/2/3; the
entry must fill within 24 hours; a third off at each target; closed at
market after 7 days (close_publish.WINDOWS["1D"]); walked on 1H candles,
stop first inside an hour; 0.13% costs. R = result / the original risk.
  A  as now: after TP1 the stop moves to entry
  B  the original stop stays after TP1
  C  the stop moves to entry only after TP2
  D  after TP1 the stop moves halfway from the original stop to entry
Hourly history reaches ~2.5 years, so the two periods are the OLDER and
RECENT halves of the signals (split at the middle signal), not 2018 / 2022.

A VARIANT REPLACES A only if, in BOTH halves: its average R per trade is
>= A's + 0.03 AND its return / max drawdown (R equity curve, booked at the
close hour) is >= A's. Of those that pass, the one with the higher worse-half
average R. Coins move together, so treat a borderline pass with care.

    python -m be_rule_test --h1-days 900
"""
from __future__ import annotations

import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
HOUR_MS = 3_600_000
FLOOR = 62.0
FILL_H, HOLD_H = 24, 7 * 24
COOLDOWN_D = 7
COST = rv.COST
VARIANTS = ("A", "B", "C", "D")
GAP_R = 0.03


def walk(direction: str, entry: float, stop: float, tps: Sequence[float], bars: Sequence[Dict],
         variant: str) -> Dict:
    sgn = 1 if direction == "LONG" else -1
    risk = abs(entry - stop)
    if risk <= 0 or not tps or sgn * (entry - stop) <= 0:
        return {"result": "skip"}
    fill = next((k for k, c in enumerate(bars[:FILL_H]) if float(c["low"]) <= entry <= float(c["high"])), None)
    if fill is None:
        return {"result": "not_filled"} if len(bars) >= FILL_H else {"result": "pending"}
    life = bars[fill:fill + HOLD_H]
    if len(life) < HOLD_H:
        return {"result": "pending"}
    share = 1.0 / len(tps)
    left, banked, cur, pending, hits = 1.0, 0.0, stop, list(tps), 0
    for k, c in enumerate(life):
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= cur) if sgn > 0 else (hi >= cur):
            banked += left * sgn * (cur - entry)
            return _done(banked, entry, risk, fill + k, "stop")
        now = [p for p in pending if ((hi >= p) if sgn > 0 else (lo <= p))]
        for p in now:
            banked += share * sgn * (p - entry)
            left -= share
            pending.remove(p)
            hits += 1
        if not pending:
            return _done(banked, entry, risk, fill + k, "target")
        if now:
            if variant == "A" and hits >= 1:
                cur = entry
            elif variant == "C" and hits >= 2:
                cur = entry
            elif variant == "D" and hits >= 1:
                cur = (stop + entry) / 2
    banked += left * sgn * (float(life[-1]["close"]) - entry)
    return _done(banked, entry, risk, fill + len(life) - 1, "expired")


def _done(banked: float, entry: float, risk: float, k: int, how: str) -> Dict:
    return {"result": how, "r": banked / risk - COST * entry / risk, "hours": k + 1}


def scan(h1: Sequence[Dict], daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    starts = [int(b["timestamp"]) for b in h1]
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < COOLDOWN_D:
            continue
        try:
            entry, stop = float(sig["entry"]), float(sig["sl"])
            tps = [float(x) for x in sig.get("tp_targets") or [] if x is not None]
        except (KeyError, TypeError, ValueError):
            continue
        if not tps:
            continue
        j0 = bisect.bisect_left(starts, ts + DAY_MS)          # the first hour after the daily close
        bars = h1[j0:j0 + FILL_H + HOLD_H]
        res = {v: walk(d, entry, stop, tps, bars, v) for v in VARIANTS}
        if res["A"]["result"] in ("skip", "pending"):
            continue
        last[d] = i
        out.append({"symbol": sym, "ts": ts, "direction": d, **res})
    return out


def stats(evs: Sequence[Dict], v: str) -> Dict:
    done = [(e["ts"] + DAY_MS + e[v]["hours"] * HOUR_MS, e[v]["r"]) for e in evs if "r" in e[v]]
    rs = [r for _, r in done]
    eq = peak = dd = 0.0
    for _, r in sorted(done):
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    n = len(rs)
    total = sum(rs)
    return {"n": n, "avg_r": round(total / n, 3) if n else None, "total_r": round(total, 1),
            "win_pct": round(100 * sum(r > 0 for r in rs) / n, 1) if n else None,
            "max_dd": round(dd, 1), "ratio": round(total / dd, 2) if dd > 0 else None,
            "stops": sum(1 for e in evs if e[v].get("result") == "stop")}


def half_eval(evs: Sequence[Dict]) -> Dict:
    st = {v: stats(evs, v) for v in VARIANTS}
    a = st["A"]
    ok = {}
    for v in VARIANTS[1:]:
        s = st[v]
        ok[v] = (s["avg_r"] is not None and a["avg_r"] is not None and s["avg_r"] >= a["avg_r"] + GAP_R
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
    passed = [v for v in VARIANTS[1:] if halves["older"]["ok"][v] and halves["recent"]["ok"][v]]
    adopt = max(passed, key=lambda v: min(halves["older"]["stats"][v]["avg_r"],
                                          halves["recent"]["stats"][v]["avg_r"])) if passed else None
    return {"halves": halves, "adopt": adopt, "coins": sorted({e["symbol"] for e in evs}),
            "split": split, "from": ts[0]}


NAMES = {"A": "A · stop to entry after TP1 (as now)", "B": "B · keep the original stop",
         "C": "C · stop to entry after TP2", "D": "D · after TP1, halfway to entry"}


def _d(ms: Optional[int]) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "-"


def _s(st: Dict) -> str:
    if not st["n"]:
        return "no trades"
    return (f"{st['n']} · avg {st['avg_r']:+.3f}R · total {st['total_r']:+.0f}R · win {st['win_pct']}% · "
            f"max DD {st['max_dd']}R · return/DD {st['ratio']} · {st['stops']} stopped")


def render_telegram(res: Dict) -> str:
    L = ["🛑 After TP1: stop to entry, or not? (pre-registered, 1H candles)",
         f"1D 62+ engine trades, {len(res['coins'])} coins from {_d(res['from'])} · engine entry / stop / TP1-3, "
         "thirds off, 7 days max, costs · halves split {}".format(_d(res["split"])), ""]
    for h, nm in (("older", "older half"), ("recent", "recent half")):
        e = res["halves"].get(h)
        if not e:
            continue
        L.append(f"━━ {nm}")
        for v in VARIANTS:
            mark = "" if v == "A" else (" ✅" if e["ok"][v] else " ❌")
            L.append(f"  {NAMES[v]}: {_s(e['stats'][v])}{mark}")
        L.append("")
    L.append(f"Verdict: {'REPLACE A with ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'none beats A ❌ (the stop keeps moving to entry after TP1)'}")
    L.append(f"Rule fixed before the run: avg R ≥ A's + {GAP_R:g} AND return/DD ≥ A's, in BOTH halves. "
             "Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="be_rule_test", description=__doc__.split("\n\n")[0])
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
