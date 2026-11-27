"""
1D picks in calm vs wild markets: does BTC's volatility at the pick change
how it does? A pre-registered test (written Nov 27 2026, before any run).

The state: BTC's ATR(14) as a % of its close on the pick's candle, ranked
against BTC's own previous 365 days -
  calm    bottom third (a compressed market)
  normal  middle third
  wild    top third (an expanded market)

The trades: every 1D signal at strength >= 62 (tao_case_1d_test.signal_at),
all scan coins, 2018 -> now, one per coin per direction per 20 days; the
standard trade (reversal_1d_test.walk): 1:1 at 1.5 x ATR, 20 days max, stop
first, 0.13% costs.

A STATE IS FILTERED (no 1D picks in it) only if, in BOTH eras (2018-2021,
2022-now): 30+ picks in it AND their average R is below the other two states'
by z <= -2 (Welch). At most one state (the worse-in-both); otherwise no
change. Coins move together.

    python -m vol_regime_1d_test --days 3400
"""
from __future__ import annotations

import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
STATES = ("calm", "normal", "wild")
MIN_N, MAX_Z = 30, -2.0
RANK_DAYS = 365


def btc_states(btc: Sequence[Dict]) -> Dict[int, str]:
    """{day ms: state} from BTC's ATR% vs its previous RANK_DAYS days."""
    pct: List[float] = []
    out: Dict[int, str] = {}
    for i in range(len(btc)):
        a = rv._atr(btc[max(0, i - 20):i + 1])
        c = float(btc[i]["close"])
        if not a or c <= 0:
            pct.append(float("nan"))
            continue
        v = a / c
        prev = [x for x in pct[-RANK_DAYS:] if x == x]
        pct.append(v)
        if len(prev) < RANK_DAYS // 2:
            continue
        rank = sum(1 for x in prev if x < v) / len(prev)
        out[int(btc[i]["timestamp"]) // DAY_MS * DAY_MS] = "calm" if rank < 1 / 3 else "wild" if rank >= 2 / 3 else "normal"
    return out


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], states: Dict[int, str], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        st = states.get(ts // DAY_MS * DAY_MS)
        if st is None:
            continue
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        res = rv.walk(d, float(daily[i]["close"]), atr, daily[i + 1:])
        if res["r"] is None:
            continue
        last[d] = i
        out.append({"symbol": sym, "ts": ts, "direction": d, "state": st, "r": res["r"]})
    return out


def _st(rs: Sequence[float]) -> Dict:
    n = len(rs)
    if not n:
        return {"n": 0, "avg_r": None, "win_pct": None, "_m": None, "_sd": None}
    m = sum(rs) / n
    sd = (sum((x - m) ** 2 for x in rs) / (n - 1)) ** 0.5 if n > 1 else None
    return {"n": n, "avg_r": round(m, 3), "win_pct": round(100 * sum(r > 0 for r in rs) / n, 1), "_m": m, "_sd": sd}


def _z(a: Dict, b: Dict) -> Optional[float]:
    if a["n"] < 2 or b["n"] < 2 or not a["_sd"] or not b["_sd"]:
        return None
    se = (a["_sd"] ** 2 / a["n"] + b["_sd"] ** 2 / b["n"]) ** 0.5
    return round((a["_m"] - b["_m"]) / se, 2) if se > 0 else None


def era(evs: Sequence[Dict]) -> Dict:
    rows = {}
    for s in STATES:
        me = _st([e["r"] for e in evs if e["state"] == s])
        rest = _st([e["r"] for e in evs if e["state"] != s])
        z = _z(me, rest)
        rows[s] = {**{k: v for k, v in me.items() if not k.startswith("_")}, "z": z,
                   "worse": me["n"] >= MIN_N and z is not None and z <= MAX_Z,
                   "longs": _st([e["r"] for e in evs if e["state"] == s and e["direction"] == "LONG"])["avg_r"],
                   "shorts": _st([e["r"] for e in evs if e["state"] == s and e["direction"] == "SHORT"])["avg_r"]}
    return rows


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    states = btc_states(daily_by_coin.get("BTC") or [])
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, states, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    bad = [s for s in STATES if a[s]["worse"] and b[s]["worse"]]
    return {"eras": {"A": a, "B": b}, "filter": bad[0] if len(bad) == 1 else None,
            "coins": sorted({e["symbol"] for e in evs}), "n": len(evs)}


def render_telegram(res: Dict) -> str:
    L = ["🌡️ 1D picks in calm vs wild markets (pre-registered)",
         f"{len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5×ATR · 20 days · costs · state = BTC ATR% ranked "
         "vs its last 365 days (thirds)", ""]
    for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
        L.append(f"━━ {nm}")
        for s in STATES:
            r = res["eras"][k][s]
            if not r["n"]:
                L.append(f"  {s}: none")
                continue
            L.append(f"  {s}: {r['n']} · avg {r['avg_r']:+.3f}R · win {r['win_pct']}% · vs the rest z {r['z']}"
                     f"{' ❌ worse' if r['worse'] else ''} · longs {r['longs']}R / shorts {r['shorts']}R")
        L.append("")
    L.append(f"Verdict: {'NO 1D picks when BTC is ' + res['filter'] + ' ✅' if res['filter'] else 'no change - picks in every state ❌'}")
    L.append("Rule fixed before the run: 30+ picks in a state and avg R below the other states by z ≤ −2, in BOTH "
             "eras. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="vol_regime_1d_test", description=__doc__.split("\n\n")[0])
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
    if not daily.get("BTC"):
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = run(daily, fg, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"picks replayed: {res['n']}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
