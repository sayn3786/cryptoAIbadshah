"""
The 'all three flipped' signal traded only WITH BTC's 200-day trend? A
pre-registered follow-up (written Nov 23 2026, before any run).

reversal_timing_test found the flip (RSI 50, MACD cross and EMA(20) cross
all within 3 days, same way) won with the market's direction in each era -
longs before 2022, shorts since. That idea came FROM that run, so it must
pass on its own fixed rules here, in both eras.

The regime is BTC's close vs its 200-day average on the flip day's close
(regime_switch_test.btc_regime; known at entry, no look-ahead):
  WITH      long when BTC is above its 200-day, short when below
  AGAINST   the other flips
Trades exactly as reversal_timing_test part C: entry at the flip day's close,
1.5 x ATR stop and target (1:1), 20 days max, 0.13% costs, 21-day cooldown
per coin and side.

PASS only if, in BOTH eras (before 2022, 2022-now), WITH has 30+ resolved
trades, win >= 53% with z >= 2, avg R > 0, AND beats AGAINST by 3+ win points
and 0.05+ avg R. Coins move together, so z overstates certainty.
If it passes, the next step would be a live shadow (log only), not scoring.

    python -m flip_regime_test --days 3400
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Dict, List, Sequence

import regime_switch_test as rst
import reversal_timing_test as rtt

DAY_MS = rtt.DAY_MS
WIN_HI, Z_MIN, MIN_N, GAP_WIN, GAP_R = 53.0, 2.0, 30, 3.0, 0.05


def tag(evs: Sequence[Dict], regime: Dict[int, str]) -> List[Dict]:
    out = []
    for e in evs:
        r = regime.get(e["ts"] // DAY_MS * DAY_MS)
        if r is None:
            continue
        with_ = (e["dir"] == "LONG") == (r == "bull")
        out.append({**e, "with": with_, "regime": r})
    return out


def summarize(evs: Sequence[Dict]) -> Dict:
    res = {}
    for era, keep in (("older", lambda ts: ts < rtt.ERA_SPLIT), ("recent", lambda ts: ts >= rtt.ERA_SPLIT)):
        e = [x for x in evs if keep(x["ts"])]
        st = lambda xs: rtt._stats(xs)                                   # noqa: E731
        res[era] = {"with": st([x for x in e if x["with"]]), "against": st([x for x in e if not x["with"]]),
                    "with_long": st([x for x in e if x["with"] and x["dir"] == "LONG"]),
                    "with_short": st([x for x in e if x["with"] and x["dir"] == "SHORT"])}

    def ok(w, a):
        return (w["resolved"] or 0) >= MIN_N and (w["win_pct"] or 0) >= WIN_HI and (w["z"] or 0) >= Z_MIN \
            and (w["avg_r"] or 0) > 0 and (a["resolved"] or 0) > 0 \
            and (w["win_pct"] or 0) - (a["win_pct"] or 0) >= GAP_WIN and (w["avg_r"] or 0) - (a["avg_r"] or 0) >= GAP_R
    res["passes"] = all(ok(res[e]["with"], res[e]["against"]) for e in ("older", "recent"))
    return res


def run(daily_by_coin: Dict[str, List[Dict]], *, log=print) -> Dict:
    regime = rst.btc_regime(daily_by_coin.get("BTC") or [])
    evs = []
    for sym, c in daily_by_coin.items():
        if len(c) < 120:
            continue
        evs += rtt.flips(c, rtt.indicators(c))
    tagged = tag(evs, regime)
    log(f"  flips {len(evs)}, with a regime {len(tagged)}")
    first = min((int(c[0]["timestamp"]) for c in daily_by_coin.values() if c), default=0)
    return {"coins": sorted(daily_by_coin), "since": first, "res": summarize(tagged)}


def render_telegram(out: Dict) -> str:
    r = out["res"]
    since = datetime.fromtimestamp(out["since"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    L = ["🧭 'All three flipped' only WITH BTC's 200-day trend? (pre-registered follow-up)",
         f"1D, {len(out['coins'])} coins since {since} · flip = RSI 50 + MACD + EMA20 within 3 days · "
         "1:1 at 1.5×ATR, 20 days, costs", ""]
    for era in ("older", "recent"):
        e = r[era]
        L.append("before 2022:" if era == "older" else "2022 → now:")
        L.append(f"  WITH the trend {rtt._st(e['with'])}")
        L.append(f"    longs (BTC above 200d) {rtt._st(e['with_long'])}")
        L.append(f"    shorts (BTC below 200d) {rtt._st(e['with_short'])}")
        L.append(f"  AGAINST the trend {rtt._st(e['against'])}")
    L.append("")
    L.append(f"Result: {'PASSES ✅ - next step a live shadow (log only)' if r['passes'] else 'does not pass ❌'}")
    L.append(f"Rule fixed before the run: WITH needs {MIN_N}+ trades, win ≥ {WIN_HI:g}%, z ≥ 2, avg R > 0 and "
             f"+{GAP_WIN:g} win pts / +{GAP_R:g}R over AGAINST, in BOTH eras. Coins move together, so z overstates "
             "certainty.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="flip_regime_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    if "BTC" not in daily:
        print("error: no BTC history", file=sys.stderr)
        return 2
    out = run(daily, log=log)
    text = render_telegram(out)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(out['coins'])}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
