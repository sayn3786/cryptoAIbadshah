"""
Re-weighting the strength score: the changes the indicator study backed in
BOTH periods, on the live HL book.

The indicator study (every coin screened 40+, Jan-May and Jun-Oct) found
most sections flip between periods; only these held in both:

  A  engulfing earns its points (+0.29 / +0.47)          -> points x1.5
  B  liquidity grab (-0.12 / -0.30) and RSI divergence
     (-0.18 / -0.22) pay losers                          -> points x0.5
  C  CVD (candle-estimated) pays losers (-0.75 / -0.26)  -> points x0.5
  D  low volatility loses (-0.52% / -0.26% a trade)      -> -5 strength when
     1H ATR is under 0.8x its 120h median

Each candidate is re-scored from its per-section breakdown: the 1H and 2H
scores move by the changed points, the 2H strength (after the BTC
adjustment) and both timeframes' strengths move by points / 220 x 100, and
the v53 caps are re-applied to the new numbers. D docks the final strength.
Then the live book: traded coins, every signal at 69+, v54 exits, $25 a
trade, HL caps. (The v55 bottom-read boost is left out so the weights are
measured alone; a coin the engine left NEUTRAL stays out — a re-weight that
would have created a new signal is not counted.)

Run on both periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m reweight_compare --fetch-days 90 [--end-days-ago 135]
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
import rec_policy
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
MAX_SCORE = 220.0
LOW_VOL_DOCK = 5.0

A = {"engulfing": 1.5}
B = {"liquidity_grab": 0.5, "rsi_divergence": 0.5}
C = {"cvd": 0.5}
VARIANTS = (
    ("live (today's weights)", {}, False),
    ("A engulfing x1.5", A, False),
    ("B liq grab + RSI div x0.5", B, False),
    ("C CVD x0.5", C, False),
    ("D low-vol -5", {}, True),
    ("A+B+C", {**A, **B, **C}, False),
    ("A+B+C+D", {**A, **B, **C}, True),
)


def score_delta(breakdown: Dict[str, float], mults: Dict[str, float]) -> float:
    """Signed score change (+ bull) from scaling sections' points."""
    return sum((m - 1.0) * (breakdown.get(k) or 0) for k, m in mults.items())


def _toward(breakdown: Dict[str, float], delta: float) -> float:
    """A score change as a strength change for the reading's own direction:
    strength is |score|, so a bull delta raises a bull reading and lowers a
    bear one."""
    score = sum(breakdown.values())
    if not score:
        return abs(delta) / MAX_SCORE * 100
    return (1 if score > 0 else -1) * delta / MAX_SCORE * 100


def reweighted(c: Dict, mults: Dict[str, float], *, low_vol: bool = False) -> float:
    """The strength HL compares to the floor, with `mults` applied and the v53
    caps re-applied; docked LOW_VOL_DOCK when `low_vol`. Pure."""
    s = float(c.get("strength") or 0)
    if mults:
        d2 = _toward(c.get("score_breakdown") or {}, score_delta(c.get("score_breakdown") or {}, mults))
        d1 = _toward(c.get("h1_score_breakdown") or {},
                     score_delta(c.get("h1_score_breakdown") or {}, mults))
        raw = c.get("strength_before_calibration")
        raw = float(raw if raw is not None else s)
        h1, h2 = c.get("h1_strength"), c.get("h2_strength")
        s = rec_policy.apply_tier_calibration(
            max(0.0, min(100.0, raw + d2)),
            h1_strength=None if h1 is None else h1 + d1,
            h2_strength=None if h2 is None else h2 + d2,
            chased=bool(c.get("chased")))["strength"]
    if low_vol:
        s -= LOW_VOL_DOCK
    return round(s, 2)


def apply(cands: Sequence[Dict], mults: Dict[str, float], vol: Dict, *,
          low_vol: bool) -> List[Dict]:
    """Candidates with strength (and the ranking's 1H/2H average) moved."""
    out = []
    for c in cands:
        s = reweighted(c, mults, low_vol=low_vol and vol.get((c["symbol"], c["slot_ms"])) == "low")
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
    vol = {(c["symbol"], c["slot_ms"]): ist.vol_state(pj.closed_before(
        (market.get(c["symbol"]) or {}).get("1H") or [], c["slot_ms"], 200))
        for c in cands if (c.get("strength") or 0) >= floor - 25}
    base = {(c["symbol"], c["slot_ms"]) for c in uc.all_floor(cands, universe, floor)}
    rows = []
    for label, mults, low_vol in VARIANTS:
        recs = uc.all_floor(apply(cands, mults, vol, low_vol=low_vol), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), "added": len(keys - base),
                     "removed": len(base - keys), **rr.usd_metrics(b["trades"], span)})
    low = sum(1 for (k, v) in vol.items() if v == "low" and k in base)
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base), "low_vol_at_floor": low, "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No re-weight beats live here (${live['pnl_usd']:+.2f}). Check the other "
                "period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["⚖️ Re-weighting the score on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps; {result['at_floor']} signals at {result['floor']:g}+ today, "
             f"{result['low_vol_at_floor']} of them in low volatility", ""]
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
    ap = argparse.ArgumentParser(prog="reweight_compare", description=__doc__.split("\n\n")[0])
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
