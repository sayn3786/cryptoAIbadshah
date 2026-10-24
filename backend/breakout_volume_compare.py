"""
Fake breakouts: does volume tell a real break from a trap?

The engine already waits for a CLOSE beyond the level (flags, reversal
patterns, triangles / wedges, BOS) and drops a break that is given back. It
grades every pattern breakout's volume (strong >= 1.5x the 20-candle average,
weak < 1x) but scores the break the same either way, and a liquidity grab (a
wick through a swing, close back inside — "trade the trap") ignores volume
altogether. The textbook says a real break comes on BIG volume and a trap's
rejection candle on sellers / buyers stepping in. Variants:

  1  breakout volume   pattern-breakout points: weak volume 0, normal as
                       today, strong x1.25
  2  grab volume       a liquidity grab counts only when its candle traded
                       above the 20-candle average (no volume data: as today)
  1+2

Points move on the 1H and 2H readings (generate_signal reports them by volume
grade), the strengths by points / 220 x 100 with the v53 caps re-applied
(reweight_compare.reweighted; group caps are not re-run). All on top of live:
HL's low-volatility dock (-5) applies throughout. Then the live book: traded
coins, every signal at 69+, v54 exits, $25 a trade, HL caps. Run on both
periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m breakout_volume_compare --fetch-days 90 [--end-days-ago 135]
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
STRONG_MULT = 1.25
GRAB_MIN_RATIO = 1.0

VARIANTS = (          # (label, breakout volume rule, grab volume rule)
    ("live (low-vol -5)", False, False),
    ("1 breakout volume: weak 0, strong x1.25", True, False),
    ("2 liquidity grab only on above-average volume", False, True),
    ("1+2", True, True),
)


def breakout_change(bvol: Dict[str, float]) -> float:
    """Score change (+ bull) from weighting pattern breakouts by volume."""
    return -(bvol.get("weak") or 0) + (STRONG_MULT - 1) * (bvol.get("strong") or 0)


def grab_change(grab: Dict) -> float:
    """Score change (+ bull) from requiring volume on a liquidity grab."""
    ratio = grab.get("volume_ratio")
    if not grab.get("points") or ratio is None or ratio > GRAB_MIN_RATIO:
        return 0.0
    return -float(grab["points"])


def changes(c: Dict, bo: bool, grab: bool) -> tuple:
    """(2H, 1H) signed score changes under a variant."""
    d2 = (breakout_change(c.get("h2_bvol") or {}) if bo else 0.0) + \
         (grab_change(c.get("h2_grab") or {}) if grab else 0.0)
    d1 = (breakout_change(c.get("h1_bvol") or {}) if bo else 0.0) + \
         (grab_change(c.get("h1_grab") or {}) if grab else 0.0)
    return d2, d1


def strength(c: Dict, low_vol: bool, bo: bool, grab: bool) -> float:
    """The strength HL compares to the floor under a variant. Pure."""
    d2, d1 = changes(c, bo, grab)
    s = rw.reweighted(c, {}, add=d2, h1_add=d1) if (d2 or d1) else float(c.get("strength") or 0)
    if low_vol:
        s -= rw.LOW_VOL_DOCK
    return round(s, 2)


def apply(cands: Sequence[Dict], vol: Dict, bo: bool, grab: bool) -> List[Dict]:
    out = []
    for c in cands:
        s = strength(c, vol.get((c["symbol"], c["slot_ms"])) == "low", bo, grab)
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
    live = uc.all_floor(apply(cands, vol, False, False), universe, floor)
    base = {(c["symbol"], c["slot_ms"]) for c in live}
    rows = []
    for label, bo, grab in VARIANTS:
        recs = uc.all_floor(apply(cands, vol, bo, grab), universe, floor)
        keys = {(c["symbol"], c["slot_ms"]) for c in recs}
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), "added": len(keys - base),
                     "removed": len(base - keys), **rr.usd_metrics(b["trades"], span)})
    grades = {"strong": 0, "normal": 0, "weak": 0}
    grabs = {"loud": 0, "quiet": 0}
    for c in live:
        for g in grades:
            if (c.get("h2_bvol") or {}).get(g):
                grades[g] += 1
        gr = c.get("h2_grab") or {}
        if gr.get("points"):
            grabs["quiet" if grab_change(gr) else "loud"] += 1
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "at_floor": len(base), "breakouts": grades, "grabs": grabs,
            "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No volume rule beats live here (${live['pnl_usd']:+.2f}). Check the other "
                "period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def render_telegram(result: Dict) -> str:
    w, bo, gr = result["window"], result["breakouts"], result["grabs"]
    lines = ["📢 Breakout & trap volume on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps. {result['at_floor']} signals at {result['floor']:g}+ live; "
             f"2H pattern breakouts on strong / normal / weak volume: {bo['strong']} / "
             f"{bo['normal']} / {bo['weak']}; 2H liquidity grabs on above / below-average "
             f"volume: {gr['loud']} / {gr['quiet']}", ""]
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
    ap = argparse.ArgumentParser(prog="breakout_volume_compare",
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
