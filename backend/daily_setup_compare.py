"""
The daily setups the combination study found, applied to the live HL book.

combo_study (28 coins, 1250 daily closes, 320 combinations held vs 68 by
chance) found two families that called the next days in both halves:

  🟢 daily momentum   a wide day (1.5x+ the 14-day range) closing in its top
                      quarter, with the engine's 1D call 60+, a bullish FVG,
                      volume or trendline: next 3 days +3.5%, big moves 2x
  🔴 daily breakdown  a bearish daily flag break with Stoch RSI, a liquidity
                      grab, MACD, engulfing or Bollinger bearish: hit 57-66%,
                      next 3 days +1.5-2% its way

The setup is read on each coin's daily closes (the engine on the closed daily
candles, exactly as combo_study) and counts for the 4h publishes in the next
day, or the next 3 days, after that close. Variants move the strength HL
compares to the 69 floor:

  with +5 / +10     a signal the setup's way gains 5 / 10
  against -10       a signal against it loses 10
  both              +10 with, -10 against
  breakdown only    next day, +5 for SHORTs after a 🔴 breakdown (the
                    reliable-hit family); momentum setups ignored
  momentum only     next day, +5 for LONGs after a 🟢 momentum day

All on top of live (HL's low-volatility dock, -5). Then the live book: traded
coins, every signal at 69+, v54 exits, $25 a trade, HL caps. Run on both
periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m daily_setup_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import combo_study as cs
import entry_exit_compare as ee
import indicator_study as ist
import portfolio_backtest as pbt
import prejump_study as pj
import read_study as rs
import reweight_compare as rw
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
DAY_MS = rs.DAY_MS
FLOOR = 69.0
BULL_CONFIRM = ("engine", "fvg", "volume", "trendlines")
BEAR_CONFIRM = ("stoch_rsi", "liquidity_grab", "macd", "engulfing", "bollinger")

VARIANTS = (          # (label, days the setup counts, with, against, only this setup)
    ("live (low-vol -5)", 1, 0.0, 0.0, None),
    ("next day: with +5", 1, 5.0, 0.0, None),
    ("next day: with +10", 1, 10.0, 0.0, None),
    ("next day: against -10", 1, 0.0, -10.0, None),
    ("next day: with +10, against -10", 1, 10.0, -10.0, None),
    ("3 days: with +10", 3, 10.0, 0.0, None),
    ("3 days: against -10", 3, 0.0, -10.0, None),
    ("3 days: with +10, against -10", 3, 10.0, -10.0, None),
    ("next day: 🔴 breakdown only, SHORTs +5", 1, 5.0, 0.0, -1),
    ("next day: 🟢 momentum only, LONGs +5", 1, 5.0, 0.0, 1),
)


def setup(f: Dict[str, int]) -> int:
    """+1 daily momentum, -1 daily breakdown, 0 neither (features from
    combo_study for one daily close)."""
    if f.get("c_range") == 1 and any(f.get(k) == 1 for k in BULL_CONFIRM):
        return 1
    if f.get("flags") == -1 and any(f.get(k) == -1 for k in BEAR_CONFIRM):
        return -1
    return 0


def coin_setups(daily: Sequence[Dict], score_fn, *, symbol: str, since_ms: int) -> List[tuple]:
    """[(close ms, +1 / -1)] for the daily closes from `since_ms` on with a setup."""
    out = []
    for i in range(cs.WINDOW, len(daily)):
        close_ms = int(daily[i]["timestamp"]) + DAY_MS
        if close_ms < since_ms:
            continue
        win = list(daily[i + 1 - cs.WINDOW:i + 1])
        try:
            sig = score_fn(win, symbol)
        except Exception:                                 # noqa: BLE001
            sig = {}
        d = setup({**cs.engine_features(sig or {}), **cs.candle_features(win)})
        if d:
            out.append((close_ms, d))
    return out


def active(setups: Sequence[tuple], at_ms: int, days: int) -> int:
    """The latest setup whose close is at or before `at_ms` and within `days`."""
    closes = [s[0] for s in setups]
    i = bisect.bisect_right(closes, at_ms) - 1
    if i < 0 or at_ms - setups[i][0] >= days * DAY_MS:
        return 0
    return setups[i][1]


def strength(c: Dict, d: int, low_vol: bool, with_: float, against: float) -> float:
    """The strength HL compares to the floor under a variant. Pure."""
    s = float(c.get("strength") or 0)
    if d:
        same = (d == 1) == (c["direction"] == "LONG")
        s += with_ if same else against
    if low_vol:
        s -= rw.LOW_VOL_DOCK
    return round(s, 2)


def apply(cands: Sequence[Dict], setups: Dict[str, List[tuple]], vol: Dict,
          days: int, with_: float, against: float, only: Optional[int] = None) -> List[Dict]:
    out = []
    for c in cands:
        d = active(setups.get(c["symbol"]) or [], c["slot_ms"], days)
        if only is not None and d != only:
            d = 0
        s = strength(c, d, vol.get((c["symbol"], c["slot_ms"])) == "low", with_, against)
        delta = s - float(c.get("strength") or 0)
        out.append({**c, "strength": s,
                    "avg_tf_strength": (c.get("avg_tf_strength") or c.get("strength") or 0) + delta}
                   if delta else c)
    return out


def compare(market: Dict, setups: Dict[str, List[tuple]], *, core: Sequence[str],
            days: Optional[float] = None, correlations=None, floor: float = FLOOR,
            warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    vol = {(c["symbol"], c["slot_ms"]): ist.vol_state(pj.closed_before(
        (market.get(c["symbol"]) or {}).get("1H") or [], c["slot_ms"], 200))
        for c in cands if (c.get("strength") or 0) >= floor - 25}
    live = uc.all_floor(apply(cands, setups, vol, 1, 0.0, 0.0), universe, floor)
    base = {(c["symbol"], c["slot_ms"]) for c in live}
    rows = []
    for label, d_days, with_, against, only in VARIANTS:
        recs = uc.all_floor(apply(cands, setups, vol, d_days, with_, against, only),
                            universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), "added": len(keys - base),
                     "removed": len(base - keys), **rr.usd_metrics(b["trades"], span)})
    in_win = [(s, d) for sym, ss in setups.items() if sym in universe
              for (s, d) in ss if start - 3 * DAY_MS <= s < end]
    touched = {"with": 0, "against": 0}
    for c in live:
        d = active(setups.get(c["symbol"]) or [], c["slot_ms"], 3)
        if d:
            touched["with" if (d == 1) == (c["direction"] == "LONG") else "against"] += 1
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base),
            "setups": {"bull": sum(1 for _, d in in_win if d == 1),
                       "bear": sum(1 for _, d in in_win if d == -1)},
            "touched": touched, "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No daily-setup rule beats live here (${live['pnl_usd']:+.2f}). Check the "
                "other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w, st, t = result["window"], result["setups"], result["touched"]
    lines = ["🧩 Daily setups on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"Setups on the coins' daily closes: {st['bull']} 🟢 momentum (wide day closing at "
             f"the high + confirmation), {st['bear']} 🔴 breakdown (daily flag break + "
             f"confirmation). $25 a trade, HL caps",
             f"{result['at_floor']} signals at {result['floor']:g}+ live: {t['with']} within 3 days "
             f"of a setup their way, {t['against']} against one", ""]
    for r in result["rows"]:
        moved = [f"+{r['added']}" if r["added"] else "", f"−{r['removed']}" if r["removed"] else ""]
        moved = " / ".join(m for m in moved if m)
        head = f"• {r['label']}" + (f" ({moved} signals)" if moved else "")
        if not r.get("trades"):
            lines += [f"{head}: no trades", ""]
            continue
        lines += [head,
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="daily_setup_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR,
                    help="unused; the HL floor is fixed at 69 like today")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = None
    if args.end_days_ago:
        end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
            // HOUR_MS * HOUR_MS
    pairs = {s: appmod.SYMBOLS[s] for s in core}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history(pairs, args.fetch_days, end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    print("downloading daily history for the setups...", file=sys.stderr)
    day_end = (end_ms or int(time.time() * 1000)) // DAY_MS * DAY_MS
    daily = rs.fetch_daily(pairs, cs.WINDOW + int(args.fetch_days) + 30, end_ms=day_end, log=log)
    since = int(market["BTC"]["1H"][0]["timestamp"]) - 3 * DAY_MS
    setups = {s: coin_setups(bars, cs.engine_score, symbol=s, since_ms=since)
              for s, bars in daily.items()}
    res = compare(market, setups, core=core, correlations=appmod._BTC_CORR)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
