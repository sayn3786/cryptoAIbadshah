"""
Does a simple MARKET REGIME decide which side of the signal works? A
pre-registered test (written Nov 15 2026, before any run).

Why: this week almost every signal worked in some markets and failed in
others - shorts into "oversold" won in bear markets (2018-19, 2025-26) and
lost in the 2020-21 bull; the 4-hourly signal swung with the period. If the
regime is the deciding factor, trading only WITH it should keep the good
half.

The regime, fixed now (one measure decides):
  BULL   BTC's daily close above its 200-day simple average (both read on the
         signal day's close - no look-ahead)
  BEAR   below it
Signals: the live 1D engine signal (the dashboard's own 1D analysis ->
signals.generate_signal, real Fear & Greed history) at strength >= 62, every
scanned coin with history, at most one per coin per direction per 20 days.
  WITH the regime     LONG in a bull, SHORT in a bear
  AGAINST the regime  LONG in a bear, SHORT in a bull
Trades: 1:1 at 1.5 x 1D ATR, max 20 days, stop first, 0.13% costs.

REGIME SWITCH CONFIRMED only if, in BOTH eras - A: 2018 to the end of 2021
(the 2018 bear and the 2019-21 bull), B: 2022 to now (the 2022 bear, the
2023-25 bull and the 2026 slide):
  - 100+ resolved with-regime trades
  - with-regime trades win >= 53% with z >= 2 and their avg R is above 0
  - their avg R beats the against-regime trades' by at least 0.10
Context (decides nothing): longs and shorts in each regime; and the regime
direction on the SAME entries ignoring the signal's own direction - if that
does as well as "with the regime", the signal adds nothing beyond the regime.
Coins move together, so z overstates certainty.

    python -m regime_switch_test --days 3400
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
SMA_DAYS = 200
COOLDOWN = rv.HOLD_DAYS
MIN_WITH = 100
R_GAP = 0.10
ERA_SPLIT = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def btc_regime(btc_daily: Sequence[Dict]) -> Dict[int, str]:
    """{day ms: 'bull' | 'bear'} from BTC's close vs its 200-day SMA that day."""
    out, closes = {}, []
    for c in sorted(btc_daily, key=lambda x: int(x["timestamp"])):
        closes.append(float(c["close"]))
        if len(closes) >= SMA_DAYS:
            sma = sum(closes[-SMA_DAYS:]) / SMA_DAYS
            out[int(c["timestamp"]) // DAY_MS * DAY_MS] = "bull" if closes[-1] > sma else "bear"
    return out


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], regime: Dict[int, str], *,
         signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        reg = regime.get(ts // DAY_MS * DAY_MS)
        if reg is None:
            continue
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
        entry, after = float(window[-1]["close"]), daily[i + 1:]
        reg_dir = "LONG" if reg == "bull" else "SHORT"
        sig_trade = rv.walk(d, entry, atr, after)
        out.append({"symbol": sym, "ts": ts, "direction": d, "regime": reg,
                    "with_regime": d == reg_dir, "trade": sig_trade,
                    "regime_trade": sig_trade if d == reg_dir else rv.walk(reg_dir, entry, atr, after)})
    return out


def _stats(evs: Sequence[Dict], key: str = "trade") -> Dict:
    return rv.side_stats([{"x": e[key]} for e in evs], "x")


def era(evs: Sequence[Dict]) -> Dict:
    w = [e for e in evs if e["with_regime"]]
    a = [e for e in evs if not e["with_regime"]]
    sw, sa = _stats(w), _stats(a)
    checks = {
        f"{MIN_WITH}+ with-regime trades": (sw["resolved"] or 0) >= MIN_WITH,
        "with-regime wins ≥ 53%, z ≥ 2, avg R > 0": ((sw["win_pct"] or 0) >= rv.WIN_HI
                                                    and (sw["z"] or 0) >= rv.Z_MIN
                                                    and (sw["avg_r"] or 0) > 0),
        f"avg R ≥ {R_GAP:g} above against-regime": ((sw["avg_r"] or 0) - (sa["avg_r"] or 0)) >= R_GAP,
    }
    ctx = {f"{d} in a {r} market": _stats([e for e in evs if e["direction"] == d and e["regime"] == r])
           for r in ("bull", "bear") for d in ("LONG", "SHORT")}
    ctx["regime direction on the same entries (ignoring the signal)"] = _stats(evs, "regime_trade")
    ctx["every 62+ signal (as now)"] = _stats(evs)
    return {"with": sw, "against": sa, "checks": checks, "passes": all(checks.values()), "context": ctx}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    regime = btc_regime(daily_by_coin.get("BTC") or [])
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, regime, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = [e for e in evs if e["ts"] < ERA_SPLIT]
    b = [e for e in evs if e["ts"] >= ERA_SPLIT]
    bull_days = sum(1 for v in regime.values() if v == "bull")
    import scalp_baseline as sb
    return {"eras": {"A": era(a), "B": era(b)}, "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(min(e["ts"] for e in evs)) if evs else "-",
            "bull_share_pct": round(100 * bull_days / len(regime)) if regime else None,
            "confirmed": bool(a and b) and era(a)["passes"] and era(b)["passes"]}


def render_telegram(res: Dict) -> str:
    lines = ["🧭📈📉 REGIME SWITCH: trade only WITH the market (BTC vs its 200-day average)? "
             "(pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · BTC above its 200-day average on "
             f"{res['bull_share_pct']}% of days",
             "1D engine signal at strength ≥ 62 · 1:1 at 1.5 × 1D ATR · max 20 days · 0.13% costs · "
             "one per coin per direction per 20 days", ""]
    names = {"A": "ERA A: 2018 → 2021 (bear 2018, bull 2019-21)",
             "B": "ERA B: 2022 → now (bear 2022, bull 2023-25, slide 2026)"}
    for k in ("A", "B"):
        e = res["eras"][k]
        lines.append(f"━━ {names[k]}  →  {'HOLDS ✅' if e['passes'] else 'FAILS ❌'}")
        lines.append(f"  WITH the regime:    {rv._s(e['with'])}")
        lines.append(f"  AGAINST the regime: {rv._s(e['against'])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        for n, st in e["context"].items():
            lines.append(f"  · {n}: {rv._s(st)}")
        lines.append("")
    lines.append(f"Verdict: {'REGIME SWITCH CONFIRMED ✅' if res['confirmed'] else 'NOT confirmed ❌'} "
                 "(it must hold in BOTH eras)")
    lines.append(f"Rule fixed before the run: {MIN_WITH}+ with-regime trades winning ≥ {rv.WIN_HI:g}% "
                 f"(z ≥ {rv.Z_MIN:g}) with avg R > 0, and {R_GAP:g}R+ better than against-regime "
                 "trades. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="regime_switch_test", description=__doc__.split("\n\n")[0])
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
    if "BTC" not in daily:
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
        print(f"coins replayed: {len(res['coins'])}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
