"""
The other reversal factors on the DAILY chart: trade the signal or the
opposite? A pre-registered test (written Nov 15 2026, before any run).

reversal_1d_test found confirmed reversal chart patterns on 1D winning 55-60%
in both periods, and the Reversal Radar not. These are the reversal factors
the app shows or scores that have never been tested on their own:

  RSI divergence    a NEW regular divergence (bullish -> LONG, bearish ->
                    SHORT; hidden divergences are continuation, left out),
                    the day it is first detectable
  CHoCH             change of character - the first break of structure
                    against the trend - on the day it breaks
  liquidity grab    a wick through a swing high / low that closes back, on
                    the candle it happens (bullish grab -> LONG)
  SuperTrend flip   the day the SuperTrend line flips side (the chart's
                    BUY / SELL marker)
  engulfing         a bullish / bearish engulfing candle on the newest day
  reversal patterns (re-check)  the family that PASSED reversal_1d_test
                    (confirmed double / triple top and bottom, H&S), re-run
                    here with the one-trade-per-coin cooldown below, which
                    that test lacked

All read from the dashboard's own 1D analysis (candle_analysis), closed
candles only, the live 240-day lookback.

Trades and the rule exactly as reversal_1d_test, fixed before running: entry
at the day's close, stop and target 1.5 x 1D ATR (1:1), max 20 days, 0.13%
costs; at most ONE trade per factor per coin per 20 days (overlapping trades
on one coin share a price path and inflate z); for each factor and period
(older / last 365 days):
  SIGNAL WINS     30+ resolved, the signal side wins >= 53% with z >= 2 and
                  avg R > 0
  OPPOSITE WINS   the signal side wins <= 47% with z <= -2 and the opposite
                  side's avg R > 0
  otherwise       no edge
A factor's answer counts only if both periods agree. Coins move together on
the same days, so z overstates certainty.

    python -m reversal_factors_1d --days 2000
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv

FACTORS = ("RSI divergence", "CHoCH", "liquidity grab", "SuperTrend flip", "engulfing",
           "reversal patterns (re-check)")
DAY_MS = rv.DAY_MS


def _side(v: Optional[str]) -> Optional[str]:
    v = str(v or "").lower()
    return "LONG" if v == "bullish" else "SHORT" if v == "bearish" else None


def analysis_at(window: List[Dict], sym: str) -> Dict:
    import candle_analysis as ca
    return ca.build_candle_analysis(window, "1D", sym)


def events_from(a: Dict, state: Dict) -> List[tuple]:
    """(factor, direction) for every factor that fires on the newest day.
    `state` remembers the last divergence so a standing one fires once."""
    out = []
    rd = a.get("rsi_divergence") or {}
    if rd.get("type") in ("bullish", "bearish"):
        key = (rd["type"], ((rd.get("points") or {}).get("curr") or {}).get("timestamp"))
        if key != state.get("div"):
            state["div"] = key
            out.append(("RSI divergence", _side(rd["type"])))
    ch = a.get("choch") or {}
    if ch.get("signal") in ("bullish", "bearish") and ch.get("candles_ago") == 0:
        out.append(("CHoCH", _side(ch["signal"])))
    lg = a.get("liq_grab") or {}
    if lg.get("signal") in ("bullish", "bearish") and lg.get("candles_ago") == 0:
        out.append(("liquidity grab", _side(lg["signal"])))
    st = a.get("supertrend") or {}
    if st.get("flipped_bars_ago") == 0 and _side(st.get("direction")):
        out.append(("SuperTrend flip", _side(st["direction"])))
    for e in a.get("engulfing") or []:
        if e.get("candles_ago") == 1 and _side(e.get("direction")):
            out.append(("engulfing", _side(e["direction"])))
            break
    return [(f, d) for f, d in out if d]


COOLDOWN_DAYS = rv.HOLD_DAYS   # one trade per factor per coin at a time


def pattern_events(window: List[Dict], pattern_fn: Callable) -> List[tuple]:
    """Confirmed reversal chart patterns breaking on the newest day - the
    family that passed reversal_1d_test, re-checked here with the cooldown."""
    ts = int(window[-1]["timestamp"])
    for p in pattern_fn(window, "1D"):
        if p.get("_kind") == "reversal" and p.get("confirmed") and int(p.get("break_ts") or -1) == ts:
            return [("reversal patterns (re-check)", "LONG" if p.get("direction") == "bullish" else "SHORT")]
    return []


def scan(daily: Sequence[Dict], sym: str = "", *, analysis_fn: Callable = analysis_at,
         pattern_fn: Optional[Callable] = None) -> List[Dict]:
    """Every factor event, at most one per factor per coin per COOLDOWN_DAYS:
    events days apart on one coin share the same price path, so counting
    each would make the z-score look surer than it is (a random-price dry run
    "found" CHoCH at z -3.2 before this)."""
    if pattern_fn is None:
        import pattern_outcome_study as po
        pattern_fn = po.detect
    out, state, last = [], {}, {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        entry, ts, after = float(window[-1]["close"]), int(window[-1]["timestamp"]), daily[i + 1:]
        for fam, d in events_from(analysis_fn(window, sym) or {}, state) + pattern_events(window, pattern_fn):
            if fam in last and i - last[fam] < COOLDOWN_DAYS:
                continue
            last[fam] = i
            opp = "SHORT" if d == "LONG" else "LONG"
            out.append({"family": fam, "symbol": sym, "ts": ts, "direction": d,
                        "with": rv.walk(d, entry, atr, after), "opp": rv.walk(opp, entry, atr, after)})
    return out


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    out = {}
    for fam in FACTORS:
        fe = [e for e in evs if e["family"] == fam]
        per = {}
        for name, sel in (("older", [e for e in fe if e["ts"] < cutoff_ms]),
                          ("recent", [e for e in fe if e["ts"] >= cutoff_ms])):
            w, o = rv.side_stats(sel, "with"), rv.side_stats(sel, "opp")
            per[name] = {"with": w, "opp": o, "verdict": rv.verdict(w, o)}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        ans = per["older"]["verdict"] if same and per["older"]["verdict"] in (
            "REVERSAL WINS", "OPPOSITE WINS") else "no consistent edge"
        out[fam] = {**per, "answer": ans.replace("REVERSAL WINS", "SIGNAL WINS"),
                    "by_side": {d: rv.side_stats([e for e in fe if e["direction"] == d], "with")
                                for d in ("LONG", "SHORT")}}
    return out


def render_telegram(res: Dict) -> str:
    lines = ["🔄 1D reversal factors: trade the signal or the opposite? (pre-registered)",
             f"{len(res['coins'])} coins · {res['days']} days of daily candles · split at "
             f"{res['cutoff'][:10]} (older / last 365 days)",
             "Entry at the day's close · stop and target 1.5 × 1D ATR (1:1, a coin flip wins 50%) · "
             "max 20 days · 0.13% costs", ""]
    for fam in FACTORS:
        f = res["summary"][fam]
        lines.append(f"━━ {fam.upper()}  →  {f['answer']}")
        for per in ("older", "recent"):
            p = f[per]
            v = p["verdict"].replace("REVERSAL WINS", "SIGNAL WINS")
            lines.append(f"  {per}: {v}")
            lines.append(f"    signal side:   {rv._s(p['with'])}")
            lines.append(f"    opposite side: {rv._s(p['opp'])}")
        bs = "; ".join(f"{d} {v['win_pct']}% of {v['resolved']}" for d, v in f["by_side"].items()
                       if v["resolved"])
        if bs:
            lines.append(f"  signal side by direction (all years): {bs}")
        lines.append("")
    lines.append(f"Rule fixed before the run: {rv.MIN_N}+ resolved trades; the signal side wins ≥ "
                 f"{rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g} and avg R > 0 = SIGNAL WINS; ≤ {rv.WIN_LO:g}% "
                 f"with z ≤ −{rv.Z_MIN:g} and the opposite's avg R > 0 = OPPOSITE WINS; counts only if "
                 "both periods agree. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def run(daily_by_coin: Dict[str, List[Dict]], *, end_ms: int, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"events": len(evs), "summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(daily_by_coin)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="reversal_factors_1d", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    res = run(daily, end_ms=end_ms, log=log)
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
