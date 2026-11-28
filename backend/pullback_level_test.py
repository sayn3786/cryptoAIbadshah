"""
Leading indicators as LOCATION + TRIGGER on top of the lagging 1D direction:
wait for a pullback into a real level and a reaction there? A pre-registered
test (written Nov 28 2026, before any run).

Each indicator has one job:
  direction  the 1D signal LONG / SHORT at strength >= 62 (lagging: trend)
  location   the nearest real level on the trend side, 0.3-3 x ATR away from
             the close, from the same 1D analysis: the support zone, an
             untouched bullish order block or an unfilled bullish fair-value
             gap below price for a LONG (resistance / bearish OB / bearish
             FVG above for a SHORT)
  trigger    within the next 5 daily closes, price trades into the level and a
             candle then CLOSES back out of it the trend's way (green and above
             the level's top for a LONG); entry at that close. A close 0.25 x
             ATR through the level's far side first = the level failed, no trade
The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at's
analysis + Fear & Greed), all scan coins, 2018 -> now, one per coin per
direction per 20 days; every arm takes the SAME standard trade from its entry
(reversal_1d_test.walk: 1:1 at 1.5 x ATR, 20 days max, stop first, 0.13%
costs), so only WHERE and WHEN it enters differs.
  base     enter at the signal's close (the picks as now)
  level    the pullback into the real level + the reaction close
  random   the same pullback rule at a RANDOM level of the same width, 0.3-3
           x ATR away (proves the LEVEL matters, not just the waiting)
R is per SIGNAL: no pullback / no trigger / a failed level = 0R.

THE LEVEL ENTRY IS AN EDGE only if, in BOTH eras (2018-2021, 2022-now): 30+
level trades, AND its R per signal >= base's + 0.03, AND >= random's + 0.03.
A second verdict, BETTER TRADES (a quality filter - fewer, better trades),
added after the random-walk dry run showed the level arm trades only ~1 in 4
signals and BEFORE any real run: 30+ level trades AND its average R per
TRADE >= base's + 0.10 AND >= random's + 0.10, in BOTH eras. Coins move
together.

    python -m pullback_level_test --days 3400
"""
from __future__ import annotations

import random
import sys
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
WAIT = 5
NEAR, FAR = 0.3, 3.0
FAIL = 0.25
ARMS = ("base", "level", "random")
MIN_N, GAP_R, GAP_TRADE = 30, 0.03, 0.10


def read_at(window: List[Dict], sym: str, fg: Optional[int]) -> Tuple[Dict, Dict]:
    """(signal, analysis) as tao_case_1d_test.signal_at, keeping the analysis."""
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, "1D", sym)
    if fg is not None:
        a["fear_greed"] = {"value": fg, "label": tc._cls(fg), "classification": tc._cls(fg)}
    return signals.generate_signal(a) or {}, a


def levels(a: Dict, direction: str) -> List[Tuple[float, float]]:
    """Candidate (bottom, top) zones on the trend side."""
    out = []
    long = direction == "LONG"
    sr = (a.get("sr_zones") or {}).get("support" if long else "resistance")
    if isinstance(sr, dict) and sr.get("top") and sr.get("bottom"):
        out.append((float(sr["bottom"]), float(sr["top"])))
    for ob in ((a.get("order_blocks") or {}).get("bullish" if long else "bearish") or []):
        if ob.get("status") == "fresh" and not ob.get("inside") and ob.get("top") and ob.get("bottom"):
            out.append((float(ob["bottom"]), float(ob["top"])))
    for g in a.get("fvgs") or []:
        if g.get("type") == ("bullish" if long else "bearish") and not g.get("filled") and g.get("top") \
                and g.get("bottom"):
            out.append((float(g["bottom"]), float(g["top"])))
    return out


def nearest(zones: Sequence[Tuple[float, float]], close: float, atr: float, direction: str):
    """The nearest zone fully on the trend side, its near edge 0.3-3 ATR away."""
    best = None
    for lo, hi in zones:
        lo, hi = min(lo, hi), max(lo, hi)
        dist = (close - hi) / atr if direction == "LONG" else (lo - close) / atr
        if NEAR <= dist <= FAR and (best is None or dist < best[0]):
            best = (dist, lo, hi)
    return (best[1], best[2]) if best else None


def pullback(direction: str, zone: Tuple[float, float], atr: float, after: Sequence[Dict]) -> Optional[int]:
    """Index (into `after`) of the reaction close, or None (no touch / failed / too late)."""
    lo, hi = zone
    long = direction == "LONG"
    touched = False
    for k, c in enumerate(after[:WAIT]):
        o, h, l, cl = (float(c[x]) for x in ("open", "high", "low", "close"))
        if long:
            if cl < lo - FAIL * atr:
                return None
            touched = touched or l <= hi
            if touched and cl > o and cl > hi:
                return k
        else:
            if cl > hi + FAIL * atr:
                return None
            touched = touched or h >= lo
            if touched and cl < o and cl < lo:
                return k
    return None


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *, read_fn: Callable = read_at,
         seed: int = 0) -> List[Dict]:
    rnd = random.Random(f"{sym}:{seed}")
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily) - WAIT - rv.HOLD_DAYS):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig, a = read_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS))
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        last[d] = i
        close = float(daily[i]["close"])
        after = daily[i + 1:]
        ev = {"symbol": sym, "ts": ts, "direction": d,
              "base": rv.walk(d, close, atr, after)["r"]}
        zone = nearest(levels(a, d), close, atr, d)
        ev["has_level"] = zone is not None
        for arm, z in (("level", zone), ("random", _random_zone(zone, close, atr, d, rnd))):
            r = 0.0
            if z is not None:
                k = pullback(d, z, atr, after)
                if k is not None:
                    r = rv.walk(d, float(after[k]["close"]), atr, after[k + 1:])["r"]
                    ev[f"{arm}_traded"] = True
            ev[arm] = r if r is not None else 0.0
        out.append(ev)
    return out


def _random_zone(zone, close: float, atr: float, d: str, rnd: random.Random):
    if zone is None:
        return None
    width = zone[1] - zone[0]
    dist = rnd.uniform(NEAR, FAR) * atr
    if d == "LONG":
        hi = close - dist
        return (hi - width, hi)
    lo = close + dist
    return (lo, lo + width)


def era(evs: Sequence[Dict]) -> Dict:
    n = len(evs)
    st = {}
    for arm in ARMS:
        rs = [e[arm] for e in evs if e[arm] is not None]
        traded = sum(1 for e in evs if e.get(f"{arm}_traded")) if arm != "base" else len(rs)
        st[arm] = {"r_per_signal": round(sum(rs) / n, 3) if n else None, "traded": traded,
                   "avg_r_traded": (round(sum(rs) / traded, 3) if traded else None)}
    b, lv, rd = st["base"]["r_per_signal"], st["level"]["r_per_signal"], st["random"]["r_per_signal"]
    ok = (n and st["level"]["traded"] >= MIN_N and lv is not None and b is not None and rd is not None
          and lv >= b + GAP_R and lv >= rd + GAP_R)
    bt_, lt_, rt_ = (st[a]["avg_r_traded"] for a in ARMS)
    better = (st["level"]["traded"] >= MIN_N and None not in (bt_, lt_, rt_)
              and lt_ >= bt_ + GAP_TRADE and lt_ >= rt_ + GAP_TRADE)
    return {"signals": n, "with_level": sum(1 for e in evs if e["has_level"]), "stats": st, "passes": bool(ok),
            "better": bool(better)}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    return {"eras": {"A": a, "B": b}, "edge": a["passes"] and b["passes"], "better": a["better"] and b["better"],
            "coins": sorted({e["symbol"] for e in evs}), "n": len(evs)}


NAMES = {"base": "enter at the signal's close (as now)", "level": "pullback into a REAL level + reaction",
         "random": "the same at a RANDOM level"}


def render_telegram(res: Dict) -> str:
    L = ["🧲 Trend + pullback into a level: an edge? (pre-registered)",
         f"{len(res['coins'])} coins · 1D signal ≥ 62 = direction · level = S/R zone, fresh order block or open FVG "
         "0.3–3 ATR away · trigger = a close back out of it within 5 days · same 1:1 trade at 1.5×ATR, 20 days, "
         "costs · R per signal (no trade = 0)", ""]
    for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
        e = res["eras"][k]
        L.append(f"━━ {nm}: {e['signals']} signals, {e['with_level']} had a level in range")
        for arm in ARMS:
            s = e["stats"][arm]
            mark = (f" {'✅' if e['passes'] else '❌'} overall · {'✅' if e['better'] else '❌'} per trade"
                    if arm == "level" else "")
            L.append(f"  {NAMES[arm]}: {s['r_per_signal']:+.3f}R per signal · {s['traded']} traded · "
                     f"{(s['avg_r_traded'] or 0):+.3f}R a trade{mark}" if s["r_per_signal"] is not None
                     else f"  {NAMES[arm]}: none")
        L.append("")
    L.append(f"Verdict (more money overall): {'the level entry IS an edge ✅' if res['edge'] else 'no ❌'}")
    L.append(f"Verdict (better trades, a quality filter): {'YES ✅' if res['better'] else 'no ❌'}")
    L.append(f"Rules fixed before the run: 30+ level trades and, in BOTH eras, R per signal ≥ base's + {GAP_R:g} and "
             f"≥ the random level's + {GAP_R:g} (overall); R per trade ≥ both + {GAP_TRADE:g} (quality). Coins move "
             "together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="pullback_level_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
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
        print(f"signals replayed: {res['n']}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
