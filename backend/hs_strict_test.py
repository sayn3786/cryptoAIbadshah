"""
A stricter Head & Shoulders detector? A pre-registered test (written Nov 27
2026, before any run).

Why: the owner showed a 4H H&S (head 204, neckline 178.3) whose neckline is
the AVERAGE of two dips ~9% apart (~187 and ~170) - a level price never
turned at - and whose price had already closed far below that neckline
between the head and the right shoulder, then bounced. The live detector
(patterns.detect_reversals) checks neither.

The strict detector is the live one plus two rules, for H&S and inverse H&S:
  1. the two dips the neckline is drawn from are within 4% of each other
     (REV_PEAK_TOL, the double-top tolerance)
  2. no candle CLOSED beyond the neckline between the head and the right
     shoulder (the pattern did not already break and recover)

The events: every CONFIRMED H&S / inverse H&S breakout, replayed candle by
candle exactly as pattern_outcome_study does (the live lookback, closed
candles, entry at the breakout close, the pattern's own target, stop at the
head, 50 candles max, 0.13% costs), on 4H (720 days) and 1D (2000 days), all
scan coins, pooled. Each event is tagged strict or not.
  current   every event (what the dashboard shows now)
  strict    only the events passing both rules
Periods: the OLDER and RECENT halves of the events (split at the middle one).

STRICT REPLACES CURRENT only if, in BOTH halves: strict has 30+ resolved
events, its target-hit edge over chance (hit % - chance %) >= current's, its
average R >= current's, AND it beats the events it drops on average R by
z >= 1 (Welch). (Tightened BEFORE the real run: without the z check a random
45% "strict" tag passed 84 of 400 simulations; with it, 5 of 400.) Otherwise the detector stays as it is (and the
card keeps showing patterns like the owner's). Coins move together.

    python -m hs_strict_test --days 2000 --h4-days 720
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence

import pattern_outcome_study as po

TROUGH_TOL = 0.04
MIN_N = 30
MIN_Z = 1.0
HS_TYPES = ("head_shoulders", "inverse_head_shoulders")


def strict_ok(p: Dict, window: Sequence[Dict]) -> bool:
    tr = p.get("troughs") or []
    if len(tr) != 2:
        return False
    a, b = float(tr[0]["price"]), float(tr[1]["price"])
    if abs(a - b) / ((a + b) / 2) > TROUGH_TOL:
        return False
    pts = {x["role"]: x for x in p.get("points") or []}
    head, rs = pts.get("head"), pts.get("right_shoulder")
    if not head or not rs:
        return False
    neck = float(p["neckline"])
    top = p.get("type") == "head_shoulders"
    h_ts, r_ts = int(head["timestamp"]), int(rs["timestamp"])
    for c in window:
        ts = int(c["timestamp"])
        if h_ts < ts < r_ts:
            cl = float(c["close"])
            if (cl < neck) if top else (cl > neck):
                return False
    return True


def detect_hs(window: List[Dict], tf: str) -> List[Dict]:
    import patterns as pt
    try:
        return [{**p, "_kind": "reversal"} for p in pt.detect_reversals(window, tf)
                if p.get("type") in HS_TYPES]
    except Exception:                                    # noqa: BLE001
        return []


def scan(candles: Sequence[Dict], tf: str, lookback: int, *, sym: str = "",
         detect_fn=detect_hs) -> List[Dict]:
    """pattern_outcome_study.scan, keeping whether each breakout passes the
    strict rules (judged on the window it confirmed in)."""
    seen, out = set(), []
    for i in range(lookback // 2, len(candles)):
        window = list(candles[max(0, i + 1 - lookback):i + 1])
        for p in detect_fn(window, tf):
            ev = po.event_from(p, window, tf)
            if not ev:
                continue
            key = (ev["type"], ev["ts"])
            if key in seen:
                continue
            seen.add(key)
            ev.update(po.outcome(ev, candles[i + 1:]))
            ev["symbol"] = sym
            ev["chance"] = abs(ev["entry"] - ev["stop"]) / (
                abs(ev["entry"] - ev["stop"]) + abs(ev["target"] - ev["entry"]))
            ev["strict"] = strict_ok(p, window)
            out.append(ev)
    return out


def half(evs: Sequence[Dict]) -> Dict:
    cur, st = po.stats(evs), po.stats([e for e in evs if e["strict"]])
    loose = po.stats([e for e in evs if not e["strict"]])
    z = _z_vs([e["r"] for e in evs if e["strict"] and e["r"] is not None and e["result"] != "pending"],
              [e["r"] for e in evs if not e["strict"] and e["r"] is not None and e["result"] != "pending"])
    ok = (z is not None and z >= MIN_Z and st["resolved"] >= MIN_N and st["edge_pts"] is not None and cur["edge_pts"] is not None
          and st["edge_pts"] >= cur["edge_pts"] and st["avg_r"] is not None and cur["avg_r"] is not None
          and st["avg_r"] >= cur["avg_r"])
    by_tf = {tf: {"current": po.stats([e for e in evs if e["tf"] == tf]),
                  "strict": po.stats([e for e in evs if e["tf"] == tf and e["strict"]])}
             for tf in sorted({e["tf"] for e in evs})}
    return {"current": cur, "strict": st, "dropped": loose, "z": z, "passes": ok, "by_tf": by_tf}


def _z_vs(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Welch z of mean(a) - mean(b)."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = (va / len(a) + vb / len(b)) ** 0.5
    return round((ma - mb) / se, 2) if se > 0 else None


def run(series: Dict[str, Dict[str, List[Dict]]], *, log=print, lookback=None, detect_fn=detect_hs) -> Dict:
    evs = []
    for sym, by_tf in series.items():
        for tf, candles in by_tf.items():
            lb = lookback or _lookback(tf)
            evs += scan(candles, tf, lb, sym=sym, detect_fn=detect_fn)
        log(f"  {sym}: scanned")                         # no results in the public log
    if not evs:
        return {"halves": {}, "adopt": False, "n": 0, "split": None, "coins": 0}
    ts = sorted(e["ts"] for e in evs)
    split = ts[len(ts) // 2]
    hv = {"older": half([e for e in evs if e["ts"] < split]),
          "recent": half([e for e in evs if e["ts"] >= split])}
    return {"halves": hv, "adopt": all(h["passes"] for h in hv.values()), "n": len(evs), "split": split,
            "coins": len({e["symbol"] for e in evs}), "strict_share": round(
                100 * sum(e["strict"] for e in evs) / len(evs), 1)}


def _lookback(tf: str) -> int:
    import app as appmod
    return appmod.TF_LIMIT.get(tf, 240)


def _d(ms: Optional[int]) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "-"


def _s(st: Dict) -> str:
    if not st.get("resolved"):
        return f"{st.get('n', 0)} events, none resolved"
    return (f"{st['resolved']} resolved · hit {st['hit_pct']}% vs {st['chance_pct']}% chance "
            f"({st['edge_pts']:+} pts) · avg {st['avg_r']:+.2f}R")


def render_telegram(res: Dict) -> str:
    L = ["🧍 A stricter Head & Shoulders? (pre-registered)",
         "H&S + inverse H&S breakouts, 4H (720d) + 1D (2000d), all scan coins · own target, stop at the head, "
         "50 candles, costs",
         "strict = the two neckline dips within 4% AND no close through the neckline between head and right "
         "shoulder", ""]
    if not res.get("halves"):
        return "\n".join(L + ["no events"])
    L.append(f"{res['n']} breakouts, {res['coins']} coins · {res['strict_share']}% pass the strict rules · "
             f"halves split {_d(res['split'])}")
    for k, h in res["halves"].items():
        L += ["", f"━━ {k} half"]
        L.append(f"  current (all): {_s(h['current'])}")
        L.append(f"  strict: {_s(h['strict'])} {'✅' if h['passes'] else '❌'}")
        L.append(f"  dropped by strict: {_s(h['dropped'])} · strict vs dropped avg R: z {h['z']}")
        for tf, v in h["by_tf"].items():
            L.append(f"    {tf}: all {_s(v['current'])} | strict {_s(v['strict'])}")
    L += ["", f"Verdict: {'ADOPT the strict H&S detector ✅' if res['adopt'] else 'keep the current detector ❌'}",
          "Rule fixed before the run: strict needs 30+ resolved, an edge over chance ≥ current's AND avg R ≥ "
          "current's, AND beat the patterns it drops on avg R by z ≥ 1, in BOTH halves (4H + 1D pooled). "
          "Coins move together."]
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="hs_strict_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--h4-days", type=int, default=720)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}
    daily = rs.fetch_daily(syms, args.days, log=log)
    h1 = cc.fetch_history(syms, args.h4_days, log=log) if args.h4_days else {}
    series = {}
    for s in syms:
        by = {}
        if s in h1:
            by["4H"] = cc.aggregate(h1[s]["1H"], 4)
        if s in daily:
            by["1D"] = daily[s]
        if by:
            series[s] = by
    res = run(series, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"breakouts: {res['n']}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
