"""
The 3-step ICT scalp setup on 15m: liquidity sweep -> fair value gap -> entry
(shared by the live scalp book, scalp_live, and its backtest,
sweep_fvg_compare). Pure; reads CLOSED candles only, and a swing counts only
once SWING candles after it have closed.

  1  SWEEP   a candle's wick takes out the latest confirmed swing low and the
             candle closes back above it
  2  FVG     within FVG_BARS candles, a bullish fair value gap forms above the
             wick (candle k's low above candle k-2's high)
  3  ENTRY   a limit in the gap (its top, or its midpoint); stop just under the
             sweep's wick; target the nearest confirmed swing high over the
             entry, only at MIN_RR+ reward to risk

SHORTs mirror it.
"""
from __future__ import annotations

import bisect
from typing import Dict, List, Optional, Sequence

M15 = 15 * 60_000
SWING = 3
FVG_BARS = 6
ENTRY_BARS = 8            # a limit is good for 2 hours
LOOKBACK = 96             # swings from the last 24 hours
STOP_BUFFER = 0.0005      # 0.05% beyond the sweep's wick
MIN_RR = 1.5


def pivots(bars: Sequence[Dict], w: int = SWING) -> Dict[str, List[tuple]]:
    """Confirmed swing points: {"low": [(index, price, confirmed_at)], "high": ...}
    — a pivot at i is known only once bar i + w has closed."""
    lows, highs = [], []
    for i in range(w, len(bars) - w):
        lo, hi = float(bars[i]["low"]), float(bars[i]["high"])
        span = bars[i - w:i + w + 1]
        if lo == min(float(b["low"]) for b in span):
            lows.append((i, lo, i + w))
        if hi == max(float(b["high"]) for b in span):
            highs.append((i, hi, i + w))
    return {"low": _Points(lows), "high": _Points(highs)}


def _known(points: Sequence[tuple], before: int, lookback: int = LOOKBACK) -> Sequence[tuple]:
    """The swings confirmed before bar `before` whose pivot is within the
    lookback (points are in pivot order, so confirmed order too)."""
    pts = points if isinstance(points, _Points) else _Points(points)
    hi = bisect.bisect_left(pts.conf, before)
    lo = bisect.bisect_left(pts.idx, before - lookback)
    return pts[lo:hi]


class _Points(list):
    """Swing points with their pivot / confirmation indices for bisect."""
    def __init__(self, pts):
        super().__init__(pts)
        self.idx = [p[0] for p in pts]
        self.conf = [p[2] for p in pts]


def _latest(points: Sequence[tuple], before: int, lookback: int = LOOKBACK) -> Optional[tuple]:
    """The most recent swing confirmed before bar `before`, within lookback."""
    known = _known(points, before, lookback)
    return known[-1] if len(known) else None


def setups(bars: Sequence[Dict], *, at: str = "top", min_rr: float = MIN_RR,
           start_ms: int = 0, piv: Optional[Dict] = None) -> List[Dict]:
    """Every sweep -> FVG setup (both ways) confirmed at a 15m close from
    `start_ms`: {"direction", "ready" (index of the candle that completed the
    gap), "limit", "sl", "target", "rr", "slot_ms"}. One setup per gap. Pure;
    no look-ahead."""
    piv = piv or pivots(bars)
    out, seen = [], set()
    first = max(SWING * 2 + 2, bisect.bisect_left([int(b["timestamp"]) for b in bars],
                                                  start_ms - (FVG_BARS + 2) * M15))
    for i in range(first, len(bars)):
        b = bars[i]
        for d in ("LONG", "SHORT"):
            long = d == "LONG"
            sw = _latest(piv["low"] if long else piv["high"], i)
            if sw is None:
                continue
            lvl = sw[1]
            wick = float(b["low"]) if long else float(b["high"])
            close = float(b["close"])
            swept = (wick < lvl < close) if long else (wick > lvl > close)
            if not swept:
                continue
            for k in range(i + 1, min(i + 1 + FVG_BARS, len(bars))):
                if k < 2:
                    continue
                a, c = bars[k - 2], bars[k]
                if long:
                    lo, hi = float(a["high"]), float(c["low"])     # the gap
                    ok = hi > lo and lo > wick
                else:
                    lo, hi = float(c["high"]), float(a["low"])
                    ok = hi > lo and hi < wick
                if not ok:
                    continue
                if int(c["timestamp"]) + M15 < start_ms:
                    break
                limit = (hi if long else lo) if at == "top" else (lo + hi) / 2
                sl = wick * (1 - STOP_BUFFER) if long else wick * (1 + STOP_BUFFER)
                tgt = _target(piv["high"] if long else piv["low"], k, limit, long)
                if tgt is None or (d, k) in seen:
                    break
                risk, reward = abs(limit - sl), abs(tgt - limit)
                if risk <= 0 or reward / risk < min_rr:
                    break
                seen.add((d, k))
                out.append({"direction": d, "sweep": i, "ready": k, "limit": limit, "sl": sl,
                            "target": tgt, "rr": round(reward / risk, 2),
                            "slot_ms": int(c["timestamp"]) + M15})
                break
    return out


def _target(points: Sequence[tuple], k: int, limit: float, long: bool) -> Optional[float]:
    """The nearest swing (confirmed before bar k) beyond the entry: the
    liquidity resting above a long's entry / below a short's."""
    beyond = [p[1] for p in _known(points, k) if (p[1] > limit if long else p[1] < limit)]
    if not beyond:
        return None
    return min(beyond) if long else max(beyond)


