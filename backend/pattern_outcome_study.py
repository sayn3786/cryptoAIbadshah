"""
Chart patterns: how many hit their target, how many failed, and what set the
winners apart? (written Nov 15 2026, before any run)

The dashboard's pattern detectors (patterns.py: flags, double tops / bottoms,
head & shoulders, triangles and wedges) are replayed candle by candle on
history exactly as live: closed candles only, the live lookback (TF_LIMIT),
the live flag pole minimum. Every CONFIRMED breakout becomes one event, on the
candle it confirmed:

  entry    the breakout candle's close
  target   the pattern's own target
  stop     where the pattern is wrong:
             flag            the flag low (bull) / flag high (bear)
             double / triple the tops (bear) / bottoms (bull)
             head & shoulders the head
             triangle/wedge  the opposite rail at the breakout
  outcome  walked forward on the same chart's candles, at most 50 candles:
           TARGET first, STOP first (a candle touching both = stop), or
           OPEN at the end (marked to the last close). Costs 0.13% round trip.

Fair baseline: when price wanders randomly, the chance of reaching the target
before the stop is stop distance / (stop distance + target distance). Every
event carries that "chance"; a pattern only has an edge when it hits its
target MORE often than its own chance. R = profit in units of the stop
distance (target hit = target / stop distance, stop = -1, after costs).

Learning, fixed before the run (so a lucky slice can't be "learned"):
  DISCOVERY  events before the last 365 days
  HOLDOUT    the last 365 days, looked at only to check the discovery picks
  A slice (one feature value, e.g. "1D chart", "breakout volume strong",
  "with the 200 EMA trend") is PROMISING on discovery with 30+ resolved
  events, a target-hit rate 8+ points above its chance, z >= 2 (hits vs
  chance, binomial) and average R >= +0.15. It is CONFIRMED only if the
  holdout then has 20+ resolved events, z >= 1.65 and average R > 0. About
  30 slices are tested, so without the z checks some would pass by luck (a
  random-price dry run "confirmed" one). Only confirmed slices are
  candidates for "show only these patterns".

    python -m pattern_outcome_study --days 2000 --h4-days 720
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
TF_MS = {"4H": 4 * HOUR_MS, "1D": DAY_MS, "1W": 7 * DAY_MS}
MAX_HOLD = 50
COST = 0.0013
HOLDOUT_DAYS = 365
MIN_DISC, MIN_HOLD = 30, 20
Z_DISC, Z_HOLD = 2.0, 1.65
EDGE_PTS, MIN_AVG_R = 8.0, 0.15


# ── indicators at entry ──────────────────────────────────────────────────────

def _ema(vals: Sequence[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    k, e = 2 / (n + 1), sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def _atr(c: Sequence[Dict], n: int = 14) -> Optional[float]:
    if len(c) < n + 1:
        return None
    trs = []
    for k in range(len(c) - n, len(c)):
        h, lo, pc = float(c[k]["high"]), float(c[k]["low"]), float(c[k - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / n


# ── detection ────────────────────────────────────────────────────────────────

def detect(window: List[Dict], tf: str) -> List[Dict]:
    """The live detectors on one closed-candle window."""
    import candle_analysis as ca
    import patterns as pt
    out = []
    try:
        mp = ca.TF_MIN_POLE_PCT.get(tf, 5.0)
        for f in pt.pick_dominant_flags(pt.detect_flags(window, tf, 1.0, min_pole_pct=mp)):
            out.append({**f, "_kind": "flag"})
    except Exception:                                    # noqa: BLE001
        pass
    for fn, kind in ((pt.detect_reversals, "reversal"), (pt.detect_triangles_wedges, "triangle")):
        try:
            out += [{**p, "_kind": kind} for p in fn(window, tf)]
        except Exception:                                # noqa: BLE001
            pass
    return out


def _type(p: Dict) -> str:
    if p["_kind"] == "flag":
        return "bull flag" if p.get("direction") == "bullish" else "bear flag"
    t = str(p.get("type") or p["_kind"])
    return {"double_top": "double top", "double_bottom": "double bottom",
            "triple_top": "triple top", "triple_bottom": "triple bottom",
            "head_shoulders": "head & shoulders",
            "inverse_head_shoulders": "inverse head & shoulders"}.get(t, t.replace("_", " "))


def _stop(p: Dict, bull: bool) -> Optional[float]:
    k = p["_kind"]
    if k == "flag":
        return p.get("flag_low") if bull else p.get("flag_high")
    if k == "reversal":
        if p.get("head_level") is not None:
            return p["head_level"]
        if p.get("peak_level") is not None:
            return p["peak_level"]
        pts = [float(x["price"]) for x in p.get("points") or []]
        return (min(pts) if bull else max(pts)) if pts else None
    return p.get("lower_now") if bull else p.get("upper_now")


def event_from(p: Dict, window: List[Dict], tf: str) -> Optional[Dict]:
    """A confirmed breakout ON the window's last candle, as a trade."""
    last = window[-1]
    bts = p.get("breakout_ts") if p["_kind"] == "flag" else p.get("break_ts")
    if not p.get("confirmed") or bts is None or int(bts) != int(last["timestamp"]):
        return None
    d = p.get("direction")
    if d == "neutral" or d not in ("bullish", "bearish"):
        d = "bullish" if p.get("breakout_dir") == "up" else "bearish" \
            if p.get("breakout_dir") == "down" else None
    if d is None:
        return None
    bull = d == "bullish"
    entry, tgt, stop = float(last["close"]), p.get("target"), _stop(p, bull)
    if tgt is None or stop is None:
        return None
    tgt, stop = float(tgt), float(stop)
    sgn = 1 if bull else -1
    if sgn * (tgt - entry) <= 0 or sgn * (entry - stop) <= 0:
        return None
    closes = [float(c["close"]) for c in window]
    atr = _atr(window)
    e50, e200 = _ema(closes, 50), _ema(closes, 200)
    body = abs(float(last["close"]) - float(last["open"]))
    vol = p.get("breakout_volume") or {}
    return {"tf": tf, "type": _type(p), "direction": d, "ts": int(last["timestamp"]),
            "entry": entry, "target": tgt, "stop": stop,
            "feat": {
                "chart": tf, "pattern": _type(p), "direction": d,
                "breakout volume": vol.get("level") or "no data",
                "vs 50 EMA": _trend(entry, e50, bull),
                "vs 200 EMA": _trend(entry, e200, bull),
                "reward:risk": _bucket(abs(tgt - entry) / abs(entry - stop),
                                       ((1, "under 1"), (2, "1-2"), (4, "2-4")), "4+"),
                "target size": _bucket(abs(tgt - entry) / atr if atr else None,
                                       ((2, "under 2 ATR"), (5, "2-5 ATR"), (10, "5-10 ATR")),
                                       "10+ ATR"),
                "breakout candle": _bucket(body / atr if atr else None,
                                           ((0.5, "small (<0.5 ATR)"), (1.5, "normal")),
                                           "big (>1.5 ATR)"),
            }}


def _trend(px: float, ema: Optional[float], bull: bool) -> str:
    if ema is None:
        return "no data"
    return "with trend" if (px > ema) == bull else "against trend"


def _bucket(v: Optional[float], edges, top: str) -> str:
    if v is None:
        return "no data"
    for lim, name in edges:
        if v < lim:
            return name
    return top


# ── outcome ──────────────────────────────────────────────────────────────────

def outcome(ev: Dict, after: Sequence[Dict]) -> Dict:
    bull = ev["direction"] == "bullish"
    e, t, s = ev["entry"], ev["target"], ev["stop"]
    risk = abs(e - s)
    cost_r = COST * e / risk
    for k, c in enumerate(after[:MAX_HOLD]):
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= s) if bull else (hi >= s):
            return {"result": "stop", "r": -1 - cost_r, "bars": k + 1}
        if (hi >= t) if bull else (lo <= t):
            return {"result": "target", "r": abs(t - e) / risk - cost_r, "bars": k + 1}
    if not after:
        return {"result": "pending", "r": None, "bars": 0}
    last = float(after[:MAX_HOLD][-1]["close"])
    r = ((last - e) if bull else (e - last)) / risk - cost_r
    return {"result": "open" if len(after) >= MAX_HOLD else "pending", "r": r,
            "bars": min(len(after), MAX_HOLD)}


def scan(candles: Sequence[Dict], tf: str, lookback: int, *, sym: str = "",
         detect_fn: Callable = detect) -> List[Dict]:
    """Every confirmed breakout on `candles`, with its outcome."""
    seen, out = set(), []
    for i in range(lookback // 2, len(candles)):
        window = list(candles[max(0, i + 1 - lookback):i + 1])
        for p in detect_fn(window, tf):
            ev = event_from(p, window, tf)
            if not ev:
                continue
            key = (ev["type"], ev["ts"])
            if key in seen:
                continue
            seen.add(key)
            ev.update(outcome(ev, candles[i + 1:]))
            ev["symbol"] = sym
            ev["chance"] = abs(ev["entry"] - ev["stop"]) / (
                abs(ev["entry"] - ev["stop"]) + abs(ev["target"] - ev["entry"]))
            out.append(ev)
    return out


# ── stats ────────────────────────────────────────────────────────────────────

def stats(evs: Sequence[Dict]) -> Dict:
    done = [e for e in evs if e["result"] in ("target", "stop", "open")]
    res = [e for e in done if e["result"] in ("target", "stop")]
    hits = sum(1 for e in res if e["result"] == "target")
    rs = [e["r"] for e in done]
    out = {"n": len(done), "target": hits, "stop": len(res) - hits,
           "open": len(done) - len(res), "resolved": len(res),
           "hit_pct": round(100 * hits / len(res), 1) if res else None,
           "chance_pct": round(100 * sum(e["chance"] for e in res) / len(res), 1) if res else None,
           "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
           "total_r": round(sum(rs), 1) if rs else None,
           "avg_bars": round(sum(e["bars"] for e in res) / len(res), 1) if res else None}
    out["edge_pts"] = (round(out["hit_pct"] - out["chance_pct"], 1)
                       if out["hit_pct"] is not None else None)
    # hits vs chance in standard deviations (sum of Bernoulli(chance))
    var = sum(e["chance"] * (1 - e["chance"]) for e in res)
    out["z"] = round((hits - sum(e["chance"] for e in res)) / var ** 0.5, 2) if var > 0 else None
    return out


def slices(evs: Sequence[Dict]) -> Dict[tuple, List[Dict]]:
    by: Dict[tuple, List[Dict]] = {}
    for e in evs:
        for k, v in e["feat"].items():
            if v in ("n/a", "no data"):
                continue
            by.setdefault((k, v), []).append(e)
    return by


def learn(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    disc = [e for e in evs if e["ts"] < cutoff_ms]
    hold = [e for e in evs if e["ts"] >= cutoff_ms]
    ds, hs = slices(disc), slices(hold)
    rows = []
    for key, d_evs in ds.items():
        d = stats(d_evs)
        promising = (d["resolved"] >= MIN_DISC and (d["edge_pts"] or -99) >= EDGE_PTS
                     and (d["z"] or -99) >= Z_DISC and (d["avg_r"] or -99) >= MIN_AVG_R)
        h = stats(hs.get(key, []))
        confirmed = promising and (h["resolved"] >= MIN_HOLD and (h["z"] or -99) >= Z_HOLD
                                   and (h["avg_r"] or -99) > 0)
        rows.append({"feature": key[0], "value": key[1], "disc": d, "hold": h,
                     "promising": promising, "confirmed": confirmed})
    return {"disc": stats(disc), "hold": stats(hold), "rows": rows}


def by_pattern(evs: Sequence[Dict]) -> List[Dict]:
    groups: Dict[tuple, List[Dict]] = {}
    for e in evs:
        groups.setdefault((e["type"], e["tf"]), []).append(e)
    return sorted(({"type": k[0], "tf": k[1], **stats(v)} for k, v in groups.items()),
                  key=lambda r: -r["n"])


# ── report ───────────────────────────────────────────────────────────────────

def _s(st: Dict) -> str:
    if not st.get("resolved"):
        return f"{st.get('n', 0)} events, none resolved"
    return (f"{st['n']} · 🎯 {st['target']} / ❌ {st['stop']} / ⏳ {st['open']} · hit "
            f"{st['hit_pct']}% vs chance {st['chance_pct']}% ({st['edge_pts']:+g}, z {st['z']}) · avg "
            f"{st['avg_r']:+.2f}R")


def render_telegram(res: Dict) -> str:
    L = res["learn"]
    lines = ["🧩 Chart patterns: which hit the target and which failed?",
             f"{len(res['coins'])} coins · 4H {res['h4_days']}d, 1D/1W {res['days']}d · confirmed "
             "breakouts only, live detectors, closed candles",
             "Entry at the breakout close · stop where the pattern is wrong · max 50 candles · "
             "'chance' = how often a random walk would reach the target first", "",
             f"ALL (discovery, before {res['cutoff'][:10]}): {_s(L['disc'])}",
             f"ALL (holdout, last 365d): {_s(L['hold'])}", "",
             "━━ By pattern and chart (all years)"]
    for r in res["patterns"]:
        if r["n"] >= 5:
            lines.append(f"• {r['type']} {r['tf']}: {_s(r)}")
    lines += ["", "━━ What set winners apart (discovery → checked on holdout)"]
    rows = sorted(L["rows"], key=lambda r: (not r["confirmed"], not r["promising"],
                                            -(r["disc"]["edge_pts"] or -99)))
    shown = 0
    for r in rows:
        if not r["promising"] and shown >= 12:
            break
        tag = "✅ CONFIRMED" if r["confirmed"] else "🟡 promising, failed holdout" \
            if r["promising"] else "·"
        if r["disc"]["resolved"] < MIN_DISC:
            continue
        lines.append(f"{tag} {r['feature']} = {r['value']}")
        lines.append(f"   discovery: {_s(r['disc'])}")
        lines.append(f"   holdout:   {_s(r['hold'])}")
        shown += 1
    worst = sorted((r for r in L["rows"] if r["disc"]["resolved"] >= MIN_DISC),
                   key=lambda r: r["disc"]["avg_r"] if r["disc"]["avg_r"] is not None else 0)[:5]
    lines += ["", "━━ Weakest slices (discovery)"]
    for r in worst:
        lines.append(f"• {r['feature']} = {r['value']}: {_s(r['disc'])} · holdout {_s(r['hold'])}")
    lines += ["", f"Rule fixed before the run: promising = {MIN_DISC}+ resolved, hit rate "
              f"{EDGE_PTS:g}+ pts above chance with z ≥ {Z_DISC:g}, avg R ≥ {MIN_AVG_R:+g} on "
              f"discovery; confirmed = holdout {MIN_HOLD}+ resolved, z ≥ {Z_HOLD:g}, avg R > 0. "
              "z = how many standard deviations the hits are above chance (2 ≈ 1-in-40 luck)."]
    return "\n".join(lines)


def run(series: Dict[str, Dict[str, List[Dict]]], *, end_ms: int, log=print,
        detect_fn: Callable = detect) -> Dict:
    import app as appmod
    evs = []
    for sym, by_tf in series.items():
        for tf, candles in by_tf.items():
            evs += scan(candles, tf, appmod.TF_LIMIT.get(tf, 240), sym=sym, detect_fn=detect_fn)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"events": evs, "learn": learn(evs, cutoff), "patterns": by_pattern(evs),
            "cutoff": sb.sc._iso(cutoff), "coins": sorted(series)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="pattern_outcome_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--h4-days", type=int, default=720)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}
    end_ms = int(time.time() * 1000) // HOUR_MS * HOUR_MS
    daily = rs.fetch_daily(syms, args.days, log=log)
    h1 = cc.fetch_history(syms, args.h4_days, log=log) if args.h4_days else {}
    series = {}
    for s in syms:
        by = {}
        if s in h1:
            by["4H"] = cc.aggregate(h1[s]["1H"], 4)
        if s in daily:
            by["1D"] = daily[s]
            by["1W"] = rs.weekly_from_daily(daily[s])
        if by:
            series[s] = by
    res = run(series, end_ms=end_ms, log=log)
    res.update(days=args.days, h4_days=args.h4_days)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(series)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
