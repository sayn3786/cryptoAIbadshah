"""
Stoch RSI crosses on the DAILY chart: trade the cross or the opposite?
A pre-registered test (written Nov 16 2026, before any run).

The dashboard's Stoch RSI card (indicators.calculate_stoch_rsi, 14/14/3/3)
names two reversal crosses:
  BULL CROSS (oversold)   K crosses above D while K <= 20 -> LONG
  BEAR CROSS (overbought) K crosses below D while K >= 80 -> SHORT
Never tested on their own. Replayed on every coin's 1D history with the live
code (candle_analysis + real Fear & Greed -> generate_signal for context), on
the day the card would first show the cross; at most one per coin per cross
per 20 days.

Trades and rule exactly as the other 1D factor tests, fixed before running:
1:1 at 1.5 x 1D ATR, max 20 days, stop first, 0.13% costs; for each cross and
period (older / last 365 days):
  CROSS WINS      30+ resolved, the cross side wins >= 53% with z >= 2 and
                  avg R > 0
  OPPOSITE WINS   the cross side wins <= 47% with z <= -2 and the opposite's
                  avg R > 0
  otherwise       no edge; a cross's answer counts only if both periods agree
Context rows (decide nothing): a bull cross while the 1D signal says SHORT
at 62+ (the screenshot's situation) and a bear cross under a LONG 62+.
Coins move together, so z overstates certainty.

    python -m stoch_cross_test --days 2000
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
BULL, BEAR = "bull cross in oversold", "bear cross in overbought"
BULL_VS_SHORT = "bull cross while the 1D signal is SHORT 62+ (context)"
BEAR_VS_LONG = "bear cross while the 1D signal is LONG 62+ (context)"
GROUPS = (BULL, BEAR, BULL_VS_SHORT, BEAR_VS_LONG)


def read_at(window: List[Dict], sym: str, fg: Optional[int]) -> Dict:
    """(stoch_rsi, signal) from the live code on closed 1D candles."""
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, "1D", sym)
    if fg is not None:
        a["fear_greed"] = {"value": fg, "label": tc._cls(fg), "classification": tc._cls(fg)}
    return {"stoch": a.get("stoch_rsi") or {}, "sig": signals.generate_signal(a)}


def groups(read: Dict) -> List[tuple]:
    st, sig = read.get("stoch") or {}, read.get("sig") or {}
    s = st.get("signal")
    d, strong = sig.get("direction"), float(sig.get("strength") or 0) >= FLOOR
    out = []
    if s == "bull_cross_oversold":
        out.append((BULL, "LONG"))
        if d == "SHORT" and strong:
            out.append((BULL_VS_SHORT, "LONG"))
    elif s == "bear_cross_overbought":
        out.append((BEAR, "SHORT"))
        if d == "LONG" and strong:
            out.append((BEAR_VS_LONG, "SHORT"))
    return out


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         read_fn: Callable = read_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        ts = int(window[-1]["timestamp"])
        hits = groups(read_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {})
        entry, after = float(window[-1]["close"]), daily[i + 1:]
        for grp, d in hits:
            if grp in last and i - last[grp] < rv.HOLD_DAYS:
                continue
            last[grp] = i
            opp = "SHORT" if d == "LONG" else "LONG"
            out.append({"family": grp, "symbol": sym, "ts": ts, "direction": d,
                        "with": rv.walk(d, entry, atr, after), "opp": rv.walk(opp, entry, atr, after)})
    return out


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    out = {}
    for g in GROUPS:
        ge = [e for e in evs if e["family"] == g]
        per = {}
        for name, sel in (("older", [e for e in ge if e["ts"] < cutoff_ms]),
                          ("recent", [e for e in ge if e["ts"] >= cutoff_ms])):
            w, o = rv.side_stats(sel, "with"), rv.side_stats(sel, "opp")
            per[name] = {"with": w, "opp": o, "verdict": rv.verdict(w, o)
                         .replace("REVERSAL WINS", "CROSS WINS")}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        out[g] = {**per, "answer": per["older"]["verdict"] if same and per["older"]["verdict"] in (
            "CROSS WINS", "OPPOSITE WINS") else "no consistent edge"}
    return out


def render_telegram(res: Dict) -> str:
    lines = ["〰️ Stoch RSI crosses on 1D: trade the cross or the opposite? (pre-registered)",
             f"{len(res['coins'])} coins · {res['days']} days of daily candles · split at "
             f"{res['cutoff'][:10]} (older / last 365 days)",
             "Entry at the day's close · stop and target 1.5 × 1D ATR (1:1, a coin flip wins 50%) · "
             "max 20 days · 0.13% costs · one per coin per cross per 20 days", ""]
    for g in GROUPS:
        f = res["summary"][g]
        side = "LONG" if g in (BULL, BULL_VS_SHORT) else "SHORT"
        other = "SHORT" if side == "LONG" else "LONG"
        lines.append(f"━━ {g}  →  {f['answer']}")
        for per in ("older", "recent"):
            p = f[per]
            lines.append(f"  {per}: {p['verdict']}")
            lines.append(f"    {side} (the cross): {rv._s(p['with'])}")
            lines.append(f"    {other} (opposite): {rv._s(p['opp'])}")
        lines.append("")
    lines.append(f"Rule fixed before the run (the first two rows decide): {rv.MIN_N}+ resolved; the "
                 f"cross side wins ≥ {rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g} and avg R > 0 = CROSS WINS; "
                 f"≤ {rv.WIN_LO:g}% with z ≤ −{rv.Z_MIN:g} and the opposite's avg R > 0 = OPPOSITE "
                 "WINS; both periods must agree. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, end_ms: int, log=print,
        **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(daily_by_coin)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="stoch_cross_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
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
