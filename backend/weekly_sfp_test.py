"""
The weekly SWING FAILURE (liquidity sweep of a MAJOR low / high): does it
mark a reversal? A pre-registered test (written Nov 23 2026, before any run).

Why: the engine's liquidity grab uses small recent swings (3 candles either
side) and tested as no edge on 1D. The popular version is bigger: a WEEKLY
candle wicks below the lowest low of the past 26 weeks (about six months)
and CLOSES back above it - the stops under a major low are taken and price
is reclaimed. The mirror for tops.

  BULLISH SFP   week low < the lowest low of the prior 26 weeks, and the
                week closes above that level -> LONG at the weekly close
  BEARISH SFP   week high > the highest high of the prior 26 weeks, and the
                week closes below that level -> SHORT at the weekly close
Weeks are Monday 00:00 UTC (the exchange's 1W), built from daily candles;
every scan coin, 2017 -> now. One event per coin per side per 4 weeks.

The trade: entry at the weekly close; stop and target 1.5 x ATR(14) of the
WEEK each (1:1, so a coin flip wins half), walked on DAILY candles, stop
first inside a day, max 20 weeks, 0.13% costs.
Baseline (drift control): the same trade, same direction, from EVERY weekly
close of the same coins and era - so a bull era cannot make longs look smart.

AN SFP SIDE HAS AN EDGE only if, in BOTH eras (before 2022, 2022-now): 20+
resolved trades, win >= 55% with z >= 2 (vs 50%), avg R > 0, AND its win
rate beats that side's baseline by 5+ points. Few weekly events exist and
coins move together, so z overstates certainty - a pass would go to the
dashboard as context first, not into the score.

ROBUSTNESS (added Nov 23 2026 after the first run, before its result): coins
sweep together in the same crash weeks, so the bullish side is re-counted
with each WEEK once (its trades' average R; a week wins if that is > 0).
It HOLDS if, in both eras, 10+ such weeks win >= 55% with avg R > 0.

FORWARD RECORD (--forward): the same bullish rule on weeks closing from
2026-11-23 on - out of sample, since the rule was fixed that day. Every
Monday (close-record.yml). After 20 resolved trades: KEEPS its edge if it
wins >= 55% with avg R > 0; otherwise the dashboard note says so.

    python -m weekly_sfp_test --days 3400
    python -m weekly_sfp_test --forward --days 400
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Dict, List, Sequence

import read_study as rs
import regime_switch_test as rgs
import reversal_1d_test as rv
import weekly_sfp as ws

DAY_MS = rv.DAY_MS
LOOK_W = ws.LOOK_W
COOLDOWN_W = 4
HOLD_D = 20 * 7
ATR_MULT, COST = rv.ATR_MULT, rv.COST
MIN_N, WIN_HI, Z_MIN, GAP = 20, 55.0, 2.0, 5.0


def walk(direction: str, entry: float, atr: float, daily_after: Sequence[Dict]) -> Dict:
    sgn = 1 if direction == "LONG" else -1
    risk = ATR_MULT * atr
    stop, tgt = entry - sgn * risk, entry + sgn * risk
    cost_r = COST * entry / risk
    for k, c in enumerate(daily_after[:HOLD_D]):
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            return {"result": "stop", "r": -1 - cost_r, "days": k + 1}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r, "days": k + 1}
    if len(daily_after) < HOLD_D:
        return {"result": "pending", "r": None}
    last = float(daily_after[HOLD_D - 1]["close"])
    return {"result": "open", "r": sgn * (last - entry) / risk - cost_r, "days": HOLD_D}


def scan(daily: Sequence[Dict], sym: str = "") -> Dict[str, List[Dict]]:
    weeks = rs.weekly_from_daily(daily)
    day_idx = {int(c["timestamp"]): i for i, c in enumerate(daily)}
    out = {"sfp": [], "base": []}
    last = {}
    for i in range(LOOK_W + 14, len(weeks)):
        w = weeks[i]
        close_ms = int(w["timestamp"]) + 7 * DAY_MS          # the Monday the week closes
        j = day_idx.get(close_ms)                            # first daily candle after the close
        if j is None and i != len(weeks) - 1:
            continue                    # a gap; the latest week is kept (its trade is still pending)
        atr = rv._atr(weeks[i - 14:i + 1])
        if not atr:
            continue
        after = daily[j:] if j is not None else []
        entry = float(w["close"])
        for d in ("LONG", "SHORT"):
            out["base"].append({"symbol": sym, "ts": close_ms, "direction": d, "trade": walk(d, entry, atr, after)})
        hits = []
        lo_lvl, hi_lvl = ws.bullish_level(weeks, i), ws.bearish_level(weeks, i)   # the live rule
        if lo_lvl is not None:
            hits.append(("LONG", lo_lvl))
        if hi_lvl is not None:
            hits.append(("SHORT", hi_lvl))
        for d, lvl in hits:
            if d in last and i - last[d] < COOLDOWN_W:
                continue
            last[d] = i
            out["sfp"].append({"symbol": sym, "ts": close_ms, "direction": d, "level": lvl,
                               "trade": walk(d, entry, atr, after)})
    return out


def era_eval(sfp: Sequence[Dict], base: Sequence[Dict]) -> Dict:
    res = {}
    for d in ("LONG", "SHORT"):
        s = rv.side_stats([e for e in sfp if e["direction"] == d], "trade")
        b = rv.side_stats([e for e in base if e["direction"] == d], "trade")
        ok = (s["resolved"] or 0) >= MIN_N and (s["win_pct"] or 0) >= WIN_HI and (s["z"] or 0) >= Z_MIN \
            and (s["avg_r"] or 0) > 0 and (s["win_pct"] or 0) - (b["win_pct"] or 0) >= GAP
        res[d] = {"sfp": s, "base": b, "ok": ok}
    return res


def by_week(evs: Sequence[Dict]) -> Dict:
    """Each event week once: the average R of its resolved trades."""
    weeks: Dict[int, List[float]] = {}
    for e in evs:
        t = e["trade"]
        if t["result"] in ("target", "stop", "open"):
            weeks.setdefault(e["ts"], []).append(t["r"])
    avg = [sum(v) / len(v) for v in weeks.values()]
    n = len(avg)
    return {"weeks": n, "win_pct": round(100 * sum(a > 0 for a in avg) / n, 1) if n else None,
            "avg_r": round(sum(avg) / n, 3) if n else None,
            "biggest": max((len(v) for v in weeks.values()), default=0)}


def week_ok(w: Dict) -> bool:
    return w["weeks"] >= 10 and (w["win_pct"] or 0) >= WIN_HI and (w["avg_r"] or 0) > 0


FORWARD_START_MS = 1_795_392_000_000          # 2026-11-23 00:00 UTC (a Monday close)
FWD_N = 20


def forward(daily_by_coin: Dict[str, List[Dict]], *, log=print) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += [e for e in scan(daily, sym)["sfp"] if e["direction"] == "LONG" and e["ts"] >= FORWARD_START_MS]
    evs.sort(key=lambda e: e["ts"])
    st = rv.side_stats(evs, "trade")
    verdict = ("waiting" if (st["resolved"] or 0) < FWD_N else
               "keeps its edge" if (st["win_pct"] or 0) >= WIN_HI and (st["avg_r"] or 0) > 0 else "lost its edge")
    log(f"  forward events: {len(evs)}")
    return {"events": [{"symbol": e["symbol"], "date": _d(e["ts"]), "result": e["trade"]["result"],
                        "r": e["trade"]["r"]} for e in evs], "stats": st, "verdict": verdict}


def render_forward(res: Dict) -> str:
    st = res["stats"]
    L = ["🪝 Weekly bullish SFP - forward record (rule fixed Nov 23 2026; weeks closing from then on)"]
    if not res["events"]:
        L.append("no bullish weekly SFP yet")
    for e in res["events"][-25:]:
        L.append(f"  {e['date']} {e['symbol']}: {e['result']}" + (f" {e['r']:+.2f}R" if e["r"] is not None else ""))
    if st["resolved"]:
        L.append(f"resolved {st['resolved']} · win {st['win_pct']}% · avg {st['avg_r']:+.3f}R")
    L.append(f"verdict: {res['verdict']} (judged at {FWD_N} resolved: win ≥ {WIN_HI:g}% and avg R > 0)")
    return "\n".join(L)


def run(daily_by_coin: Dict[str, List[Dict]], *, log=print) -> Dict:
    sfp, base = [], []
    for sym, daily in daily_by_coin.items():
        got = scan(daily, sym)
        sfp += got["sfp"]
        base += got["base"]
        log(f"  {sym}: scanned")                         # no results in the public log
    eras = {}
    for key, keep in (("A", lambda ts: ts < rgs.ERA_SPLIT), ("B", lambda ts: ts >= rgs.ERA_SPLIT)):
        eras[key] = era_eval([e for e in sfp if keep(e["ts"])], [e for e in base if keep(e["ts"])])
    edge = {d: eras["A"][d]["ok"] and eras["B"][d]["ok"] for d in ("LONG", "SHORT")}
    for key, keep in (("A", lambda ts: ts < rgs.ERA_SPLIT), ("B", lambda ts: ts >= rgs.ERA_SPLIT)):
        eras[key]["weeks_long"] = by_week([e for e in sfp if keep(e["ts"]) and e["direction"] == "LONG"])
    weeks_hold = week_ok(eras["A"]["weeks_long"]) and week_ok(eras["B"]["weeks_long"])
    first = min((e["ts"] for e in base), default=0)
    recent = sorted((e for e in sfp if e["symbol"] == "BTC"), key=lambda e: e["ts"])[-3:]
    return {"eras": eras, "edge": edge, "weeks_hold": weeks_hold, "coins": sorted(daily_by_coin), "since": first,
            "btc_recent": [{"date": _d(e["ts"]), "direction": e["direction"], "result": e["trade"]["result"]}
                           for e in recent]}


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _s(s: Dict) -> str:
    if not s["resolved"]:
        return "no trades"
    return f"{s['resolved']} · win {s['win_pct']}% (z {s['z']:+.2f}) · avg {s['avg_r']:+.3f}R"


def render_telegram(res: Dict) -> str:
    L = ["🪝 Weekly swing failure (sweep of a 26-week low / high, closed back) - a reversal? (pre-registered)",
         f"1W from daily, {len(res['coins'])} coins since {_d(res['since']) if res['since'] else '-'} · "
         "entry at the weekly close · 1:1 at 1.5×weekly ATR, walked daily, max 20 weeks, costs", ""]
    for key, nm in (("A", "before 2022"), ("B", "2022 → now")):
        L.append(f"━━ {nm}")
        for d, lab in (("LONG", "bullish SFP (low swept) → LONG"), ("SHORT", "bearish SFP (high swept) → SHORT")):
            x = res["eras"][key][d]
            L.append(f"  {lab}: {_s(x['sfp'])} {'✅' if x['ok'] else '❌'}")
            L.append(f"    baseline, every week {d.lower()}: {_s(x['base'])}")
        wl = res["eras"][key].get("weeks_long")
        if wl and wl["weeks"]:
            L.append(f"  bullish, each week once: {wl['weeks']} weeks · win {wl['win_pct']}% · avg {wl['avg_r']:+.3f}R "
                     f"(busiest week: {wl['biggest']} coins) {'✅' if week_ok(wl) else '❌'}")
        L.append("")
    for d, lab in (("LONG", "Bullish SFP"), ("SHORT", "Bearish SFP")):
        L.append(f"{lab}: {'EDGE ✅ - add as dashboard context first' if res['edge'][d] else 'no edge ❌'}")
    if "weeks_hold" in res:
        L.append(f"Counting each week once: {'HOLDS ✅' if res['weeks_hold'] else 'does not hold ❌'} "
                 f"(10+ weeks, win ≥ {WIN_HI:g}%, avg R > 0, both eras)")
    if res["btc_recent"]:
        L.append("BTC's last weekly SFPs: " + "; ".join(f"{x['date']} {x['direction']} ({x['result']})"
                                                       for x in res["btc_recent"]))
    L.append(f"Rule fixed before the run: {MIN_N}+ trades, win ≥ {WIN_HI:g}%, z ≥ 2, avg R > 0 and +{GAP:g} win pts "
             "over the every-week baseline, in BOTH eras. Coins move together, so z overstates certainty.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="weekly_sfp_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--forward", action="store_true", help="the out-of-sample record since Nov 23 2026")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    if "BTC" not in daily:
        print("error: no BTC history", file=sys.stderr)
        return 2
    if args.forward:
        res = forward(daily, log=log)
        res["coins"] = sorted(daily)
        text = render_forward(res)
    else:
        res = run(daily, log=log)
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
