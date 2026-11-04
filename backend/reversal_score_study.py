"""
A combined REVERSAL SCORE: do several weak reversal tells firing together
call a real turn much better than any one alone?

The 1D reversal audit (reversal_audit, Sep 2022 - Nov 2026) found no single
part that calls turns, but a handful that fired 1.3-1.7x more often at real
turns than at false ones. The score is fixed HERE, from that audit, before it
meets new data - the count of tells that fired the turn's way between the
swing day and its confirmation (day 3), exactly as the audit reads them:

  bottoms  Radar bullish pattern break, momentum against the downtrend
           (combo), volume, Radar bullish RSI divergence, Radar RSI oversold,
           RSI slope, engulfing
  tops     Bollinger, VWAP, momentum with trend (combo), flags, trend
           (EMA / SuperTrend / Ichimoku), Radar bearish pattern break,
           reversal patterns, Radar bearish RSI divergence - all weak at tops

Same turns and grading as the audit: a swing low (high) in a 15%+ down (up)
trend, confirmed 3 days later; from that close, real = +10% the turn's way
before -10% the other way (30 days). Per score: how many turns, how many were
real (precision) against the base rate, the two-sample z of "score >= k" vs
the rest, each half of the window, and the race as a trade (+1 / -1 in units
of 10%, minus 13 bps).

Two windows: the audit's own (IN-SAMPLE - the tells were chosen there, so it
will look better than it is) and the years BEFORE it (UNSEEN - the real test,
fewer coins). A threshold counts only if, on the UNSEEN window, it has 50+
turns, precision 8+ points over the base rate, z >= 2.5, and 4+ points over
the base rate in EACH half (a bare "above it" let an edge confined to one
half through). Private report.

    python -m reversal_score_study --days 1500 --unseen-days 1000
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import reversal_audit as ra

DAY_MS = ra.DAY_MS
BOTTOM_TELLS = ("radar:Bullish pattern break", "combo_momentum_against_trend", "volume",
                "radar:Bullish RSI divergence", "radar:RSI oversold", "rsi_slope", "engulfing")
TOP_TELLS = ("bollinger", "vwap", "combo_momentum_trend", "flags",
             "trend_ema_supertrend_ichimoku", "radar:Bearish pattern break",
             "reversal_patterns", "radar:Bearish RSI divergence")
THRESHOLDS = (2, 3, 4, 5)
MIN_TURNS = 50
MIN_EDGE_PTS = 8.0
HALF_EDGE_PTS = 4.0                # each half at least this far over its own base rate
Z_SIG = 2.5
COST_R = 0.013                     # 13 bps over a 10% move


def score(e: Dict) -> int:
    tells = BOTTOM_TELLS if e["side"] > 0 else TOP_TELLS
    return sum(1 for k in tells if (e["feat"].get(k) or 0) > 0)


def _z(k1: int, n1: int, k2: int, n2: int) -> Optional[float]:
    if not n1 or not n2:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0.0
    return round((k1 / n1 - k2 / n2) / se, 2) if se else None


def _stats(rows: Sequence[Dict]) -> Dict:
    n = len(rows)
    real = sum(1 for e in rows if e["real"])
    return {"n": n, "real": real, "prec": round(100 * real / n, 1) if n else None,
            "avg_r": round((2 * real - n) / n - COST_R, 3) if n else None}


def analyse(events: Sequence[Dict]) -> Dict:
    ts = sorted(e["t"] for e in events)
    mid = ts[len(ts) // 2] if ts else 0
    out: Dict = {"split": mid}
    for side, sgn in (("bottom", 1), ("top", -1)):
        rows = [dict(e, score=score(e)) for e in events if e["side"] == sgn]
        base = _stats(rows)
        by = {}
        for s_ in range(0, (len(BOTTOM_TELLS if sgn > 0 else TOP_TELLS)) + 1):
            grp = [e for e in rows if (e["score"] == s_ if s_ < 5 else e["score"] >= 5)]
            if grp:
                by["5+" if s_ >= 5 else str(s_)] = _stats(grp)
            if s_ >= 5:
                break
        thr = []
        for k in THRESHOLDS:
            hi = [e for e in rows if e["score"] >= k]
            lo = [e for e in rows if e["score"] < k]
            st = _stats(hi)
            halves = [_stats([e for e in hi if (e["t"] < mid) == first]) for first in (True, False)]
            base_h = [_stats([e for e in rows if (e["t"] < mid) == first]) for first in (True, False)]
            z = _z(st["real"], st["n"], sum(1 for e in lo if e["real"]), len(lo))
            edge = (st["prec"] - base["prec"]) if st["n"] and base["n"] else None
            both = all(h["n"] and b["n"] and h["prec"] >= b["prec"] + HALF_EDGE_PTS
                       for h, b in zip(halves, base_h))
            thr.append({"k": k, **st, "edge": round(edge, 1) if edge is not None else None,
                        "z": z, "halves": halves, "both_halves": both,
                        "passes": bool(st["n"] >= MIN_TURNS and (edge or 0) >= MIN_EDGE_PTS
                                       and (z or 0) >= Z_SIG and both)})
        out[side] = {"base": base, "by_score": by, "thresholds": thr}
    return out


def run_window(daily: Dict[str, List[Dict]], *, read_fn: Callable = ra.engine_read,
               log=print, label: str = "") -> Dict:
    events: List[Dict] = []
    for sym, bars in daily.items():
        events += ra.coin_events(sym, bars, read_fn)
        log(f"  {label} {sym}: done")                   # no results in the (public) log
    return {"coins": sorted(daily), "analysis": analyse(events),
            "first": min((int(b[0]["timestamp"]) for b in daily.values() if b), default=0),
            "last": max((int(b[-1]["timestamp"]) for b in daily.values() if b), default=0)}


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _section(name: str, w: Dict, verdict: bool) -> List[str]:
    lines = [f"━ {name}: {_d(w['first'])} → {_d(w['last'])} · {len(w['coins'])} coins"]
    for side, word in (("bottom", "BOTTOMS (bullish tells)"), ("top", "TOPS (bearish tells)")):
        s = w["analysis"][side]
        b = s["base"]
        if not b["n"]:
            lines.append(f"• {word}: no turns")
            continue
        lines.append(f"• {word}: {b['n']} turns · base rate {b['prec']}% real")
        lines.append("  by score: " + " · ".join(f"{k}: {v['n']} @ {v['prec']}%"
                                                for k, v in s["by_score"].items()))
        for t in s["thresholds"]:
            if not t["n"]:
                continue
            h1, h2 = t["halves"]
            mark = "✅ " if (verdict and t["passes"]) else ""
            lines.append(f"  {mark}score ≥ {t['k']}: {t['n']} turns · {t['prec']}% real "
                         f"({t['edge']:+.1f} pts, z {t['z']}) · halves {h1['prec']}% / "
                         f"{h2['prec']}% · as a trade {t['avg_r']:+.2f} (×10%)")
    lines.append("")
    return lines


def render_telegram(ins: Dict, unseen: Optional[Dict]) -> str:
    lines = ["🧩 Combined reversal score (1D): do several weak tells together call real turns?",
             "Score = how many of the audit's tells fired the turn's way by the confirmation "
             "day. Real = +10% the turn's way before -10% (30 days) from the confirmation close.",
             f"✅ only on the UNSEEN window: {MIN_TURNS}+ turns, {MIN_EDGE_PTS:g}+ points over "
             f"the base rate, z ≥ {Z_SIG:g}, {HALF_EDGE_PTS:g}+ points over it in each half", ""]
    lines += _section("IN-SAMPLE (the tells were chosen here - expect it to flatter)", ins, False)
    if unseen:
        lines += _section("UNSEEN (the real test)", unseen, True)
        winners = [f"{side} score ≥ {t['k']}" for side in ("bottom", "top")
                   for t in unseen["analysis"][side]["thresholds"] if t["passes"]]
        lines.append("Verdict: " + ("holds out of sample for " + ", ".join(winners)
                                    + " - worth a forward check" if winners else
                                    "no score threshold holds on the unseen years"))
    lines.append("Turns on different coins in the same week move together, so treat a narrow "
                 "pass with care.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reversal_score_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1500, help="the audit's (in-sample) window")
    ap.add_argument("--unseen-days", type=int, default=1000,
                    help="the window before it (0 = skip)")
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
    ins = run_window({s: b for s, b in daily.items() if len(b) > 120}, log=log, label="in-sample")
    unseen = None
    if args.unseen_days:
        cut = now - args.days * DAY_MS
        old = rs.fetch_daily(syms, args.unseen_days, end_ms=cut, log=log)
        unseen = run_window({s: b for s, b in old.items() if len(b) > 120}, log=log,
                            label="unseen")
    text = render_telegram(ins, unseen)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
