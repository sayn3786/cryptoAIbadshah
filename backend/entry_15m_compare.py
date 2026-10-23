"""
15m entries for the live HL signals: wait for a better price instead of
buying at market?

15m scalping lost in every variant (scalp_compare): on 15m the engine is a coin
flip and fees eat it. 15m candles can still TIME an entry. The signals stay
exactly the live ones (every 69+ candidate at a 4h publish, low-vol dock);
only HL's entry changes. Today: a market order at the next open. Variants, all
judged on 15m candles (the live entry too, so every row is measured the same
way):

  pullback 0.25R / 0.5R   a limit order 0.25 / 0.5 x the risk (open → stop)
                          better than the open; maker fee on the fill
  EMA 21 (15m)            a limit at the coin's 15m EMA 21 when it is on the
                          pullback side, else market
  15m confirmation        wait for the first 15m candle that closes the
                          trade's way, enter at market at the next open

each waiting 1h or 2h, then either giving up ("else skip") or entering at
market ("else market"). Exits stay v54 (stop + 1 ATR, 50% TP1, rest TP2, stop
to entry at 1R). $25 a trade, HL caps. Run on both periods (--end-days-ago 0
and 135); adopt only what wins both.

    python -m entry_15m_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import combined_compare as cm
import daily_setup_compare as ds
import entry_exit_compare as ee
import indicator_study as ist
import portfolio_backtest as pbt
import prejump_study as pj
import rr_compare as rr
import scalp_compare as sc
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
M15 = sc.M15
FLOOR = 69.0
LIMIT_FILL = {**cm.LIMIT_TPS, "entry": cm.MAKER_BPS}

VARIANTS = (          # (label, kind, R / None, wait hours, else market)
    ("live: market at the next open", "market", None, 0, True),
    ("pullback 0.25R, wait 1h, else skip", "pullback", 0.25, 1, False),
    ("pullback 0.25R, wait 2h, else market", "pullback", 0.25, 2, True),
    ("pullback 0.5R, wait 2h, else skip", "pullback", 0.5, 2, False),
    ("pullback 0.5R, wait 2h, else market", "pullback", 0.5, 2, True),
    ("15m EMA 21, wait 2h, else skip", "ema", None, 2, False),
    ("15m EMA 21, wait 2h, else market", "ema", None, 2, True),
    ("15m candle closes its way, wait 1h, else skip", "confirm", None, 1, False),
)


def _from(bars: Sequence[Dict], at_ms: int) -> int:
    ts = [int(b["timestamp"]) for b in bars]
    return bisect.bisect_left(ts, at_ms)


def _at(bars: Sequence[Dict], i: int, price: float) -> List[Dict]:
    """The candles from i on, the first one's open set to the fill price."""
    first = dict(bars[i])
    first["open"] = price
    first["high"] = max(float(first["high"]), price)
    first["low"] = min(float(first["low"]), price)
    return [first] + list(bars[i + 1:])


def ema21(bars: Sequence[Dict]) -> Optional[float]:
    return _ema([float(b["close"]) for b in bars], 21)


def _ema(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def entry(r: Dict, bars: Sequence[Dict], kind: str, r_mult: Optional[float],
          wait_h: int, else_market: bool) -> Optional[tuple]:
    """(candles from the fill, costs) for one trade, or None when it never
    filled. `r` carries the signal's direction, (widened) stop and slot."""
    i = _from(bars, r["slot_ms"])
    if i >= len(bars):
        return None
    long = r["direction"] == "LONG"
    sgn = 1 if long else -1
    open0 = float(bars[i]["open"])
    end = r["slot_ms"] + wait_h * HOUR_MS
    if kind == "market":
        return list(bars[i:]), cm.LIMIT_TPS
    limit = None
    if kind == "pullback":
        risk = abs(open0 - float(r["sl"]))
        limit = open0 - sgn * r_mult * risk
    elif kind == "ema":
        e = ema21(bars[max(0, i - 120):i])
        if e is not None and (e - open0) * sgn < 0 and (e - float(r["sl"])) * sgn > 0:
            limit = e
        else:
            return list(bars[i:]), cm.LIMIT_TPS            # not on the pullback side
    j = i
    while j < len(bars) and int(bars[j]["timestamp"]) < end:
        b = bars[j]
        if kind == "confirm":
            if (float(b["close"]) - float(b["open"])) * sgn > 0 and j + 1 < len(bars):
                return list(bars[j + 1:]), cm.LIMIT_TPS
        else:
            touched = float(b["low"]) <= limit if long else float(b["high"]) >= limit
            if touched:
                o = float(b["open"])
                px = min(o, limit) if long else max(o, limit)   # a gap fills at the open
                return _at(bars, j, px), LIMIT_FILL
        j += 1
    if else_market and j < len(bars):
        return list(bars[j:]), cm.LIMIT_TPS
    return None


def compare(market: Dict, m15: Dict[str, List[Dict]], *, core: Sequence[str],
            days: Optional[float] = None, correlations=None, floor: float = FLOOR,
            warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market and s in m15]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    vol = {(c["symbol"], c["slot_ms"]): ist.vol_state(pj.closed_before(
        (market.get(c["symbol"]) or {}).get("1H") or [], c["slot_ms"], 200))
        for c in cands if (c.get("strength") or 0) >= floor - 25}
    recs = uc.all_floor(ds.apply(cands, {}, vol, 1, 0.0, 0.0), universe, floor)
    rows = []
    for label, kind, rm, wait_h, else_mkt in VARIANTS:
        fn: Callable = (lambda r, k=kind, m=rm, w=wait_h, e=else_mkt:
                        entry(r, m15.get(r["symbol"]) or [], k, m, w, e))
        b = rr.book(recs, market, floor=floor, entry_for=fn)
        rows.append({"label": label, "missed": b["counts"]["missed"],
                     **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "coins": len(universe), "signals": len(recs), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No 15m entry beats the market entry here (${live['pnl_usd']:+.2f}). Check "
                "the other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🎯 15m entries for the live HL signals (v54 exits, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {result['coins']} coins · "
             f"{result['signals']} signals at {result['floor']:g}+",
             "All rows on 15m candles. Limit fills pay the maker fee. $25 a trade, HL caps, "
             "low-vol dock", ""]
    for r in result["rows"]:
        miss = f" ({r['missed']} never filled)" if r.get("missed") else ""
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades{miss}", ""]
            continue
        lines += [f"• {r['label']}{miss}",
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="entry_15m_compare", description=__doc__.split("\n\n")[0])
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
    print("downloading 15m history for the entries...", file=sys.stderr)
    m15_end = end_ms or int(time.time() * 1000) // HOUR_MS * HOUR_MS
    m15 = sc.fetch_15m(pairs, args.fetch_days, end_ms=m15_end, log=log)
    res = compare(market, m15, core=core, correlations=appmod._BTC_CORR)
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
