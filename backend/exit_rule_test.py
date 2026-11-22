"""
Early exits for open swing trades: cut losses when a reversal starts? A
pre-registered test (written Nov 22 2026, before any run).

Why: an open 1D trade only ends at its stop, a target or the time limit -
nothing reacts when the market turns. Cutting losers early also closes some
trades that would have recovered, so it is tested.

How: every 1D signal at strength >= 62 (the dashboard's 1D analysis + real
Fear & Greed, as regime_switch_test / strength_tier_test), all coins, 2018 ->
now; one per coin per direction per 20 days. The trade: 1:1 at 1.5 x 1D ATR
from the close, max 20 days, stop first inside a day, 0.13% costs. Variants
(checked at each later daily close, acted on at that close):
  base       no early exit (the trade as measured)
  opp62      exit when the coin's 1D signal points the OTHER way at >= 62
  opp51      the same at >= 51 (Strong) - an earlier warning
  be_half    once a day's extreme reaches +0.5R, the stop moves to entry from
             the next day (break-even)
A VARIANT IS ADOPTED only if, in BOTH eras (2018-2021 and 2022-now): its
average R per trade is at least the base's AND its average losing trade is
smaller (less negative) than the base's. Of those that pass, the one with the
highest average R goes to the published picks' monitor. Coins move together.

    python -m exit_rule_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
VARIANTS = ("base", "opp62", "opp51", "be_half")


def manage(direction: str, entry: float, atr: float, after: Sequence[Dict],
           later_sigs: Sequence[Optional[Dict]], variant: str) -> Dict:
    """One trade under a variant. `later_sigs[k]` = the signal at after[k]'s close."""
    sgn = 1 if direction == "LONG" else -1
    risk = rv.ATR_MULT * atr
    stop, tgt = entry - sgn * risk, entry + sgn * risk
    cost_r = rv.COST * entry / risk
    be_armed = False
    for k, c in enumerate(after[:rv.HOLD_DAYS]):
        hi, lo, close = float(c["high"]), float(c["low"]), float(c["close"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            r = sgn * (stop - entry) / risk - cost_r
            return {"result": "breakeven" if stop == entry else "stop", "r": r, "days": k + 1}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r, "days": k + 1}
        if variant in ("opp62", "opp51"):
            s = later_sigs[k] if k < len(later_sigs) else None
            floor = 62.0 if variant == "opp62" else 51.0
            if s and s.get("direction") in ("LONG", "SHORT") and s["direction"] != direction \
                    and float(s.get("strength") or 0) >= floor:
                return {"result": "early", "r": sgn * (close - entry) / risk - cost_r, "days": k + 1}
        if variant == "be_half" and not be_armed:
            best = hi if sgn > 0 else lo
            if sgn * (best - entry) >= 0.5 * risk:
                be_armed = True
                stop = entry                               # from the next day
    if len(after) < rv.HOLD_DAYS:
        return {"result": "pending", "r": None}
    last = float(after[rv.HOLD_DAYS - 1]["close"])
    return {"result": "time", "r": sgn * (last - entry) / risk - cost_r, "days": rv.HOLD_DAYS}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    sigs: Dict[int, Dict] = {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sigs[i] = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
    out, last = [], {}
    for i, sig in sigs.items():
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        window = daily[max(0, i - 20):i + 1]
        atr = rv._atr(window)
        if not atr:
            continue
        last[d] = i
        after = daily[i + 1:]
        later = [sigs.get(i + 1 + k) for k in range(min(len(after), rv.HOLD_DAYS))]
        entry = float(daily[i]["close"])
        out.append({"symbol": sym, "ts": int(daily[i]["timestamp"]), "direction": d,
                    **{v: manage(d, entry, atr, after, later, v) for v in VARIANTS}})
    return out


def stats(evs: Sequence[Dict], v: str) -> Dict:
    rs = [e[v]["r"] for e in evs if e[v]["r"] is not None]
    losses = [r for r in rs if r < 0]
    early = sum(1 for e in evs if e[v]["result"] == "early")
    return {"n": len(rs), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "total_r": round(sum(rs), 1), "win_pct": round(100 * sum(r > 0 for r in rs) / len(rs), 1) if rs else None,
            "avg_loss": round(sum(losses) / len(losses), 3) if losses else None, "early_exits": early}


def era(evs: Sequence[Dict]) -> Dict:
    st = {v: stats(evs, v) for v in VARIANTS}
    b = st["base"]
    passes = {}
    for v in VARIANTS[1:]:
        s = st[v]
        passes[v] = (s["avg_r"] is not None and b["avg_r"] is not None and s["avg_r"] >= b["avg_r"]
                     and s["avg_loss"] is not None and b["avg_loss"] is not None
                     and s["avg_loss"] > b["avg_loss"])
    return {"stats": st, "passes": passes}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    ok = [v for v in VARIANTS[1:] if a["passes"][v] and b["passes"][v]]
    best = max(ok, key=lambda v: (a["stats"][v]["avg_r"] + b["stats"][v]["avg_r"])) if ok else None
    import scalp_baseline as sb
    return {"eras": {"A": a, "B": b}, "adopt": best, "passed": ok,
            "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(min(e["ts"] for e in evs)) if evs else "-"}


def _s(st: Dict) -> str:
    if not st["n"]:
        return "no trades"
    return (f"{st['n']} trades · win {st['win_pct']}% · avg {st['avg_r']:+.3f}R · avg loss "
            f"{st['avg_loss']:+.2f}R · total {st['total_r']:+.1f}R"
            + (f" · {st['early_exits']} early exits" if st["early_exits"] else ""))


NAMES = {"base": "no early exit (as now)", "opp62": "exit on an opposite 62+ signal",
         "opp51": "exit on an opposite 51+ signal", "be_half": "stop to entry after +0.5R"}


def render_telegram(res: Dict) -> str:
    lines = ["🚪 Early exits for open 1D trades: cut losses when a reversal starts? (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5 × 1D ATR · "
             "max 20 days · 0.13% costs", ""]
    for k, name in (("A", "ERA A: 2018 → 2021"), ("B", "ERA B: 2022 → now")):
        e = res["eras"][k]
        lines.append(f"━━ {name}")
        for v in VARIANTS:
            mark = "" if v == "base" else (" ✅" if e["passes"][v] else " ❌")
            lines.append(f"  {NAMES[v]}: {_s(e['stats'][v])}{mark}")
        lines.append("")
    lines.append(f"Verdict: {'ADOPT ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'none adopted ❌ (trades keep their stop / target / time exit)'}")
    lines.append("Rule fixed before the run: a variant needs avg R ≥ the base's AND a smaller average loss, in "
                 "BOTH eras; the best of those is adopted. Coins move together, so certainty is overstated.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="exit_rule_test", description=__doc__.split("\n\n")[0])
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
        print(f"coins replayed: {len(res['coins'])}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
