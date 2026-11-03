"""
"Price back at the oversold-bottom low, RSI LOWER than at the bottom" - what
happened next, historically?

The dashboard's RSI reversal marker (candle_analysis.rsi_swing_markers): a
daily swing low (the lowest low of the 3 candles either side) with RSI 14 at
40 or below. It is only KNOWN 3 candles later, so the study only uses it from
then. Within the next MAX_GAP days, the first daily close back within RETEST_PCT
of that low (without a close more than RETEST_PCT under it first) is a RETEST,
classed by RSI then vs RSI at the bottom:

  weaker    RSI at least RSI_GAP lower  (the TAO chart, Nov 2026: 36.5 -> 30.8)
  similar   within RSI_GAP
  stronger  RSI at least RSI_GAP higher (a bullish-divergence-like retest)

From the retest close: the return after 3 / 7 / 14 days, how often the low
broke (a close BREAK_PCT under it within 7 days), and the race to +10% vs
-10% within 30 days (daily highs / lows; a day that reaches both counts as
-10%). Against every day of the same coins (the ordinary drift). Split into
the older and newer half of the history to see whether it holds. Daily, all
scanned coins, ~4 years; private report.

    python -m rsi_retest_study --days 1500
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from indicators import calculate_rsi_series

DAY_MS = 86_400_000
WINDOW = 3                 # candle_analysis.rsi_swing_markers' window
OVERSOLD = 40.0            # candle_analysis.RSI_MARK_OVERSOLD
MAX_GAP = 15
RETEST_PCT = 0.02
RSI_GAP = 2.0
BREAK_PCT = 0.03
RACE_PCT = 0.10
RACE_DAYS = 30
HORIZONS = (3, 7, 14)


def _f(b: Dict, k: str) -> float:
    return float(b[k])


def markers(bars: Sequence[Dict], rsi: Sequence[Optional[float]]) -> List[Dict]:
    """The dashboard's oversold-bottom markers, with the day each is known."""
    out = []
    for i in range(WINDOW, len(bars) - WINDOW):
        r = rsi[i]
        if r is None or r > OVERSOLD:
            continue
        lo = _f(bars[i], "low")
        if lo == min(_f(bars[j], "low") for j in range(i - WINDOW, i + WINDOW + 1)):
            out.append({"i": i, "low": lo, "rsi": r, "known": i + WINDOW})
    return out


def race(bars: Sequence[Dict], t: int) -> Optional[int]:
    """+1 if +RACE_PCT came first, -1 if -RACE_PCT, 0 neither, None if no data."""
    ref = _f(bars[t], "close")
    up, dn = ref * (1 + RACE_PCT), ref * (1 - RACE_PCT)
    end = min(len(bars), t + 1 + RACE_DAYS)
    if end <= t + 1:
        return None
    for j in range(t + 1, end):
        if _f(bars[j], "low") <= dn:
            return -1
        if _f(bars[j], "high") >= up:
            return 1
    return 0 if end == t + 1 + RACE_DAYS else None


def outcome(bars: Sequence[Dict], t: int, low: Optional[float] = None) -> Dict:
    c = _f(bars[t], "close")
    out = {}
    for h in HORIZONS:
        out[f"r{h}"] = (_f(bars[t + h], "close") / c - 1) * 100 if t + h < len(bars) else None
    if low is not None:
        nxt = bars[t + 1:t + 8]
        out["broke"] = any(_f(b, "close") < low * (1 - BREAK_PCT) for b in nxt) \
            if len(nxt) == 7 else None
    out["race"] = race(bars, t)
    return out


def coin_events(sym: str, bars: Sequence[Dict]) -> Dict[str, List[Dict]]:
    closes = [_f(b, "close") for b in bars]
    rsi = calculate_rsi_series(closes)
    events = []
    for m in markers(bars, rsi):
        low = m["low"]
        for t in range(m["known"], min(len(bars), m["i"] + MAX_GAP + 1)):
            c = _f(bars[t], "close")
            if c < low * (1 - RETEST_PCT):
                break                                    # broke first: not a retest
            if c <= low * (1 + RETEST_PCT) and rsi[t] is not None:
                gap = rsi[t] - m["rsi"]
                kind = "weaker" if gap <= -RSI_GAP else "stronger" if gap >= RSI_GAP \
                    else "similar"
                events.append({"symbol": sym, "t": int(bars[t]["timestamp"]), "kind": kind,
                               "bottom_rsi": round(m["rsi"], 1), "rsi": round(rsi[t], 1),
                               "low": low, "days_after": t - m["i"],
                               **outcome(bars, t, low)})
                break
    base = [{"symbol": sym, "t": int(bars[t]["timestamp"]), **outcome(bars, t)}
            for t in range(30, len(bars))]
    return {"events": events, "base": base}


def stats(rows: Sequence[Dict]) -> Dict:
    out: Dict = {"n": len(rows)}
    for h in HORIZONS:
        v = sorted(x[f"r{h}"] for x in rows if x.get(f"r{h}") is not None)
        out[f"avg{h}"] = round(sum(v) / len(v), 2) if v else None
        out[f"med{h}"] = round(v[len(v) // 2], 2) if v else None
        out[f"up{h}"] = round(100 * sum(1 for x in v if x > 0) / len(v), 1) if v else None
    br = [x["broke"] for x in rows if x.get("broke") is not None]
    out["broke_pct"] = round(100 * sum(br) / len(br), 1) if br else None
    rc = [x["race"] for x in rows if x.get("race") in (1, -1)]
    out["up_first_pct"] = round(100 * sum(1 for x in rc if x == 1) / len(rc), 1) if rc else None
    return out


def compare(daily: Dict[str, List[Dict]], *, focus: str = "TAO", log=print) -> Dict:
    ev: List[Dict] = []
    base: List[Dict] = []
    for sym, bars in daily.items():
        got = coin_events(sym, bars)
        ev += got["events"]
        base += got["base"]
        log(f"  {sym}: done")                           # no results in the (public) log
    ts = sorted(e["t"] for e in ev)
    mid = ts[len(ts) // 2] if ts else 0
    groups = []
    for kind in ("weaker", "similar", "stronger"):
        rows = [e for e in ev if e["kind"] == kind]
        groups.append({"label": kind, "all": stats(rows),
                       "older": stats([e for e in rows if e["t"] < mid]),
                       "newer": stats([e for e in rows if e["t"] >= mid])})
    recent_focus = sorted([e for e in ev if e["symbol"] == focus], key=lambda e: -e["t"])[:8]
    return {"coins": sorted(daily), "base": stats(base), "groups": groups, "split": mid,
            "focus": focus, "focus_events": recent_focus,
            "first": min((int(b[0]["timestamp"]) for b in daily.values() if b), default=0),
            "last": max((int(b[-1]["timestamp"]) for b in daily.values() if b), default=0)}


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _line(s: Dict) -> str:
    if not s.get("n"):
        return "no cases"
    return (f"{s['n']} cases · 7d {_pct(s['avg7'])} avg ({_pct(s['med7'])} median, up "
            f"{s['up7']}%) · 14d {_pct(s['avg14'])} · low broke within 7d "
            f"{s['broke_pct'] if s.get('broke_pct') is not None else '—'}% · +10% before -10% "
            f"{s['up_first_pct']}%")


def render_telegram(res: Dict) -> str:
    b = res["base"]
    lines = ["🔁 RSI oversold bottom, then price back at the low: what happened next?",
             f"{_d(res['first'])} → {_d(res['last'])} · {len(res['coins'])} coins · daily "
             f"candles · the dashboard's marker (swing low, RSI ≤ {OVERSOLD:g}), retest = a "
             f"close within {RETEST_PCT:.0%} of that low within {MAX_GAP} days",
             f"Ordinary day (the bar): 7d {_pct(b['avg7'])} avg, up {b['up7']}% · 14d "
             f"{_pct(b['avg14'])} · +10% before -10% {b['up_first_pct']}%", ""]
    names = {"weaker": "RSI LOWER than at the bottom (like TAO now)",
             "similar": "RSI about the same", "stronger": "RSI HIGHER (bullish-divergence-like)"}
    for g in res["groups"]:
        lines.append(f"• {names[g['label']]}")
        lines.append(f"  all: {_line(g['all'])}")
        lines.append(f"  before {_d(res['split'])}: {_line(g['older'])}")
        lines.append(f"  after: {_line(g['newer'])}")
        lines.append("")
    if res["focus_events"]:
        lines.append(f"{res['focus']}'s recent retests (newest first):")
        for e in res["focus_events"]:
            race_txt = {1: "+10% first", -1: "-10% first", 0: "neither in 30d",
                        None: "still open"}[e["race"]]
            lines.append(f"  {_d(e['t'])} · RSI {e['bottom_rsi']} → {e['rsi']} ({e['kind']}) · "
                         f"7d {_pct(e['r7'])} · 14d {_pct(e['r14'])} · {race_txt}")
        lines.append("")
    lines.append("Cases on different coins in the same sell-off move together, so a small "
                 "difference between groups is not a signal; look for one that holds in both "
                 "halves.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rsi_retest_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1500)
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
    res = compare({s: b for s, b in daily.items() if len(b) > 60}, log=log)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
