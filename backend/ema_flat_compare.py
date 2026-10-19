"""
The 9/21 EMA rules: "flat lines = stay out", and a 9/21 cross instead of 7/21.

The engine already scores the short EMA cross (EMA 7 over / under 21: +14 on
a fresh cross, +6 while it holds) and keeps entries off chased moves. What it
does not do is stay out when the short EMAs are flat — the chop where
crossovers whipsaw. On the 2H chart at the publish, the lines are FLAT when

  |EMA 9 - EMA 21| < SPREAD_PCT of price, and
  EMA 21 moved less than SLOPE_PCT over the last SLOPE_BARS candles.

Variants, all on top of live (HL's low-volatility dock, -5 under 0.8x ATR,
already applied — flat lines and a quiet market overlap, so this measures
what flatness adds):

  flat -5 / -10 / skip     a signal on flat 2H lines loses 5 / 10 / is skipped
  9/21 cross               the short cross scored on EMA 9 instead of 7 on the
                           1H and 2H (same +14 fresh / +6 holding)

Then the live book: traded coins, every signal at 69+, v54 exits, $25 a
trade, HL caps. Run on both periods (--end-days-ago 0 and 135); adopt only
what wins both.

    python -m ema_flat_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import indicator_study as ist
import portfolio_backtest as pbt
import prejump_study as pj
import reweight_compare as rw
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
SKIP = -1000.0
SPREAD_PCT = 0.3
SLOPE_PCT = 0.3
SLOPE_BARS = 5
EMA_BARS = 120            # closed candles fed to the EMAs
FRESH_PTS, HOLD_PTS = 14.0, 6.0

VARIANTS = (          # (label, flat dock, 9/21 cross)
    ("live (low-vol -5, EMA 7/21 cross)", 0.0, False),
    ("flat 9/21 lines -5", 5.0, False),
    ("flat 9/21 lines -10", 10.0, False),
    ("flat 9/21 lines: skip", -SKIP, False),
    ("EMA 9/21 cross instead of 7/21", 0.0, True),
)


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    """EMA at every bar, SMA-seeded like indicators.calculate_ema_trend."""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out[period - 1] = e
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def is_flat(bars: Sequence[Dict]) -> Optional[bool]:
    """Are the EMA 9 / 21 lines flat and tangled on these closed candles?"""
    closes = [float(c["close"]) for c in bars]
    if len(closes) < 21 + SLOPE_BARS:
        return None
    e9, e21 = ema(closes, 9)[-1], ema(closes, 21)
    now, then = e21[-1], e21[-1 - SLOPE_BARS]
    if not now or not then or not closes[-1]:
        return None
    spread = abs(e9 - now) / closes[-1] * 100
    slope = abs(now - then) / then * 100
    return spread < SPREAD_PCT and slope < SLOPE_PCT


def cross_points(closes: Sequence[float], fast: int, slow: int = 21) -> float:
    """The engine's short-cross score (+ bull): +-14 on a fresh cross, else
    +-6 by which side the fast EMA is on; 0 without enough history."""
    if len(closes) < slow + 1:
        return 0.0
    f, s = ema(closes, fast), ema(closes, slow)
    if None in (f[-1], s[-1], f[-2], s[-2]):
        return 0.0
    if f[-2] <= s[-2] and f[-1] > s[-1]:
        return FRESH_PTS
    if f[-2] >= s[-2] and f[-1] < s[-1]:
        return -FRESH_PTS
    return HOLD_PTS if f[-1] > s[-1] else -HOLD_PTS


def cross_change(bars: Sequence[Dict]) -> float:
    """Score change (+ bull) from scoring the cross on EMA 9 instead of 7."""
    closes = [float(c["close"]) for c in bars]
    return cross_points(closes, 9) - cross_points(closes, 7)


def features(market: Dict, c: Dict) -> Dict:
    tfs = market.get(c["symbol"]) or {}
    slot = c["slot_ms"]
    h2 = pbt.closed_slice(tfs.get("2H") or [], "2H", slot, lookback=EMA_BARS)
    h1 = pbt.closed_slice(tfs.get("1H") or [], "1H", slot, lookback=EMA_BARS)
    return {"flat": is_flat(h2), "low_vol": ist.vol_state(pj.closed_before(
                tfs.get("1H") or [], slot, 200)) == "low",
            "add2": cross_change(h2), "add1": cross_change(h1)}


def strength(c: Dict, f: Dict, flat_dock: float, cross9: bool) -> float:
    """The strength HL compares to the floor under a variant. Pure."""
    s = (rw.reweighted(c, {}, add=f.get("add2", 0.0), h1_add=f.get("add1", 0.0))
         if cross9 and (f.get("add2") or f.get("add1")) else float(c.get("strength") or 0))
    if f.get("low_vol"):
        s -= rw.LOW_VOL_DOCK
    if flat_dock and f.get("flat"):
        s -= flat_dock
    return round(s, 2)


def apply(cands: Sequence[Dict], feats: Dict, flat_dock: float, cross9: bool) -> List[Dict]:
    out = []
    for c in cands:
        f = feats.get((c["symbol"], c["slot_ms"]))
        if f is None:
            out.append(c)
            continue
        s = strength(c, f, flat_dock, cross9)
        delta = s - float(c.get("strength") or 0)
        out.append({**c, "strength": s,
                    "avg_tf_strength": (c.get("avg_tf_strength") or c.get("strength") or 0) + delta}
                   if delta else c)
    return out


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    feats = {(c["symbol"], c["slot_ms"]): features(market, c)
             for c in cands if (c.get("strength") or 0) >= floor - 25}
    live = apply(cands, feats, 0.0, False)
    base = {(c["symbol"], c["slot_ms"]) for c in uc.all_floor(live, universe, floor)}
    rows = []
    for label, flat_dock, cross9 in VARIANTS:
        recs = uc.all_floor(apply(cands, feats, flat_dock, cross9), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), "added": len(keys - base),
                     "removed": len(base - keys), **rr.usd_metrics(b["trades"], span)})
    at = [feats[k] for k in base if k in feats]
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base),
            "flat": sum(1 for f in at if f["flat"]),
            "flat_low_vol": sum(1 for f in at if f["flat"] and f["low_vol"]),
            "cross_differs": sum(1 for f in at if f["add2"] or f["add1"]), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No EMA rule beats live here (${live['pnl_usd']:+.2f}). Check the other "
                "period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["📏 9/21 EMA rules on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"Flat = 2H EMA 9/21 within {SPREAD_PCT:g}% and EMA 21 moved under {SLOPE_PCT:g}% "
             f"in {SLOPE_BARS} candles. $25 a trade, HL caps",
             f"{result['at_floor']} signals at {result['floor']:g}+ live: {result['flat']} on flat "
             f"lines ({result['flat_low_vol']} of them also low-vol docked); the 9/21 cross scores "
             f"differently on {result['cross_differs']}", ""]
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
    ap = argparse.ArgumentParser(prog="ema_flat_compare", description=__doc__.split("\n\n")[0])
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
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, core=core, correlations=appmod._BTC_CORR)
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
