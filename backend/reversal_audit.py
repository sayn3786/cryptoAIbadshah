"""
Reversal audit, daily candles: at every real trend reversal, what was the
engine scoring - and which parts actually told a real reversal from a false one?

Every candidate is a turn a trader could SEE: in a downtrend (close 15%+
under its 30-day high close), a swing low - the lowest low of the 10 days
before it and the CONFIRM days after it - known on the CONFIRM-th day after
(the decision day, like the dashboard's RSI marker). Only what happens AFTER
the decision day grades it, as a race from that day's close:

  real bottom  +RACE_PCT before -RACE_PCT (within 30 days)
  false low    -RACE_PCT first (a day reaching both = false)
  neither      dropped
  real top / false high   the mirror image in an uptrend (close 15%+ over its
               30-day low close)

Why a symmetric race from the decision close: a random walk is 50/50 from
any starting point, so a component can only show lift if it carries real
information. Two earlier drafts leaked: grading by moves that had already
begun on the days read (ROC / RSI slope / MACD "showed" 3-5x on a random
walk), then grading "20% over the low before under it", where a low that had
already bounced more by the decision day was simply closer to the target
(trend / CHoCH parts "showed" lift on a random walk). Both caught by the
random-walk test, which this design passes.

The engine is read exactly as live reads a daily close (candle analysis +
generate_signal on the closed candles up to that day) on the day of the
swing and each day up to the decision day - all before the outcome. For each,
in the turn's favour (bullish points at a bottom, bearish at a top):

  * the engine's call: LONG / NEUTRAL / SHORT and strength on days 0-3
  * every score section (score_breakdown), the RSI reversal / OBV / fib /
    structure adjustments and each Reversal Radar check: how often it fired the
    turn's way at REAL turns vs FALSE ones (lift), and its average points
  * verdicts per component (fired 30+ times), required to hold in both
    halves of the history AND be significant overall (two-sample z):
      helps + underweighted  z >= 2.5, lift >= 1.5 in both halves, below-median
                             points
      noise + heavy          |z| < 1, lift <= 1.15 in both halves, above-median
                             points
      misleads               z <= -2.5, lift < 0.8 in both halves

Private report. ~1500 days, all scanned coins.

With --tunings "-,A,B,C,D,ABC" it reads every turn once per set of the
engine's reversal tuning switches (signals.TUNING; "-" = today's engine) and
compares how well each set's call separates real turns from false ones.

    python -m reversal_audit --days 1500 [--tunings -,A,B]
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

DAY_MS = 86_400_000
LOOKBACK = 240
TREND_DAYS = 30
TREND_PCT = 0.15
SWING_BACK = 10
CONFIRM = 3
RACE_PCT = 0.10
HORIZON = 30
OFFSETS = tuple(range(CONFIRM + 1))       # the swing day .. the decision day
ADJUSTMENTS = ("rsi_reversal_adjustment", "obv_adjustment", "fib_adjustment",
               "structure_adjustment")


def _f(b: Dict, k: str) -> float:
    return float(b[k])


def grade(bars: Sequence[Dict], i: int, side: int) -> Optional[bool]:
    """From the decision day's close: True if price went RACE_PCT the turn's
    way first, False if RACE_PCT the other way first (or both in one day),
    None if neither within HORIZON days."""
    c = i + CONFIRM
    ref = _f(bars[c], "close")
    good = ref * (1 + side * RACE_PCT)
    bad = ref * (1 - side * RACE_PCT)
    for j in range(c + 1, min(len(bars), c + 1 + HORIZON)):
        hi, lo = _f(bars[j], "high"), _f(bars[j], "low")
        if (lo <= bad) if side > 0 else (hi >= bad):
            return False
        if (hi >= good) if side > 0 else (lo <= good):
            return True
    return None


def label_turns(bars: Sequence[Dict]) -> Dict[str, List[int]]:
    """Swing indexes of real bottoms / false lows / real tops / false highs.
    The swing is confirmed CONFIRM days later; the grade uses only the days
    after that."""
    n = len(bars)
    closes = [_f(b, "close") for b in bars]
    lows = [_f(b, "low") for b in bars]
    highs = [_f(b, "high") for b in bars]
    out = {"real_bottom": [], "false_low": [], "real_top": [], "false_high": []}
    for i in range(max(TREND_DAYS, SWING_BACK), n - CONFIRM - 1):
        hi30 = max(closes[i - TREND_DAYS:i])
        lo30 = min(closes[i - TREND_DAYS:i])
        if closes[i] <= hi30 * (1 - TREND_PCT) and \
                lows[i] == min(lows[i - SWING_BACK:i + CONFIRM + 1]):
            g = grade(bars, i, 1)
            if g is not None:
                out["real_bottom" if g else "false_low"].append(i)
        if closes[i] >= lo30 * (1 + TREND_PCT) and \
                highs[i] == max(highs[i - SWING_BACK:i + CONFIRM + 1]):
            g = grade(bars, i, -1)
            if g is not None:
                out["real_top" if g else "false_high"].append(i)
    return out


def engine_read(win: Sequence[Dict], sym: str) -> Dict:
    import candle_analysis
    import signals
    return signals.generate_signal(candle_analysis.build_candle_analysis(list(win), "1D", sym))


def compact(sig: Dict) -> Dict:
    rr = sig.get("reversal_radar") or {}
    return {"direction": sig.get("direction"), "strength": float(sig.get("strength") or 0),
            "breakdown": {k: float(v) for k, v in (sig.get("score_breakdown") or {}).items()
                          if isinstance(v, (int, float))},
            "adj": {k: float(sig.get(k) or 0) for k in ADJUSTMENTS},
            "radar_mode": rr.get("mode"), "radar_level": rr.get("level"),
            "radar_count": rr.get("count") or 0,
            "radar": [s.get("label") for s in rr.get("signals") or [] if s.get("label")]}


def features(reads: Sequence[Optional[Dict]], side: int, mode: str) -> Dict[str, float]:
    """Per component, the strongest points the turn's way over days 0-3 (side
    +1 bottom / -1 top); Reversal Radar checks as 1 when they fired in the
    turn's mode."""
    out: Dict[str, float] = {}
    for r in reads:
        if not r:
            continue
        for k, v in r["breakdown"].items():
            out[k] = max(out.get(k, 0.0), v * side)
        for k, v in r["adj"].items():
            out["adj:" + k] = max(out.get("adj:" + k, 0.0), v * side)
        if r["radar_mode"] == mode:
            for lab in r["radar"]:
                out["radar:" + lab] = 1.0
            out["radar level elevated+"] = max(out.get("radar level elevated+", 0.0),
                                               float(r["radar_level"] in ("elevated", "high")))
    return out


def coin_events(sym: str, bars: Sequence[Dict], read_fn: Callable = engine_read) -> List[Dict]:
    turns = label_turns(bars)
    need = sorted({i + k for idx in turns.values() for i in idx for k in OFFSETS
                   if i + k < len(bars)})
    cache: Dict[int, Optional[Dict]] = {}
    for d in need:
        if d < 60:
            cache[d] = None
            continue
        try:
            cache[d] = compact(read_fn(bars[max(0, d + 1 - LOOKBACK):d + 1], sym) or {})
        except Exception:                                # noqa: BLE001
            cache[d] = None
    out = []
    for kind, idx in turns.items():
        side = 1 if kind in ("real_bottom", "false_low") else -1
        mode = "bottom" if side > 0 else "top"
        for i in idx:
            reads = [cache.get(i + k) for k in OFFSETS]
            calls = []
            for r in reads:
                if not r:
                    calls.append(None)
                    continue
                d = r["direction"]
                s = r["strength"] if d == "LONG" else -r["strength"] if d == "SHORT" else 0.0
                calls.append(s * side)                  # + = the turn's way
            out.append({"symbol": sym, "kind": kind, "t": int(bars[i]["timestamp"]),
                        "real": kind.startswith("real"), "side": side,
                        "calls": calls, "dirs": [r["direction"] if r else None for r in reads],
                        "feat": features(reads, side, mode)})
    return out


def _rate(rows, k):
    return sum(1 for e in rows if e["feat"].get(k, 0) > 0) / len(rows) if rows else 0.0


MIN_FIRED = 30
Z_SIG = 2.5


def components(real: Sequence[Dict], fake: Sequence[Dict]) -> List[Dict]:
    import math
    keys = sorted({k for e in list(real) + list(fake) for k in e["feat"]})
    n1, n2 = len(real), len(fake)
    out = []
    for k in keys:
        rr_, rf = _rate(real, k), _rate(fake, k)
        pts = [e["feat"][k] for e in list(real) + list(fake) if e["feat"].get(k, 0) > 0]
        p = (rr_ * n1 + rf * n2) / (n1 + n2) if n1 + n2 else 0.0
        se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if n1 and n2 and 0 < p < 1 else 0.0
        out.append({"key": k, "real_pct": round(100 * rr_, 1), "false_pct": round(100 * rf, 1),
                    "lift": round(rr_ / rf, 2) if rf else (None if not rr_ else 99.0),
                    "z": round((rr_ - rf) / se, 2) if se else 0.0, "fired": len(pts),
                    "avg_pts": round(statistics.mean(pts), 1) if pts else 0.0})
    return out


def calls(rows: Sequence[Dict]) -> List[Dict]:
    out = []
    for k, off in enumerate(OFFSETS):
        v = [e["calls"][k] for e in rows if e["calls"][k] is not None]
        out.append({"day": off, "n": len(v),
                    "with_pct": round(100 * sum(1 for x in v if x > 0) / len(v), 1) if v else None,
                    "against_pct": round(100 * sum(1 for x in v if x < 0) / len(v), 1) if v else None,
                    "avg": round(statistics.mean(v), 1) if v else None})
    return out


def verdicts(full: Sequence[Dict], older: Sequence[Dict], newer: Sequence[Dict]) -> Dict:
    o = {c["key"]: c for c in older}
    nw = {c["key"]: c for c in newer}
    fired = [c for c in full if c["fired"] >= MIN_FIRED]              # fires often enough
    med = statistics.median([c["avg_pts"] for c in fired if not c["key"].startswith("radar")]
                            or [0])

    def both(c, test):
        a, b = o.get(c["key"]), nw.get(c["key"])
        return a and b and a["lift"] is not None and b["lift"] is not None and test(a["lift"]) \
            and test(b["lift"])
    helps = [c for c in fired if c["z"] >= Z_SIG and both(c, lambda x: x >= 1.5)]
    return {"median_pts": med,
            "underweighted": [c for c in helps if c["avg_pts"] < med
                              or c["key"].startswith("radar")],
            "helps": helps,
            "noise_heavy": [c for c in fired if abs(c["z"]) < 1 and both(c, lambda x: x <= 1.15)
                            and c["avg_pts"] >= med and not c["key"].startswith("radar")],
            "misleads": [c for c in fired if c["z"] <= -Z_SIG and both(c, lambda x: x < 0.8)]}


def analyse(events: Sequence[Dict]) -> Dict:
    ts = sorted(e["t"] for e in events)
    mid = ts[len(ts) // 2] if ts else 0
    out = {"split": mid}
    for side, real_k, fake_k in (("bottom", "real_bottom", "false_low"),
                                 ("top", "real_top", "false_high")):
        real = [e for e in events if e["kind"] == real_k]
        fake = [e for e in events if e["kind"] == fake_k]
        full = components(real, fake)
        older = components([e for e in real if e["t"] < mid], [e for e in fake if e["t"] < mid])
        newer = components([e for e in real if e["t"] >= mid], [e for e in fake if e["t"] >= mid])
        out[side] = {"n_real": len(real), "n_false": len(fake), "calls_real": calls(real),
                     "calls_false": calls(fake), "components": full,
                     "verdicts": verdicts(full, older, newer),
                     "examples": sorted(real, key=lambda e: -e["t"])[:6]}
    return out


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _name(k: str) -> str:
    return k.replace("adj:", "").replace("radar:", "Radar: ")


def render_telegram(res: Dict) -> str:
    lines = ["🔄 Reversal audit (1D): what the engine scored at real turns vs false ones",
             f"{_d(res['first'])} → {_d(res['last'])} · {len(res['coins'])} coins · a swing "
             "low (high) in a 15%+ down (up) trend, confirmed 3 days later; from that close, "
             "real = 10% the turn's way first, false = 10% the other way first (30 days)",
             "Engine read as live on the swing day and each day to the confirmation (day 3), "
             "all before the outcome. 'With' = the call pointed the turn's way.", ""]
    for side, real_name, fake_name in (("bottom", "real bottoms", "false lows"),
                                       ("top", "real tops", "false highs")):
        s = res["analysis"][side]
        want = "LONG" if side == "bottom" else "SHORT"
        lines.append(f"• {side.upper()}S: {s['n_real']} {real_name} vs {s['n_false']} {fake_name}")
        for cr, cf_ in zip(s["calls_real"], s["calls_false"]):
            lines.append(f"  day {cr['day']}: {want} at real {cr['with_pct']}% (against "
                         f"{cr['against_pct']}%, avg {cr['avg']:+}) · at false {cf_['with_pct']}% "
                         f"(avg {cf_['avg']:+})" if cr["n"] and cf_["n"] else
                         f"  day {cr['day']}: no reads")
        comps = [c for c in s["components"] if c["fired"] >= MIN_FIRED]
        best = sorted([c for c in comps if c["lift"] is not None], key=lambda c: -c["lift"])[:8]
        worst = sorted([c for c in comps if c["lift"] is not None], key=lambda c: c["lift"])[:5]
        lines.append("  tells real from false best (fired the turn's way · real % vs false % · "
                     "lift · avg pts):")
        lines += [f"    {_name(c['key'])}: {c['real_pct']}% vs {c['false_pct']}% · ×{c['lift']}"
                  f" (z {c['z']}) · {c['avg_pts']:g}" for c in best]
        lines.append("  fires as often at false turns (or more):")
        lines += [f"    {_name(c['key'])}: {c['real_pct']}% vs {c['false_pct']}% · ×{c['lift']}"
                  f" (z {c['z']}) · {c['avg_pts']:g}" for c in worst]
        v = s["verdicts"]
        lines.append(f"  significant (z ≥ {Z_SIG:g}, 30+ firings) AND holds in both halves "
                     f"(split {_d(res['analysis']['split'])}):")
        lines.append("    raise (helps, underweighted): "
                     + (", ".join(f"{_name(c['key'])} (×{c['lift']}, {c['avg_pts']:g} pts)"
                                  for c in v["underweighted"]) or "none"))
        lines.append("    lower (noise, heavy): "
                     + (", ".join(f"{_name(c['key'])} (×{c['lift']}, {c['avg_pts']:g} pts)"
                                  for c in v["noise_heavy"]) or "none"))
        lines.append("    misleads (fires more at false turns): "
                     + (", ".join(f"{_name(c['key'])} (×{c['lift']})" for c in v["misleads"])
                        or "none"))
        if s["examples"]:
            lines.append(f"  latest {real_name}:")
            for e in s["examples"]:
                d1 = e["calls"][1]
                lines.append(f"    {e['symbol']} {_d(e['t'])}: day+1 "
                             f"{e['dirs'][1] or '—'} {abs(d1) if d1 is not None else '—'}"
                             + (" ✓" if d1 and d1 > 0 else " ✗" if d1 and d1 < 0 else ""))
        lines.append("")
    lines.append("Lift = how much more often it fired at real turns than false ones (×1 = no "
                 "information). A change is worth testing only if it holds in both halves; "
                 "test it with a replay before going live.")
    return "\n".join(lines)


def separation(rows: Sequence[Dict]) -> List[Dict]:
    """Per day 0-3: the call the turn's way at real vs false turns, the gap,
    and how often a 'with' call was a real turn (precision)."""
    real = [e for e in rows if e["real"]]
    fake = [e for e in rows if not e["real"]]
    out = []
    for k, off in enumerate(OFFSETS):
        def with_n(es):
            return sum(1 for e in es if e["calls"][k] is not None and e["calls"][k] > 0)
        nr = sum(1 for e in real if e["calls"][k] is not None)
        nf = sum(1 for e in fake if e["calls"][k] is not None)
        wr, wf = with_n(real), with_n(fake)
        avg_r = [e["calls"][k] for e in real if e["calls"][k] is not None]
        avg_f = [e["calls"][k] for e in fake if e["calls"][k] is not None]
        out.append({"day": off,
                    "with_real": round(100 * wr / nr, 1) if nr else None,
                    "with_false": round(100 * wf / nf, 1) if nf else None,
                    "gap": round(100 * (wr / nr - wf / nf), 1) if nr and nf else None,
                    "precision": round(100 * wr / (wr + wf), 1) if wr + wf else None,
                    "avg_gap": round(statistics.mean(avg_r) - statistics.mean(avg_f), 1)
                    if avg_r and avg_f else None})
    return out


def compare_tunings(daily: Dict[str, List[Dict]], tunings: Sequence[str], *,
                    read_fn: Callable = engine_read, log=print) -> Dict:
    import signals
    out = []
    for t in tunings:
        flags = t.replace("-", "")
        events: List[Dict] = []
        with signals.tuning(*flags):
            for sym, bars in daily.items():
                events += coin_events(sym, bars, read_fn)
        log(f"  tuning {t or '-'}: done")               # no results in the (public) log
        base = {}
        for side, real_k, fake_k in (("bottom", "real_bottom", "false_low"),
                                     ("top", "real_top", "false_high")):
            rows = [e for e in events if e["kind"] in (real_k, fake_k)]
            base[side] = {"n_real": sum(1 for e in rows if e["real"]),
                          "n_false": sum(1 for e in rows if not e["real"]),
                          "sep": separation(rows)}
        out.append({"tuning": t, **base})
    return {"coins": sorted(daily), "variants": out,
            "first": min((int(b[0]["timestamp"]) for b in daily.values() if b), default=0),
            "last": max((int(b[-1]["timestamp"]) for b in daily.values() if b), default=0)}


def render_tunings(res: Dict) -> str:
    lines = ["🎛 Reversal audit (1D) with the tuning switches: does the call separate real "
             "turns from false ones better?",
             f"{_d(res['first'])} → {_d(res['last'])} · {len(res['coins'])} coins · same turns, "
             "same grading (+10% vs -10% from the confirmation close)",
             "A = RSI slope / momentum-against-trend ×1.5 at a downtrend · B = no overbought "
             "penalty in an uptrend (+ Radar) · C = trendline / 200-EMA / flow+trend bullish "
             "×0.5 at a downtrend · D = CVD / FVG ×0.5",
             "gap = the call the turn's way at real turns minus at false ones (points; 0 = no "
             "separation); precision = of the 'with' calls, how many were real turns", ""]
    for side, word in (("bottom", "BOTTOMS (LONG calls)"), ("top", "TOPS (SHORT calls)")):
        v0 = res["variants"][0][side]
        lines.append(f"• {word}: {v0['n_real']} real vs {v0['n_false']} false · base rate "
                     f"{100 * v0['n_real'] / max(1, v0['n_real'] + v0['n_false']):.1f}% real")
        for v in res["variants"]:
            name = "today's engine" if v["tuning"] in ("", "-") else f"tuning {v['tuning']}"
            parts = []
            for d in v[side]["sep"][1:]:
                if d["gap"] is None:
                    continue
                parts.append(f"d{d['day']} {d['with_real']}/{d['with_false']}% gap {d['gap']:+.1f}"
                             + (f" prec {d['precision']}%" if d["precision"] is not None else ""))
            lines.append(f"  {name}: " + " · ".join(parts))
        lines.append("")
    lines.append("Better = a bigger positive gap and a precision above the base rate, in both "
                 "bottoms and tops; confirm with the close-picks replay before going live.")
    return "\n".join(lines)


def compare(daily: Dict[str, List[Dict]], *, read_fn: Callable = engine_read, log=print) -> Dict:
    events: List[Dict] = []
    for sym, bars in daily.items():
        events += coin_events(sym, bars, read_fn)
        log(f"  {sym}: done")                           # no results in the (public) log
    return {"coins": sorted(daily), "analysis": analyse(events),
            "first": min((int(b[0]["timestamp"]) for b in daily.values() if b), default=0),
            "last": max((int(b[-1]["timestamp"]) for b in daily.values() if b), default=0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reversal_audit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1500)
    ap.add_argument("--tunings", default="",
                    help="comma-separated tuning sets, '-' = today's engine (e.g. -,A,B,C,D,ABC)")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    now = int(time.time() * 1000) // DAY_MS * DAY_MS
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS if s in appmod.SYMBOLS}
    daily = rs.fetch_daily(syms, args.days, end_ms=now, log=log)
    daily = {s: b for s, b in daily.items() if len(b) > 120}
    tunings = [t.strip().upper() for t in args.tunings.split(",") if t.strip()]
    if tunings:
        res = compare_tunings(daily, tunings, log=log)
        render = render_tunings
    else:
        res = compare(daily, log=log)
        render = render_telegram
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
