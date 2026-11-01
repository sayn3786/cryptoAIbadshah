"""
15m AMD (Power of 3): Accumulation -> Manipulation -> Distribution.

Each UTC day, per coin, on closed 15m candles only:

  A  ACCUMULATION  the Asia range: high / low of 00:00-06:00 UTC (all 24
                   candles needed). "Tight" = its width at or under the median
                   of the previous 20 days' Asia ranges (known at 06:00).
  M  MANIPULATION  in the window after it (06:00-12:00 UTC London, or
                   06:00-16:00 London + New York), the first candle that trades
                   beyond one side:
                     wick     it closes back inside -> the sweep is done
                     reclaim  it closes outside, and one of the next 2 candles
                              closes back inside
                   A breakout that does not come back inside = no trade.
  D  DISTRIBUTION  trade the other way at the close of the candle back inside
                   (swept the high -> SHORT, swept the low -> LONG). One trade a
                   coin a day.

Exits: the live fixed exits ($50 margin at 11x, -10% / +15% of margin, max
4h), or the setup's own: stop 0.05% beyond the sweep's extreme, target the
other side of the Asia range (R:R 1+, stop 0.15-3%), $5 risk a trade, max
8h. Fees + slippage as live. Each variant is compared with the MATCHED RANDOM
baseline (same entries and exits, coin-flip direction; scalp_baseline).
PASS = 30+ trades, profit, PF >= 1.2 and above the best 5% of random runs.
Adopt only what passes in all three periods (end_days_ago 0, 65, 135).

    python -m scalp_amd_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence

import scalp_baseline as sb

M15 = sb.M15
HOUR_MS = sb.HOUR_MS
DAY_MS = 24 * HOUR_MS
ASIA_H = 6
LONDON_END_H = 12
NY_END_H = 16
RECLAIM_BARS = 2
TIGHT_DAYS = 20
TIGHT_MIN_DAYS = 10
STOP_BUFFER = 0.0005
MIN_STOP_PCT = 0.0015
MAX_STOP_PCT = 0.03
MIN_RR = 1.0
STRUCT_HOLD_H = 8


def days(bars: Sequence[Dict]) -> Dict[int, List[int]]:
    """{UTC day start ms: [bar indexes]}."""
    out: Dict[int, List[int]] = {}
    for i, b in enumerate(bars):
        out.setdefault(int(b["timestamp"]) // DAY_MS * DAY_MS, []).append(i)
    return out


def asia_range(bars: Sequence[Dict], idx: Sequence[int], day: int) -> Optional[Dict]:
    asia = [i for i in idx if int(bars[i]["timestamp"]) < day + ASIA_H * HOUR_MS]
    if len(asia) != ASIA_H * 4:
        return None
    hi = max(float(bars[i]["high"]) for i in asia)
    lo = min(float(bars[i]["low"]) for i in asia)
    return {"hi": hi, "lo": lo, "width": (hi - lo) / lo if lo else 0.0}


def manipulation(bars: Sequence[Dict], idx: Sequence[int], day: int, rng: Dict, *,
                 end_h: int, reclaim: bool) -> Optional[Dict]:
    """The first sweep of the Asia range in [06:00, end_h): {"i" (the candle
    back inside), "direction", "extreme", "kind"}, or None."""
    hi, lo = rng["hi"], rng["lo"]
    win = [i for i in idx
           if day + ASIA_H * HOUR_MS <= int(bars[i]["timestamp"]) < day + end_h * HOUR_MS]
    for n, i in enumerate(win):
        h, l, c = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i]["close"])
        up, down = h > hi, l < lo
        if not (up or down):
            continue
        if up and down:
            return None                                  # both sides in one candle: no read
        side = "high" if up else "low"
        inside = lo < c < hi
        if inside:
            return {"i": i, "direction": "SHORT" if up else "LONG",
                    "extreme": h if up else l, "kind": "wick"}
        if not reclaim:
            return None
        ext = h if up else l
        for j in win[n + 1:n + 1 + RECLAIM_BARS]:
            hj, lj, cj = float(bars[j]["high"]), float(bars[j]["low"]), float(bars[j]["close"])
            ext = max(ext, hj) if side == "high" else min(ext, lj)
            if lo < cj < hi:
                return {"i": j, "direction": "SHORT" if side == "high" else "LONG",
                        "extreme": ext, "kind": "reclaim"}
        return None                                      # a real breakout: no trade
    return None


def setups(sym: str, bars: Sequence[Dict], *, start_ms: int, end_h: int = LONDON_END_H,
           reclaim: bool = True, tight: bool = False) -> List[Dict]:
    out = []
    by = days(bars)
    widths: List[float] = []
    for day in sorted(by):
        rng = asia_range(bars, by[day], day)
        if rng is None:
            continue
        prior = widths[-TIGHT_DAYS:]
        widths.append(rng["width"])
        if tight:
            if len(prior) < TIGHT_MIN_DAYS:
                continue
            med = sorted(prior)[len(prior) // 2]
            if rng["width"] > med:
                continue
        m = manipulation(bars, by[day], day, rng, end_h=end_h, reclaim=reclaim)
        if m is None:
            continue
        i = m["i"]
        slot = int(bars[i]["timestamp"]) + M15
        if slot < start_ms:
            continue
        entry = float(bars[i]["close"])
        long = m["direction"] == "LONG"
        sl = m["extreme"] * (1 - STOP_BUFFER) if long else m["extreme"] * (1 + STOP_BUFFER)
        target = rng["hi"] if long else rng["lo"]
        out.append({"symbol": sym, "slot_ms": slot, "direction": m["direction"],
                    "entry": entry, "sl": sl, "target": target, "kind": m["kind"],
                    "strength": 0.0, "asia_width": rng["width"]})
    return out


def struct_ok(c: Dict) -> bool:
    e, sl, t = c["entry"], c["sl"], c["target"]
    risk = abs(e - sl)
    if not risk or (sl - e) * (1 if c["direction"] == "SHORT" else -1) <= 0:
        return False
    if (t - e) * (1 if c["direction"] == "LONG" else -1) <= 0:
        return False
    return MIN_STOP_PCT <= risk / e <= MAX_STOP_PCT and abs(t - e) / risk >= MIN_RR


VARIANTS = (   # (label, end hour, reclaim, tight, exits)
    ("Asia → London sweep, live exits", LONDON_END_H, True, False, "live"),
    ("Asia → London sweep, stop past the sweep / target the other side", LONDON_END_H, True,
     False, "struct"),
    ("… tight Asia range only", LONDON_END_H, True, True, "struct"),
    ("… wick sweeps only (no reclaim)", LONDON_END_H, False, False, "struct"),
    ("Asia → London or New York sweep (06-16 UTC), own exits", NY_END_H, True, False,
     "struct"),
)


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            seeds: int = sb.SEEDS) -> Dict:
    span = (end_ms - start_ms) / DAY_MS
    rows = []
    for label, end_h, reclaim, tight, exits in VARIANTS:
        cands = [c for sym, bars in m15.items()
                 for c in setups(sym, bars, start_ms=start_ms, end_h=end_h, reclaim=reclaim,
                                 tight=tight)]
        if exits == "struct":
            cands = [c for c in cands if struct_ok(c)]
            book = lambda c: sb.struct_book(c, m15, max_hold_h=STRUCT_HOLD_H)   # noqa: E731
        else:
            book = lambda c: sb.live_book(c, m15)                                # noqa: E731
        rows.append(sb.row(label, cands, book, span, seeds))
        log(f"  {label}: done")                        # no results in the (public) log
    return {"window": sb.window(start_ms, end_ms), "coins": sorted(m15), "rows": rows}


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🎭 15m AMD (Power of 3): Asia range → sweep → trade the other way",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             f"Live exits: ${sb.rc.MARGIN:g} margin at {sb.rc.LEVERAGE}x, stop "
             f"-{sb.rc.sl.SL_ROE_PCT:g}% / TP +{sb.rc.sl.TP1_ROE_PCT:g}% of margin, max 4h; own exits: "
             f"stop past the sweep, target the other side of Asia, ${sb.RISK_USD:g} risk, max "
             f"{STRUCT_HOLD_H}h; one per coin; costs: entry/stop 6.5 bps, target 1.5 bps",
             "✅ = 30+ trades, profit, PF ≥ 1.2 AND above the best 5% of random-direction runs", ""]
    for r in result["rows"]:
        lines += sb.render_row(r)
    lines.append(sb.verdict(result["rows"]))
    lines.append("Adopt only what passes in all three periods (end_days_ago 0, 65, 135).")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = sb.cli("scalp_amd_compare", __doc__, argv)
    return sb.telegram_main(render_telegram, compare, args)


if __name__ == "__main__":
    sys.exit(main())
