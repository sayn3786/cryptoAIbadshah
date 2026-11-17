"""
Should a HIGH-CHANCE pattern push the 1D signal its way? A pre-registered
test (written Nov 17 2026, before any run).

The idea: when a high-chance pattern confirms, add strength to signals in
the pattern's direction and take it from signals against it. On 1D the
high-chance patterns are the reversal patterns (double / triple top and
bottom, head & shoulders, inverse) - the family that beat the opposite trade
in reversal_1d_test and reversal_factors_1d. (The 4H / 1W high-chance types
are not on this chart.)

Replay on every coin's 1D history with the live code: the dashboard's 1D
analysis + real Fear & Greed -> signals.generate_signal (as regime_switch_test)
and the live pattern detectors (pattern_outcome_study.detect) on the same
closed candles. A pattern counts from the day it confirms for 20 days.
Every 1D signal at strength >= 62 (one per coin per direction per 20 days)
falls in one group:
  AGREES      a high-chance pattern confirmed in the last 20 days points the
              same way as the signal (the latest one, if several)
  OPPOSES     it points the other way
  NO PATTERN  none in the last 20 days
Trades: 1:1 at 1.5 x 1D ATR, max 20 days, stop first, 0.13% costs.

THE PUSH IS SUPPORTED only if, in BOTH eras (A: 2018 to the end of 2021,
B: 2022 to now):
  - 30+ resolved agreeing AND 30+ opposing trades
  - agreeing trades win >= 53% with z >= 2 and avg R > 0
  - agreeing trades' avg R beats the opposing ones' by at least 0.10
Then the points are sized from the measured gap, not guessed. Context:
the no-pattern group, and every signal. Coins move together, so z
overstates certainty.

    python -m pattern_agree_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import pattern_outcome_study as po
import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
PATTERN_DAYS = 20
COOLDOWN = rv.HOLD_DAYS
MIN_N = 30
R_GAP = 0.10
REVERSAL_TYPES = ("double_top", "double_bottom", "triple_top", "triple_bottom",
                  "head_shoulders", "inverse_head_shoulders")
AGREES, OPPOSES, NONE = "agrees with a high-chance pattern", "opposes it", "no pattern (context)"


def confirmed_today(patterns: Sequence[Dict], ts: int) -> Optional[str]:
    """'LONG' / 'SHORT' if a high-chance reversal pattern confirmed on `ts`."""
    for p in patterns or []:
        if (p.get("_kind") == "reversal" and p.get("type") in REVERSAL_TYPES and p.get("confirmed")
                and int(p.get("break_ts") or -1) == int(ts)):
            return "LONG" if p.get("direction") == "bullish" else "SHORT"
    return None


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at, detect_fn: Callable = po.detect) -> List[Dict]:
    out, last, recent = [], {}, None          # recent = (day index, direction) of the latest pattern
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        pd = confirmed_today(detect_fn(window, "1D"), ts)
        if pd:
            recent = (i, pd)
        atr = rv._atr(window)
        if not atr:
            continue
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d = sig.get("direction")
        if d not in ("LONG", "SHORT") or float(sig.get("strength") or 0) < FLOOR:
            continue
        if d in last and i - last[d] < COOLDOWN:
            continue
        last[d] = i
        if recent and i - recent[0] < PATTERN_DAYS:
            grp = AGREES if recent[1] == d else OPPOSES
        else:
            grp = NONE
        out.append({"symbol": sym, "ts": ts, "direction": d, "group": grp,
                    "trade": rv.walk(d, float(window[-1]["close"]), atr, daily[i + 1:])})
    return out


def _st(evs):
    return rv.side_stats([{"x": e["trade"]} for e in evs], "x")


def era(evs: Sequence[Dict]) -> Dict:
    a, o, n = (_st([e for e in evs if e["group"] == g]) for g in (AGREES, OPPOSES, NONE))
    checks = {
        f"{MIN_N}+ agreeing and opposing trades": (a["resolved"] or 0) >= MIN_N and (o["resolved"] or 0) >= MIN_N,
        "agreeing wins ≥ 53%, z ≥ 2, avg R > 0": ((a["win_pct"] or 0) >= rv.WIN_HI and (a["z"] or 0) >= rv.Z_MIN
                                                 and (a["avg_r"] or 0) > 0),
        f"avg R ≥ {R_GAP:g} above opposing": ((a["avg_r"] or 0) - (o["avg_r"] or 0)) >= R_GAP,
    }
    return {"agrees": a, "opposes": o, "none": n, "all": _st(evs), "checks": checks,
            "passes": all(checks.values())}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    import scalp_baseline as sb
    return {"eras": {"A": a, "B": b}, "supported": a["passes"] and b["passes"],
            "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(min(e["ts"] for e in evs)) if evs else "-"}


def render_telegram(res: Dict) -> str:
    lines = ["🧩➕ Should a high-chance pattern push the 1D signal its way? (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · 1D signal at strength ≥ 62 · "
             "high-chance = 1D reversal patterns, counted for 20 days after they confirm",
             "1:1 at 1.5 × 1D ATR · max 20 days · 0.13% costs · one per coin per direction per 20 days", ""]
    names = {"A": "ERA A: 2018 → 2021", "B": "ERA B: 2022 → now"}
    for k in ("A", "B"):
        e = res["eras"][k]
        lines.append(f"━━ {names[k]}  →  {'HOLDS ✅' if e['passes'] else 'FAILS ❌'}")
        lines.append(f"  signal AGREES with the pattern: {rv._s(e['agrees'])}")
        lines.append(f"  signal OPPOSES the pattern:     {rv._s(e['opposes'])}")
        lines.append(f"  no pattern (context):           {rv._s(e['none'])}")
        lines.append(f"  every signal (context):         {rv._s(e['all'])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        lines.append("")
    lines.append(f"Verdict: {'PUSH SUPPORTED ✅' if res['supported'] else 'NOT supported ❌'} (both eras must hold)")
    lines.append(f"Rule fixed before the run: {MIN_N}+ agreeing and opposing trades; agreeing wins ≥ "
                 f"{rv.WIN_HI:g}% (z ≥ {rv.Z_MIN:g}) with avg R > 0 and {R_GAP:g}R+ above opposing. "
                 "Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="pattern_agree_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed history unavailable: {type(exc).__name__}")
        fg = {}
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    res = run(daily, fg, log=log)
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
