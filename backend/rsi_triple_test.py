"""
The "Triple RSI" strategy (quantifiedstrategies.com, S&P 500) on crypto's
weekly charts: 1W and the business week (BW). A pre-registered test
(written Nov 28 2026, before any run).

The rules, as posted (long only):
  entry   the 5-period RSI is below 30
          the 5-period RSI is down for the third bar in a row
          the 5-period RSI was below 60 three bars ago
          the close is above the 200-DAY moving average
  exit    the 5-period RSI closes above 50
Bars are 1W (Monday-Sunday) or BW (Monday-Friday, close_publish.business_weeks);
the RSI is on those bars, the 200-day average on the daily closes up to the
bar's last day (the original's filter, not 200 weeks). Entry and exit at the
bar's close, 0.13% costs, one trade per coin at a time, no stop and no time
limit (as posted); a trade still open at the end of the data is left out.
1D (the original timeframe) is run too, as context only.

The yardstick: crypto mostly rose while above its 200-day average, so a
profit alone proves nothing. Each trade is paired with the SAME coin's
average return over the same number of bars from EVERY bar that was above its
200-day average (buying any time in an uptrend). The difference is what the
rules add.

AN EDGE (per timeframe, 1W and BW separately) only if, in BOTH eras
(2018-2021, 2022-now, by entry): 30+ closed trades, average return > 0, AND
the trades beat their yardstick by z >= 2 (paired). Coins move together.

    python -m rsi_triple_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence

import indicators as ind
import regime_switch_test as rgs

DAY_MS = 86_400_000
COST = 0.0013
RSI_N, SMA_DAYS = 5, 200
TFS = ("1W", "BW", "1D")
DECIDE = ("1W", "BW")
MIN_N, MIN_Z = 30, 2.0
SPAN_DAYS = {"1W": 7, "BW": 5, "1D": 1}


def bars_for(daily: Sequence[Dict], tf: str) -> List[Dict]:
    if tf == "1D":
        return list(daily)
    if tf == "1W":
        import read_study as rs
        return rs.weekly_from_daily(daily)
    import close_publish as cp
    bw = cp.business_weeks(daily)
    return bw[:-1] if bw else bw                       # the last one may be unfinished


def sma_by_day(daily: Sequence[Dict]) -> Dict[int, float]:
    """{day open ms: 200-day average of closes up to and including that day}."""
    out, run = {}, 0.0
    closes = [float(c["close"]) for c in daily]
    for i, c in enumerate(closes):
        run += c
        if i >= SMA_DAYS:
            run -= closes[i - SMA_DAYS]
        if i >= SMA_DAYS - 1:
            out[int(daily[i]["timestamp"]) // DAY_MS * DAY_MS] = run / SMA_DAYS
    return out


def scan(daily: Sequence[Dict], tf: str, sym: str = "") -> Dict[str, List]:
    """{"trades": [...], "uptrend": [(i, close)]} on one coin's `tf` bars."""
    bars = bars_for(daily, tf)
    sma = sma_by_day(daily)
    closes = [float(b["close"]) for b in bars]
    rsi = ind.calculate_rsi_series(closes, RSI_N)
    last_day = [int(b["timestamp"]) // DAY_MS * DAY_MS + (SPAN_DAYS[tf] - 1) * DAY_MS for b in bars]
    above = [(sma.get(d) is not None and closes[i] > sma[d]) for i, d in enumerate(last_day)]
    trades, i = [], 3
    while i < len(bars):
        r = rsi[i]
        ok = (r is not None and r < 30 and all(rsi[k] is not None for k in (i - 1, i - 2, i - 3))
              and r < rsi[i - 1] < rsi[i - 2] < rsi[i - 3] and rsi[i - 3] < 60 and above[i])
        if not ok:
            i += 1
            continue
        j = next((k for k in range(i + 1, len(bars)) if rsi[k] is not None and rsi[k] > 50), None)
        if j is None:
            break                                       # still open at the end of the data
        trades.append({"symbol": sym, "tf": tf, "ts": int(bars[i]["timestamp"]), "i": i, "bars": j - i,
                       "ret": closes[j] / closes[i] - 1 - COST})
        i = j + 1
    return {"trades": trades, "closes": closes, "above": above}


def yardstick(closes: Sequence[float], above: Sequence[bool], h: int) -> Optional[float]:
    """Average h-bar return (after costs) from every bar above the 200-day average."""
    rs = [closes[k + h] / closes[k] - 1 - COST for k in range(len(closes) - h) if above[k]]
    return sum(rs) / len(rs) if rs else None


def period(trades: Sequence[Dict]) -> Dict:
    n = len(trades)
    if not n:
        return {"n": 0, "avg_ret": None, "win_pct": None, "avg_bars": None, "edge": None, "z": None,
                "passes": False}
    rets = [t["ret"] for t in trades]
    diffs = [t["ret"] - t["base"] for t in trades if t.get("base") is not None]
    m = sum(rets) / n
    z = None
    if len(diffs) > 1:
        dm = sum(diffs) / len(diffs)
        sd = (sum((x - dm) ** 2 for x in diffs) / (len(diffs) - 1)) ** 0.5
        z = round(dm / (sd / len(diffs) ** 0.5), 2) if sd > 0 else None
    edge = round(100 * sum(diffs) / len(diffs), 2) if diffs else None
    return {"n": n, "avg_ret": round(100 * m, 2), "win_pct": round(100 * sum(r > 0 for r in rets) / n, 1),
            "avg_bars": round(sum(t["bars"] for t in trades) / n, 1), "edge": edge, "z": z,
            "passes": n >= MIN_N and m > 0 and z is not None and z >= MIN_Z}


def run(daily_by_coin: Dict[str, List[Dict]], *, log=print) -> Dict:
    out = {}
    for tf in TFS:
        trades = []
        for sym, daily in daily_by_coin.items():
            s = scan(daily, tf, sym)
            cache: Dict[int, Optional[float]] = {}
            for t in s["trades"]:
                if t["bars"] not in cache:
                    cache[t["bars"]] = yardstick(s["closes"], s["above"], t["bars"])
                t["base"] = cache[t["bars"]]
            trades += s["trades"]
        a = period([t for t in trades if t["ts"] < rgs.ERA_SPLIT])
        b = period([t for t in trades if t["ts"] >= rgs.ERA_SPLIT])
        out[tf] = {"eras": {"A": a, "B": b}, "edge": a["passes"] and b["passes"], "n": len(trades),
                   "coins": len({t["symbol"] for t in trades})}
        log(f"  {tf}: scanned")                          # no results in the public log
    return out


def _s(p: Dict) -> str:
    if not p["n"]:
        return "no trades"
    return (f"{p['n']} trades · avg {p['avg_ret']:+.2f}% · win {p['win_pct']}% · held {p['avg_bars']} bars · vs "
            f"buying any uptrend bar {p['edge']:+.2f}% (z {p['z']})")


def render_telegram(res: Dict) -> str:
    L = ["📈 'Triple RSI' on crypto weekly charts (pre-registered)",
         "RSI(5) < 30, down 3 bars in a row, < 60 three bars ago, close > 200-day average → long; out when RSI(5) "
         "> 50 · no stop, costs · all scan coins", ""]
    names = {"1W": "1W (crypto week)", "BW": "BW (business week)", "1D": "1D (the original - context only)"}
    for tf in TFS:
        r = res[tf]
        L.append(f"━━ {names[tf]}")
        for k, nm in (("A", "2018 → 2021"), ("B", "2022 → now")):
            p = r["eras"][k]
            mark = "" if tf not in DECIDE else (" ✅" if p["passes"] else " ❌")
            L.append(f"  {nm}: {_s(p)}{mark}")
        if tf in DECIDE:
            L.append(f"  {tf} verdict: {'an EDGE ✅' if r['edge'] else 'no edge ❌'}")
        L.append("")
    L.append("Rule fixed before the run (1W and BW each): 30+ trades, avg return > 0 AND beating the same coin "
             "bought at any uptrend bar for the same hold by z ≥ 2, in BOTH eras. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="rsi_triple_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    res = run(daily, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"trades: 1W {res['1W']['n']} / BW {res['BW']['n']} / 1D {res['1D']['n']}; "
              f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
