"""
Three more market-wide leans - US dollar + Nasdaq (risk-on / off), altcoin
breadth vs BTC, hash ribbons. A pre-registered test (written Nov 22 2026,
before any data was fetched).

Why: two money leans (Coinbase premium, stablecoin supply) held in both eras
(macro_lean_test), mostly through the alts. These three read the macro tide,
the rotation into alts, and Bitcoin miners - not chart shapes.

LEANS (each day, from data known by that day's 00:00 UTC close)
  risk      FRED broad dollar index (DTWEXBGS) and Nasdaq Composite
            (NASDAQCOM). The dollar's 20-observation change uses values dated
            at least 7 days back (it is published weekly - no look-ahead);
            Nasdaq uses its latest close on or before the day.
              dollar <= -1% AND Nasdaq above its 50-day average -> LONG
              dollar >= +1% AND Nasdaq below its 50-day average -> SHORT
  breadth   the share of the scanned ALTS whose 30-day return beats BTC's:
              >= 70% -> LONG, <= 30% -> SHORT       (traded on alts only)
  ribbons   Bitcoin hash rate (blockchain.com), lagged 1 day: the day the
            30-day average crosses back ABOVE the 60-day after at least 10
            days below (miner capitulation over) -> LONG on that day only
Each lean is market-wide: on a lean day every coin (alts only for breadth)
trades the lean's side at the close, 1:1 at 1.5 x 1D ATR, max 20 days, stop
first, 0.13% costs; one per coin per lean per side per 20 days, time order.

A LEAN HOLDS only if, in BOTH eras (2018-2021 and 2022-now), its trades win
>= 53% with z >= 2, avg R > 0 and 30+ resolved. Context: BTC only, LONG and
SHORT days, and the number of distinct lean DAYS (hash ribbons fire rarely,
so a pass there is weak evidence). A lean that holds goes to a shadow paper
test before any live use. Coins move together, so z overstates certainty.

    python -m risk_lean_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
LEANS = ("risk", "breadth", "ribbons")
NAMES = {"risk": "US dollar + Nasdaq (risk-on / off)",
         "breadth": "altcoin breadth vs BTC (≥ 70% / ≤ 30%)",
         "ribbons": "hash ribbons (miner capitulation over)"}
DOLLAR_PCT, DOLLAR_OBS, DOLLAR_LAG_DAYS, NASDAQ_SMA = 1.0, 20, 7, 50
BREADTH_DAYS, BREADTH_HI, BREADTH_LO = 30, 70.0, 30.0
RIB_FAST, RIB_SLOW, RIB_BELOW_DAYS, RIB_LAG_DAYS = 30, 60, 10, 1


def _day(ms: int) -> int:
    return int(ms) // DAY_MS * DAY_MS


def _prep(series: Dict[int, float]) -> Dict:
    s = {k: v for k, v in series.items() if isinstance(k, int)}
    s["__keys__"] = sorted(s)
    return s


# ── leans ────────────────────────────────────────────────────────────────────
def risk_lean(days: Sequence[int], dollar: Dict[int, float], nasdaq: Dict[int, float]) -> Dict[int, str]:
    dl, nq = _prep(dollar), _prep(nasdaq)
    dk, nk = dl["__keys__"], nq["__keys__"]
    import bisect
    out = {}
    for d in days:
        j = bisect.bisect_right(dk, d - DOLLAR_LAG_DAYS * DAY_MS) - 1
        n = bisect.bisect_right(nk, d) - 1
        if j < DOLLAR_OBS or n < NASDAQ_SMA - 1:
            continue
        dchg = (dl[dk[j]] / dl[dk[j - DOLLAR_OBS]] - 1) * 100
        sma = sum(nq[nk[k]] for k in range(n - NASDAQ_SMA + 1, n + 1)) / NASDAQ_SMA
        above = nq[nk[n]] > sma
        if dchg <= -DOLLAR_PCT and above:
            out[d] = "LONG"
        elif dchg >= DOLLAR_PCT and not above:
            out[d] = "SHORT"
    return out


def breadth_lean(closes: Dict[str, Dict[int, float]]) -> Dict[int, str]:
    btc = closes.get("BTC") or {}
    out = {}
    for d in sorted(btc):
        b0 = btc.get(d - BREADTH_DAYS * DAY_MS)
        if not b0:
            continue
        b_ret = btc[d] / b0 - 1
        beat = n = 0
        for sym, c in closes.items():
            if sym == "BTC" or d not in c or not c.get(d - BREADTH_DAYS * DAY_MS):
                continue
            n += 1
            beat += (c[d] / c[d - BREADTH_DAYS * DAY_MS] - 1) > b_ret
        if n < 10:
            continue
        share = 100 * beat / n
        if share >= BREADTH_HI:
            out[d] = "LONG"
        elif share <= BREADTH_LO:
            out[d] = "SHORT"
    return out


def ribbons_lean(hashrate: Dict[int, float]) -> Dict[int, str]:
    ks = sorted(hashrate)
    vals = [hashrate[k] for k in ks]
    out, below = {}, 0
    for i in range(RIB_SLOW, len(ks)):
        fast = sum(vals[i - RIB_FAST + 1:i + 1]) / RIB_FAST
        slow = sum(vals[i - RIB_SLOW + 1:i + 1]) / RIB_SLOW
        if fast < slow:
            below += 1
            continue
        if below >= RIB_BELOW_DAYS:
            out[ks[i] + RIB_LAG_DAYS * DAY_MS] = "LONG"     # known a day later
        below = 0
    return out


# ── trades ───────────────────────────────────────────────────────────────────
def scan(daily: Sequence[Dict], sym: str, leans: Dict[str, Dict[int, str]]) -> List[Dict]:
    out, last = [], {}
    for i in range(20, len(daily)):
        d = _day(daily[i]["timestamp"])
        atr = None
        for lean in LEANS:
            if lean == "breadth" and sym == "BTC":
                continue                                    # an alt read: alts only
            side = (leans.get(lean) or {}).get(d)
            if not side:
                continue
            key = (lean, side)
            if key in last and i - last[key] < rv.HOLD_DAYS:
                continue
            atr = atr or rv._atr(daily[i - 20:i + 1])
            if not atr:
                continue
            last[key] = i
            out.append({"lean": lean, "side": side, "symbol": sym, "ts": d,
                        "x": rv.walk(side, float(daily[i]["close"]), atr, daily[i + 1:])})
    return out


def era(evs: Sequence[Dict]) -> Dict:
    st = rv.side_stats(evs, "x")
    ok = ((st["resolved"] or 0) >= rv.MIN_N and (st["win_pct"] or 0) >= rv.WIN_HI
          and (st["z"] or 0) >= rv.Z_MIN and (st["avg_r"] or 0) > 0)
    return {"stats": st, "holds": ok, "days": len({e["ts"] for e in evs})}


def summarize(evs: Sequence[Dict]) -> Dict:
    out = {}
    for lean in LEANS:
        le = [e for e in evs if e["lean"] == lean]
        a = era([e for e in le if e["ts"] < rgs.ERA_SPLIT])
        b = era([e for e in le if e["ts"] >= rgs.ERA_SPLIT])
        ctx = {name: rv.side_stats(sel, "x") for name, sel in (
            ("BTC only", [e for e in le if e["symbol"] == "BTC"]),
            ("LONG days", [e for e in le if e["side"] == "LONG"]),
            ("SHORT days", [e for e in le if e["side"] == "SHORT"]))}
        out[lean] = {"A": a, "B": b, "holds": a["holds"] and b["holds"], "context": ctx}
    return out


def run(daily_by_coin: Dict[str, List[Dict]], leans: Dict[str, Dict[int, str]], *, log=print) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, leans)
        log(f"  {sym}: scanned")                         # no results in the public log
    return {"summary": summarize(evs), "coins": sorted(daily_by_coin),
            "lean_days": {k: len(v) for k, v in leans.items()}}


def render_telegram(res: Dict) -> str:
    lines = ["🌍 More leans: US dollar + Nasdaq, altcoin breadth, hash ribbons (pre-registered)",
             f"{len(res['coins'])} coins · lean days: " + " · ".join(f"{k} {v}" for k, v in res["lean_days"].items()),
             "On a lean day every coin (alts only for breadth) trades the lean's side at the close · 1:1 at "
             "1.5 × 1D ATR · max 20 days · 0.13% costs · one per coin per lean per side per 20 days", ""]
    for lean in LEANS:
        s = res["summary"][lean]
        lines.append(f"━━ {NAMES[lean]}  →  {'LEAN HOLDS ✅' if s['holds'] else 'no edge ❌'}")
        for k, name in (("A", "2018 → 2021"), ("B", "2022 → now")):
            lines.append(f"  {name}: {rv._s(s[k]['stats'])} · {s[k]['days']} lean days {'✅' if s[k]['holds'] else '❌'}")
        for name, st in s["context"].items():
            lines.append(f"    ({name}: {rv._s(st)})")
        lines.append("")
    lines.append(f"Rule fixed before the run: a lean holds only if its trades win ≥ {rv.WIN_HI:g}% with z ≥ "
                 f"{rv.Z_MIN:g}, avg R > 0 and {rv.MIN_N}+ resolved in BOTH eras; then a shadow paper test "
                 "before any live use. Few lean days = weak evidence. Coins move together, so z overstates "
                 "certainty.")
    return "\n".join(lines)


# ── data (free, keyless) ─────────────────────────────────────────────────────
def fetch_fred(series_id: str, get: Optional[Callable] = None) -> Dict[int, float]:
    """{day ms: value} from FRED's public CSV ('.' = missing)."""
    from datetime import datetime, timezone
    if get is None:
        import requests
        get = lambda sid: requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",   # noqa: E731
                                       params={"id": sid}, timeout=30).text
    out = {}
    for line in (get(series_id) or "").splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            d = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            out[int(d.timestamp() * 1000)] = float(parts[1])
        except ValueError:
            continue
    return out


def fetch_hashrate(get: Optional[Callable] = None) -> Dict[int, float]:
    if get is None:
        import requests
        get = lambda: requests.get("https://api.blockchain.info/charts/hash-rate",   # noqa: E731
                                   params={"timespan": "all", "format": "json", "sampled": "false"},
                                   timeout=30).json()
    out = {}
    for p in (get() or {}).get("values") or []:
        try:
            out[_day(int(p["x"]) * 1000)] = float(p["y"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="risk_lean_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    days = sorted({_day(c["timestamp"]) for v in daily.values() for c in v})
    leans: Dict[str, Dict[int, str]] = {}
    try:
        dollar, nasdaq = fetch_fred("DTWEXBGS"), fetch_fred("NASDAQCOM")
        leans["risk"] = risk_lean(days, dollar, nasdaq)
        log(f"  fred days: dollar {len(dollar)}, nasdaq {len(nasdaq)}")
    except Exception as exc:                             # noqa: BLE001
        log(f"  fred unavailable: {type(exc).__name__}")
        leans["risk"] = {}
    closes = {s: {_day(c["timestamp"]): float(c["close"]) for c in v} for s, v in daily.items()}
    leans["breadth"] = breadth_lean(closes)
    try:
        hr = fetch_hashrate()
        leans["ribbons"] = ribbons_lean(hr)
        log(f"  hash-rate days: {len(hr)}")
    except Exception as exc:                             # noqa: BLE001
        log(f"  hash rate unavailable: {type(exc).__name__}")
        leans["ribbons"] = {}
    res = run(daily, leans, log=log)
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
