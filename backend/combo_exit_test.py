"""
Close an open 1D trade early when the REVERSAL ALERTS fire against it? A
pre-registered test (written Nov 25 2026, before any run).

Why: on Nov 25 SOL, TAO, TRX, XRP and others fired three Telegram alerts at
once - a confirmed bullish RSI divergence, an RSI oversold bottom and a MACD
bullish cross - while 1D shorts were open. The owner asked whether open
trades should be closed when that happens. Each alert alone tested as no
edge; this asks about the COMBINATION, as an exit.

The alerts are the live ones, from the same code that sends them
(app._confirmed_patterns_for / app._indicator_flips_for on the closed 1D
candles, 240 bars): against a SHORT, a confirmed bullish RSI divergence
(within 5 closes), an RSI oversold bottom (within 5 closes, still active)
and a MACD bullish cross (within 1 close); against a LONG the bearish
mirror.

The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at),
all scan coins, 2018 -> now, one per coin per direction per 20 days; 1:1 at
1.5 x 1D ATR from the close, max 20 days, stop first, 0.13% costs.
  base    the opposite-signal exit (out at the daily close when the coin's
          1D signal points the other way at 62+; time_stop_test's base)
  all3    base + out at the daily close when ALL THREE alerts are against it
  any2    base + out when at least TWO of the three are against it
AN EXIT IS ADOPTED only if, in BOTH eras (2018-2021, 2022-now), on the
trades it actually closed: 30+ of them, closing them SAVED R on average
versus leaving them to the base exit (paired, trade by trade) with z >= 2,
AND the variant's overall average R is >= the base's. Of those that pass,
the higher average R across the eras. Coins move together.
(Tightened Nov 25 2026 BEFORE the real run: the random-walk dry run let
"2 of 3" pass the older rule - avg R >= base and a smaller average loss -
on a tie plus the mechanical loss cut every early exit gives.)

    python -m combo_exit_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import exit_rule_test as er
import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
VARIANTS = ("base", "all3", "any2")
NEED = {"all3": 3, "any2": 2}


def live_alerts(window: Sequence[Dict]) -> Dict[str, int]:
    """{"bullish": n, "bearish": n}: how many of divergence / oversold-or-
    overbought / MACD cross fire each way on this closed window (0-3)."""
    import app
    out = {"bullish": set(), "bearish": set()}
    try:
        for a in app._confirmed_patterns_for(list(window), "1D"):
            d = a.get("direction")
            if d not in out:
                continue
            if a.get("kind") == "divergence":
                out[d].add("div")
            elif a.get("kind") == "rsi_swing" and a.get("status", "active") == "active":
                out[d].add("rsi")
    except Exception:
        pass
    try:
        for a in app._indicator_flips_for(list(window), "1D"):
            if a.get("type") == "macd" and a.get("direction") in out:
                out[a["direction"]].add("macd")
    except Exception:
        pass
    return {k: len(v) for k, v in out.items()}


def manage(direction: str, entry: float, atr: float, after: Sequence[Dict],
           later_sigs: Sequence[Optional[Dict]], alerts_at: Callable[[int], Dict[str, int]],
           need: Optional[int]) -> Dict:
    sgn = 1 if direction == "LONG" else -1
    against = "bullish" if direction == "SHORT" else "bearish"
    risk = rv.ATR_MULT * atr
    stop, tgt = entry - sgn * risk, entry + sgn * risk
    cost_r = rv.COST * entry / risk
    for k, c in enumerate(after[:rv.HOLD_DAYS]):
        hi, lo, close = float(c["high"]), float(c["low"]), float(c["close"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            return {"result": "stop", "r": -1 - cost_r, "days": k + 1}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r, "days": k + 1}
        s = later_sigs[k] if k < len(later_sigs) else None
        if s and s.get("direction") in ("LONG", "SHORT") and s["direction"] != direction \
                and float(s.get("strength") or 0) >= FLOOR:
            return {"result": "early", "r": sgn * (close - entry) / risk - cost_r, "days": k + 1}
        if need is not None and alerts_at(k).get(against, 0) >= need:
            return {"result": "alerts", "r": sgn * (close - entry) / risk - cost_r, "days": k + 1}
    if len(after) < rv.HOLD_DAYS:
        return {"result": "pending", "r": None}
    last = float(after[rv.HOLD_DAYS - 1]["close"])
    return {"result": "time", "r": sgn * (last - entry) / risk - cost_r, "days": rv.HOLD_DAYS}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         signal_fn: Callable = tc.signal_at, alert_fn: Callable = live_alerts) -> List[Dict]:
    sigs: Dict[int, Dict] = {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sigs[i] = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
    cache: Dict[int, Dict[str, int]] = {}

    def alerts_on(i: int) -> Dict[str, int]:            # lazily: only days inside a trade
        if i not in cache:
            cache[i] = alert_fn(daily[max(0, i - rv.LOOKBACK + 1):i + 1])
        return cache[i]
    out, last = [], {}
    for i, sig in sigs.items():
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        last[d] = i
        after = daily[i + 1:]
        later = [sigs.get(i + 1 + k) for k in range(min(len(after), rv.HOLD_DAYS))]
        entry = float(daily[i]["close"])
        at = (lambda k, _i=i: alerts_on(_i + 1 + k))
        out.append({"symbol": sym, "ts": int(daily[i]["timestamp"]), "direction": d,
                    **{v: manage(d, entry, atr, after, later, at, NEED.get(v)) for v in VARIANTS}})
    return out


def saved(evs: Sequence[Dict], v: str) -> Dict:
    """On the trades the alerts closed: R at the alert exit minus the base's R."""
    d = [e[v]["r"] - e["base"]["r"] for e in evs
         if e[v]["result"] == "alerts" and e[v]["r"] is not None and e["base"]["r"] is not None]
    n = len(d)
    if n < 2:
        return {"n": n, "mean": round(d[0], 3) if d else None, "z": None}
    m = sum(d) / n
    sd = (sum((x - m) ** 2 for x in d) / (n - 1)) ** 0.5
    return {"n": n, "mean": round(m, 3), "z": round(m / (sd / n ** 0.5), 2) if sd > 0 else None}


def era(evs: Sequence[Dict]) -> Dict:
    st = {v: er.stats(evs, v) for v in VARIANTS}
    for v in VARIANTS:
        st[v]["alert_exits"] = sum(1 for e in evs if e[v]["result"] == "alerts")
    b = st["base"]
    sv = {v: saved(evs, v) for v in VARIANTS[1:]}
    passes = {v: (sv[v]["n"] >= 30 and (sv[v]["mean"] or 0) > 0 and (sv[v]["z"] or 0) >= 2
                  and st[v]["avg_r"] is not None and b["avg_r"] is not None and st[v]["avg_r"] >= b["avg_r"])
              for v in VARIANTS[1:]}
    return {"stats": st, "saved": sv, "passes": passes}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    ok = [v for v in VARIANTS[1:] if a["passes"][v] and b["passes"][v]]
    best = max(ok, key=lambda v: a["stats"][v]["avg_r"] + b["stats"][v]["avg_r"]) if ok else None
    return {"eras": {"A": a, "B": b}, "adopt": best, "coins": sorted({e["symbol"] for e in evs})}


NAMES = {"base": "opposite-signal exit (base)", "all3": "+ out when all 3 alerts fire against it",
         "any2": "+ out when 2 of the 3 fire against it"}


def _s(st: Dict) -> str:
    if not st["n"]:
        return "no trades"
    return (f"{st['n']} · win {st['win_pct']}% · avg {st['avg_r']:+.3f}R · avg loss {st['avg_loss']:+.2f}R · "
            f"total {st['total_r']:+.0f}R" + (f" · {st['alert_exits']} closed by alerts" if st.get("alert_exits")
                                               else ""))


def render_telegram(res: Dict) -> str:
    L = ["🚨 Close open 1D trades when the reversal alerts fire against them? (pre-registered)",
         f"{len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5×ATR · 20 days max · costs · alerts = RSI "
         "divergence + RSI oversold/overbought + MACD cross (the live Telegram ones)", ""]
    for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
        e = res["eras"][k]
        L.append(f"━━ {nm}")
        for v in VARIANTS:
            mark = "" if v == "base" else (" ✅" if e["passes"][v] else " ❌")
            L.append(f"  {NAMES[v]}: {_s(e['stats'][v])}{mark}")
            if v != "base" and e["saved"][v]["n"]:
                sv = e["saved"][v]
                L.append(f"      on the {sv['n']} trades it closed: {sv['mean']:+.3f}R each vs holding"
                         + (f" (z {sv['z']:+.2f})" if sv["z"] is not None else ""))
        L.append("")
    L.append(f"Verdict: {'ADOPT ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'no alert exit passes ❌ (the 8H opposite-signal exit stays the only early exit)'}")
    L.append("Rule fixed before the run: on the trades it closed, 30+ and R saved vs holding with z ≥ 2, and "
             "overall avg R ≥ the base's, in BOTH eras. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="combo_exit_test", description=__doc__.split("\n\n")[0])
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
