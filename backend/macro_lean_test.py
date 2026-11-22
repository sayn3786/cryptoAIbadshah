"""
Three real-money leans - Coinbase premium, stablecoin supply, Fear & Greed
extremes. A pre-registered test (written Nov 22 2026, before any data was
fetched).

Why: every price-only read tested this month was a coin flip. These three
measure money and positioning, with free daily history back to 2018-2019,
so they can be tested now instead of forward.

LEANS (each day, from data known at that day's close)
  premium   Coinbase BTC-USD close vs Binance BTC-USDT close, the 3-day mean:
            >= +0.10% -> LONG (US buyers paying up), <= -0.10% -> SHORT
  stable    total stablecoin supply (DefiLlama), the 30-day change:
            >= +2% -> LONG (new dollars coming in), < 0% -> SHORT (shrinking)
  fg        Fear & Greed (alternative.me): <= 20 -> LONG (extreme fear),
            >= 80 -> SHORT (extreme greed)
Each lean is market-wide: on a lean day, every coin (all scanned coins, from
their own daily candles) gets a trade in the lean's direction at the close,
1:1 at 1.5 x 1D ATR, max 20 days, stop first, 0.13% costs; one per coin per
lean per direction per 20 days, in time order.

A LEAN HOLDS only if, in BOTH eras (2018-2021 and 2022-now), its trades win
>= 53% with z >= 2, avg R > 0 and 30+ resolved. Context (decides nothing):
BTC only; LONG and SHORT days separately. A lean that holds goes to a shadow
paper test on the 1D book (+/-10 points) before any live use. Coins move
together, so z overstates certainty (one lean day = many coins).

    python -m macro_lean_test --days 3400
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
LEANS = ("premium", "stable", "fg")
NAMES = {"premium": "Coinbase premium (3-day mean ±0.10%)",
         "stable": "stablecoin supply (30-day change ≥ +2% / < 0%)",
         "fg": "Fear & Greed extremes (≤ 20 long / ≥ 80 short)"}
PREMIUM_PCT, PREMIUM_DAYS = 0.10, 3
STABLE_UP, STABLE_DOWN, STABLE_DAYS = 2.0, 0.0, 30
FG_LOW, FG_HIGH = 20, 80


def _day(ms: int) -> int:
    return int(ms) // DAY_MS * DAY_MS


# ── the three leans, {day ms: "LONG" | "SHORT"} ─────────────────────────────
def premium_lean(coinbase: Dict[int, float], offshore: Dict[int, float]) -> Dict[int, str]:
    days = sorted(d for d in coinbase if d in offshore and offshore[d])
    prem = {d: (coinbase[d] / offshore[d] - 1) * 100 for d in days}
    out = {}
    for k in range(PREMIUM_DAYS - 1, len(days)):
        win = days[k - PREMIUM_DAYS + 1:k + 1]
        if win[-1] - win[0] != (PREMIUM_DAYS - 1) * DAY_MS:
            continue                                        # gap in the data
        m = sum(prem[d] for d in win) / PREMIUM_DAYS
        if m >= PREMIUM_PCT:
            out[days[k]] = "LONG"
        elif m <= -PREMIUM_PCT:
            out[days[k]] = "SHORT"
    return out


def stable_lean(supply: Dict[int, float]) -> Dict[int, str]:
    out = {}
    for d, v in supply.items():
        past = supply.get(d - STABLE_DAYS * DAY_MS)
        if not past or not v:
            continue
        chg = (v / past - 1) * 100
        if chg >= STABLE_UP:
            out[d] = "LONG"
        elif chg < STABLE_DOWN:
            out[d] = "SHORT"
    return out


def fg_lean(fg: Dict[int, int]) -> Dict[int, str]:
    return {d: "LONG" if v <= FG_LOW else "SHORT" for d, v in fg.items() if v <= FG_LOW or v >= FG_HIGH}


# ── trades ───────────────────────────────────────────────────────────────────
def scan(daily: Sequence[Dict], sym: str, leans: Dict[str, Dict[int, str]]) -> List[Dict]:
    out, last = [], {}
    for i in range(20, len(daily)):
        d = _day(daily[i]["timestamp"])
        atr = None
        for lean in LEANS:
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
    return {"stats": st, "holds": ok}


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
    lines = ["💵 Real-money leans: Coinbase premium, stablecoin supply, Fear & Greed (pre-registered)",
             f"{len(res['coins'])} coins · lean days: " + " · ".join(f"{k} {v}" for k, v in res["lean_days"].items()),
             "On a lean day every coin trades the lean's side at the close · 1:1 at 1.5 × 1D ATR · max 20 days · "
             "0.13% costs · one per coin per lean per side per 20 days", ""]
    for lean in LEANS:
        s = res["summary"][lean]
        lines.append(f"━━ {NAMES[lean]}  →  {'LEAN HOLDS ✅' if s['holds'] else 'no edge ❌'}")
        for k, name in (("A", "2018 → 2021"), ("B", "2022 → now")):
            lines.append(f"  {name}: {rv._s(s[k]['stats'])} {'✅' if s[k]['holds'] else '❌'}")
        for name, st in s["context"].items():
            lines.append(f"    ({name}: {rv._s(st)})")
        lines.append("")
    lines.append(f"Rule fixed before the run: a lean holds only if its trades win ≥ {rv.WIN_HI:g}% with z ≥ "
                 f"{rv.Z_MIN:g}, avg R > 0 and {rv.MIN_N}+ resolved in BOTH eras; then a shadow paper test "
                 "(±10 points) before any live use. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


# ── data (free, keyless) ─────────────────────────────────────────────────────
def fetch_coinbase_daily(start_ms: int, end_ms: int, get: Optional[Callable] = None) -> Dict[int, float]:
    """{day ms: Coinbase BTC-USD daily close}, paged 300 days at a time."""
    import requests
    from datetime import datetime, timezone
    get = get or (lambda params: requests.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                                              params=params, timeout=20).json())
    out, t = {}, start_ms
    while t < end_ms:
        t2 = min(end_ms, t + 299 * DAY_MS)
        iso = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()   # noqa: E731
        rows = get({"granularity": 86400, "start": iso(t), "end": iso(t2)})
        for r in rows if isinstance(rows, list) else []:
            try:
                out[_day(int(r[0]) * 1000)] = float(r[4])
            except (IndexError, TypeError, ValueError):
                continue
        t = t2 + DAY_MS
        time.sleep(0.4)
    return out


def fetch_stable_supply(get: Optional[Callable] = None) -> Dict[int, float]:
    """{day ms: total stablecoin supply in USD} from DefiLlama."""
    import requests
    get = get or (lambda: requests.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=30).json())
    out = {}
    for r in get() or []:
        try:
            v = float((r.get("totalCirculatingUSD") or {}).get("peggedUSD"))
            out[_day(int(r["date"]) * 1000)] = v
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="macro_lean_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    import tao_case_1d_test as tc
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    leans: Dict[str, Dict[int, str]] = {}
    btc = {_day(c["timestamp"]): float(c["close"]) for c in daily.get("BTC") or []}
    try:
        end = max(btc) + DAY_MS if btc else int(time.time() * 1000)
        cb = fetch_coinbase_daily(min(btc) if btc else end - args.days * DAY_MS, end)
        leans["premium"] = premium_lean(cb, btc)
        log(f"  coinbase days: {len(cb)}")
    except Exception as exc:                             # noqa: BLE001
        log(f"  coinbase unavailable: {type(exc).__name__}")
        leans["premium"] = {}
    try:
        sup = fetch_stable_supply()
        leans["stable"] = stable_lean(sup)
        log(f"  stablecoin days: {len(sup)}")
    except Exception as exc:                             # noqa: BLE001
        log(f"  stablecoin supply unavailable: {type(exc).__name__}")
        leans["stable"] = {}
    try:
        fg = tc.fetch_fear_greed()
        leans["fg"] = fg_lean(fg)
        log(f"  fear & greed days: {len(fg)}")
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed unavailable: {type(exc).__name__}")
        leans["fg"] = {}
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
