"""
The TAO case on the DAILY chart: the engine says SHORT at high strength while
the Reversal Radar says "bottoming" - take the short, or the reversal long?
A pre-registered test (written Nov 15 2026, before any run).

The screenshot: TAO 1D, SHORT 91/100 CONFIRMED, Reversal Radar "BOTTOMING
WATCH 5/12 - ELEVATED" with RSI oversold, Extreme fear (F&G 10), Riding lower
Bollinger, Capitulation volume (2.7x) and Stretched below EMA50 (-24.5%).

Replayed on every coin's 1D history with the live code: the dashboard's own
1D analysis (candle_analysis) -> signals.generate_signal (direction,
strength, and its Reversal Radar). Fear & Greed is the real daily history
(alternative.me) for the candle's day; funding, long/short and on-chain
checks have no history here and are not evaluable (fewer radar checks than
live).

  THE CASE    a day the 1D signal is SHORT at strength >= 62 AND the radar is
              in bottoming mode at ELEVATED or HIGH - at most one per coin per
              20 days (overlapping trades share a price path)
  the trades  the SHORT (what the signal says) and the LONG (what the radar
              hints) on the same close: stop and target 1.5 x 1D ATR (1:1, a
              coin flip wins half), max 20 days, stop first, 0.13% costs

Decided before running, on BOTH the older years and the last 365 days:
  SHORT WINS     30+ resolved, the short wins >= 53% with z >= 2, avg R > 0
  LONG WINS      the short wins <= 47% with z <= -2 and the long's avg R > 0
  otherwise      no consistent edge - the two cancel out
Context rows (decide nothing): each of the screenshot's five signs on its
own under a SHORT 62+; SHORT 62+ without a bottoming radar (how 1D shorts do
anyway); the mirror (LONG 62+ into a topping radar). Coins move together, so
z overstates certainty.

    python -m tao_case_1d_test --days 2000
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
FLOOR = 62.0
COOLDOWN = rv.HOLD_DAYS
SIGNS = ("RSI oversold", "Extreme fear", "Riding lower Bollinger", "Capitulation volume",
         "Stretched below EMA50")
CASE = "SHORT 62+ into a bottoming radar (the TAO case)"
PLAIN = "SHORT 62+ WITHOUT a bottoming radar (context)"
MIRROR = "LONG 62+ into a topping radar (mirror, context)"


def fetch_fear_greed(session=None) -> Dict[int, int]:
    """{UTC day start ms: Fear & Greed value} for the whole history (2018+)."""
    import requests
    s = session or requests
    r = s.get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"}, timeout=20)
    out = {}
    for row in (r.json() or {}).get("data") or []:
        try:
            out[int(row["timestamp"]) * 1000 // DAY_MS * DAY_MS] = int(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _cls(v: int) -> str:
    return ("Extreme Fear" if v <= 25 else "Fear" if v <= 45 else "Neutral" if v <= 55
            else "Greed" if v <= 75 else "Extreme Greed")


def signal_at(window: List[Dict], sym: str, fg: Optional[int]) -> Dict:
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, "1D", sym)
    if fg is not None:
        a["fear_greed"] = {"value": fg, "label": _cls(fg), "classification": _cls(fg)}
    return signals.generate_signal(a)


def classify(sig: Dict) -> List[str]:
    """The groups a day belongs to (before cooldowns)."""
    d, s = sig.get("direction"), float(sig.get("strength") or 0)
    rr = sig.get("reversal_radar") or {}
    hot = rr.get("level") in rv.LEVELS
    labels = {x.get("label") for x in rr.get("signals") or []}
    out = []
    if d == "SHORT" and s >= FLOOR:
        if rr.get("mode") == "bottom" and hot:
            out.append(CASE)
        else:
            out.append(PLAIN)
        if rr.get("mode") == "bottom":
            out += [f"SHORT 62+ with '{x}' (context)" for x in SIGNS if x in labels]
    if d == "LONG" and s >= FLOOR and rr.get("mode") == "top" and hot:
        out.append(MIRROR)
    return out


def scan(daily: Sequence[Dict], sym: str = "", fg: Optional[Dict[int, int]] = None, *,
         signal_fn: Callable = signal_at) -> List[Dict]:
    out, last = [], {}
    fg = fg or {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS))
        entry, after = float(window[-1]["close"]), daily[i + 1:]
        for grp in classify(sig or {}):
            if grp in last and i - last[grp] < COOLDOWN:
                continue
            last[grp] = i
            sig_dir = "LONG" if grp == MIRROR else "SHORT"      # the signal's own side
            opp = "SHORT" if sig_dir == "LONG" else "LONG"
            out.append({"family": grp, "symbol": sym, "ts": ts, "direction": sig_dir,
                        "with": rv.walk(sig_dir, entry, atr, after),
                        "opp": rv.walk(opp, entry, atr, after)})
    return out


GROUPS = [CASE, PLAIN, MIRROR] + [f"SHORT 62+ with '{x}' (context)" for x in SIGNS]


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    out = {}
    for g in GROUPS:
        ge = [e for e in evs if e["family"] == g]
        per = {}
        for name, sel in (("older", [e for e in ge if e["ts"] < cutoff_ms]),
                          ("recent", [e for e in ge if e["ts"] >= cutoff_ms])):
            w, o = rv.side_stats(sel, "with"), rv.side_stats(sel, "opp")
            per[name] = {"with": w, "opp": o, "verdict": rv.verdict(w, o)}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        ans = per["older"]["verdict"] if same and per["older"]["verdict"] in (
            "REVERSAL WINS", "OPPOSITE WINS") else "no consistent edge"
        sig_side = "LONG" if g == MIRROR else "SHORT"
        rev_side = "SHORT" if g == MIRROR else "LONG"
        out[g] = {**per, "answer": ans.replace("REVERSAL WINS", f"{sig_side} (the signal) WINS")
                  .replace("OPPOSITE WINS", f"{rev_side} (the reversal) WINS"),
                  "sig_side": sig_side, "rev_side": rev_side}
    return out


def render_telegram(res: Dict) -> str:
    lines = ["🎯 The TAO case on 1D: SHORT 62+ while the radar says BOTTOMING — short or long? "
             "(pre-registered)",
             f"{len(res['coins'])} coins · {res['days']} days of daily candles · split at "
             f"{res['cutoff'][:10]} (older / last 365 days) · real Fear & Greed history "
             f"({'loaded' if res['fg_days'] else 'NOT available'}: {res['fg_days']} days)",
             "Entry at the day's close · stop and target 1.5 × 1D ATR (1:1) · max 20 days · 0.13% "
             "costs · one per coin per 20 days", ""]
    for g in GROUPS:
        f = res["summary"][g]
        lines.append(f"━━ {g}  →  {f['answer']}")
        for per in ("older", "recent"):
            p = f[per]
            lines.append(f"  {per}: {f['sig_side']} {rv._s(p['with'])}")
            lines.append(f"  {' ' * len(per)}  {f['rev_side']} {rv._s(p['opp'])}")
        lines.append("")
    lines.append(f"Rule fixed before the run (decides on the first row only): {rv.MIN_N}+ resolved; "
                 f"the short wins ≥ {rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g} and avg R > 0 = SHORT WINS; "
                 f"≤ {rv.WIN_LO:g}% with z ≤ −{rv.Z_MIN:g} and the long's avg R > 0 = LONG WINS; "
                 "both periods must agree. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, end_ms: int, log=print,
        **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"events": len(evs), "summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(daily_by_coin), "fg_days": len(fg)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="tao_case_1d_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = fetch_fear_greed()
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed history unavailable: {type(exc).__name__}")
        fg = {}
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    res = run(daily, fg, end_ms=end_ms, log=log)
    res["days"] = args.days
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
