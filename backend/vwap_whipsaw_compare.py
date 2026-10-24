"""
VWAP whipsaw: "flat VWAP, price crossing = no trade".

The engine scores VWAP on the 1H and 2H readings (rolling 50-candle VWAP):
+-14 on a fresh cross, else +-6 above / below (+-10 when the slope agrees).
In chop price saws across a flat VWAP and every new cross scores 14 again.
A reading is WHIPSAWING when its close crossed the VWAP (each close against
the VWAP of the window ending there) MIN_CROSSES+ times in the last
CROSS_BARS candles. Variants, on that timeframe's reading:

  A  whipsaw: a cross on the latest candle scores 0
  B  whipsaw: VWAP scores nothing at all (cross or above / below)
  B at 4+    the same, only at 4+ crosses

The VWAP section's points come from the reading's score breakdown; the
strengths move by points / 220 x 100 with the v53 caps re-applied. All on top
of live (HL's low-volatility dock, -5). Then the live book: traded coins,
every signal at 69+, v54 exits, $25 a trade, HL caps. Run on both periods
(--end-days-ago 0 and 135); adopt only what wins both.

    python -m vwap_whipsaw_compare --fetch-days 90 [--end-days-ago 135]
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
PERIOD = 50               # indicators.calculate_vwap
CROSS_BARS = 10
MIN_CROSSES = 3
SECTION = "vwap"

VARIANTS = (          # (label, rule, min crosses)
    ("live (low-vol -5)", None, MIN_CROSSES),
    ("A whipsaw (3+ crosses in 10): a new cross scores 0", "cross", MIN_CROSSES),
    ("B whipsaw (3+ crosses in 10): no VWAP points", "all", MIN_CROSSES),
    ("B at 4+ crosses in 10", "all", 4),
)


def _vwap(window: Sequence[Dict]) -> Optional[float]:
    vol = sum(float(c["volume"]) for c in window)
    if vol <= 0:
        return None
    return sum((float(c["high"]) + float(c["low"]) + float(c["close"])) / 3 * float(c["volume"])
               for c in window) / vol


def crosses(bars: Sequence[Dict], n: int = CROSS_BARS, period: int = PERIOD) -> Dict:
    """{"count": closes that crossed the VWAP in the last `n` candles,
    "now": whether the latest one did}. Each close is judged against the
    rolling VWAP of the window ending at it, like calculate_vwap."""
    if len(bars) < n + 2:
        return {"count": 0, "now": False}
    sides = []
    for i in range(len(bars) - n - 1, len(bars)):
        v = _vwap(bars[max(0, i + 1 - period):i + 1])
        sides.append(None if v is None else float(bars[i]["close"]) > v)
    flips = [a is not None and b is not None and a != b for a, b in zip(sides, sides[1:])]
    return {"count": sum(flips), "now": bool(flips and flips[-1])}


def change(points: float, x: Dict, rule: Optional[str], min_crosses: int) -> float:
    """Signed score change (+ bull) for one reading's VWAP points. Pure."""
    if not rule or not points or x.get("count", 0) < min_crosses:
        return 0.0
    if rule == "cross" and not x.get("now"):
        return 0.0
    return -float(points)


def features(market: Dict, c: Dict) -> Dict:
    tfs = market.get(c["symbol"]) or {}
    slot = c["slot_ms"]
    h2 = pbt.closed_slice(tfs.get("2H") or [], "2H", slot, lookback=PERIOD + CROSS_BARS + 2)
    h1 = pbt.closed_slice(tfs.get("1H") or [], "1H", slot, lookback=PERIOD + CROSS_BARS + 2)
    return {"h2": crosses(h2), "h1": crosses(h1),
            "low_vol": ist.vol_state(pj.closed_before(tfs.get("1H") or [], slot, 200)) == "low"}


def strength(c: Dict, f: Dict, rule: Optional[str], min_crosses: int) -> float:
    """The strength HL compares to the floor under a variant. Pure."""
    d2 = change((c.get("score_breakdown") or {}).get(SECTION) or 0, f["h2"], rule, min_crosses)
    d1 = change((c.get("h1_score_breakdown") or {}).get(SECTION) or 0, f["h1"], rule, min_crosses)
    s = rw.reweighted(c, {}, add=d2, h1_add=d1) if (d2 or d1) else float(c.get("strength") or 0)
    if f.get("low_vol"):
        s -= rw.LOW_VOL_DOCK
    return round(s, 2)


def apply(cands: Sequence[Dict], feats: Dict, rule: Optional[str], min_crosses: int) -> List[Dict]:
    out = []
    for c in cands:
        f = feats.get((c["symbol"], c["slot_ms"]))
        if f is None:
            out.append(c)
            continue
        s = strength(c, f, rule, min_crosses)
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
    live = uc.all_floor(apply(cands, feats, None, MIN_CROSSES), universe, floor)
    base = {(c["symbol"], c["slot_ms"]) for c in live}
    rows = []
    for label, rule, mc in VARIANTS:
        recs = uc.all_floor(apply(cands, feats, rule, mc), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), "added": len(keys - base),
                     "removed": len(base - keys), **rr.usd_metrics(b["trades"], span)})
    at = [feats[k]["h2"] for k in base if k in feats]
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base),
            "whipsaw": sum(1 for x in at if x["count"] >= MIN_CROSSES),
            "whipsaw_cross": sum(1 for x in at if x["count"] >= MIN_CROSSES and x["now"]),
            "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No VWAP rule beats live here (${live['pnl_usd']:+.2f}). Check the other "
                "period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["〰️ VWAP whipsaw on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps. {result['at_floor']} signals at {result['floor']:g}+ live: "
             f"{result['whipsaw']} with the 2H whipsawing ({MIN_CROSSES}+ VWAP crosses in "
             f"{CROSS_BARS} candles), {result['whipsaw_cross']} of them on a fresh cross", ""]
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
    ap = argparse.ArgumentParser(prog="vwap_whipsaw_compare",
                                 description=__doc__.split("\n\n")[0])
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
