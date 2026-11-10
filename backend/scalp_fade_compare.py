"""
FADE the live 15m scalp score: a pre-registered test (written Nov 9 2026,
before any run of it).

Why: on the same entries the live score's direction did WORSE than a coin
flip (scalp_score_vs_random: score >= 100 better than only 22% and 0% of
random runs in Dec-Feb and Apr-Jun, and the higher the score the worse),
while the reversed direction did better (72% and 100%). Reversed still LOST
money on the live fixed exits (PF 0.80 / 0.90). So this asks whether fading
the high-score signal, with exits scaled to each coin's volatility, makes
money AFTER costs - on periods nobody has looked at.

The rule, fixed before running (nothing below is tuned afterwards):

  signal   every candle where the live score is >= 100 (live floor),
           traded in the OPPOSITE direction, entry at the signal close
  exits    stop 1.5 x ATR(14, 15m) from the entry, target 1.5 x ATR (1:1),
           max hold 4h; a setup without an ATR is skipped
  size     $5 at risk to the stop (notional = $5 / stop distance), costs as
           live (entry / stop 6.5 bps, target 1.5 bps); one position per coin
  PASS     on BOTH never-read periods (end_days_ago 550 and 620): 30+ trades,
           net profit after costs, PF >= 1.15, AND above the best 5% of the
           same entries with random directions (50 runs)

Reported for context only (they decide nothing): following the score with
the same volatility exits (do the exits alone help?), fading with the live
fixed exits, and fading score >= 110.

    python -m scalp_fade_compare --fetch-days 60 --end-days-ago 550
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import scalp_baseline as sb
import scalp_portfolio_compare as spc
import scalp_score_vs_random as svr

ATR_STOP = 1.5
ATR_TARGET = 1.5
HOLD_H = 4.0
FLOOR = 100.0
MIN_TRADES = 30
MIN_PF = 1.15
PERIODS = (550, 620)
PRIMARY = "FADE score ≥ 100, volatility exits (the test)"


def vol_exits(c: Dict, direction: str) -> Optional[Dict]:
    """`c` traded `direction` with a stop / target 1.5 ATR each side of the
    entry; None without an ATR."""
    atr = (c.get("quality") or {}).get("atr")
    try:
        atr = float(atr)
    except (TypeError, ValueError):
        return None
    if atr <= 0:
        return None
    e = float(c["entry"])
    sgn = 1 if direction == "LONG" else -1
    return {**c, "direction": direction, "sl": e - sgn * ATR_STOP * atr,
            "target": e + sgn * ATR_TARGET * atr}


def faded(c: Dict) -> str:
    return "SHORT" if c["direction"] == "LONG" else "LONG"


def passes(r: Dict) -> bool:
    return ((r.get("trades") or 0) >= MIN_TRADES and (r.get("pnl_usd") or 0) > 0
            and (r.get("pf_usd") or 0) >= MIN_PF and bool(r.get("beats_random")))


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            read_kw: Optional[Dict] = None, seeds: int = svr.SEEDS,
            vol_book: Optional[Callable] = None, live_book: Optional[Callable] = None) -> Dict:
    data = spc.candidates(m15, start_ms=start_ms, log=log, read_kw=read_kw)
    cands = [c for c in data["cands"] if start_ms <= c["slot_ms"] <= end_ms
             and c["strength"] >= FLOOR]
    span = (end_ms - start_ms) / (24 * sb.HOUR_MS)
    vol_book = vol_book or (lambda cs: sb.struct_book(cs, m15, max_hold_h=HOLD_H))
    live_book = live_book or (lambda cs: sb.live_book(cs, m15))
    fade_vol = [x for x in (vol_exits(c, faded(c)) for c in cands) if x]
    follow_vol = [x for x in (vol_exits(c, c["direction"]) for c in cands) if x]
    variants = [
        (PRIMARY, fade_vol, vol_book),
        ("follow score ≥ 100, volatility exits (context)", follow_vol, vol_book),
        ("FADE score ≥ 100, live fixed exits (context)", [svr.reverse(c) for c in cands], live_book),
        ("FADE score ≥ 110, volatility exits (context)",
         [x for x in fade_vol if x["strength"] >= 110], vol_book),
    ]
    rows = []
    for label, cs, book in variants:
        r = svr.row(label, cs, book, span, seeds)
        r["passes"] = passes(r) if label == PRIMARY else None
        rows.append(r)
        log(f"  {label}: done")                          # no results in the (public) log
    return {"window": sb.window(start_ms, end_ms), "coins": sorted(m15), "rows": rows,
            "no_atr": len(cands) - len(fade_vol)}


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🔄 15m scalp: FADE the live score (pre-registered test)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             f"Test: trade AGAINST score ≥ {FLOOR:g}; stop and target {ATR_STOP:g} × ATR each, max "
             f"{HOLD_H:g}h; $5 risk a trade; costs as live",
             f"Pass (on BOTH end_days_ago {PERIODS[0]} and {PERIODS[1]}): {MIN_TRADES}+ trades, profit "
             f"after costs, PF ≥ {MIN_PF:g}, and above the best 5% of random directions", ""]
    for r in result["rows"]:
        lines += svr.render_row(r)
    prim = next(r for r in result["rows"] if r["label"] == PRIMARY)
    lines.append(f"Test on this period: {'PASS ✅' if prim['passes'] else 'FAIL ❌'}"
                 + (f" ({result['no_atr']} setups skipped: no ATR)" if result["no_atr"] else ""))
    lines.append("It counts only if it passes on BOTH periods. The context rows decide nothing.")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = sb.cli("scalp_fade_compare", __doc__, argv)
    return sb.telegram_main(render_telegram, compare, args)


if __name__ == "__main__":
    sys.exit(main())
