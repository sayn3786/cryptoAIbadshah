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


def structure_exit(bars: Sequence[Dict], i: int, direction: str, piv: Dict,
                   min_rr: float = MIN_RR) -> Optional[Dict]:
    """Stop beyond the latest confirmed swing on the other side, target the
    nearest confirmed swing beyond, measured from bar i's close; None when
    either is missing or R:R < min_rr. Swings confirmed by bar i only."""
    close = float(bars[i]["close"])
    long = direction == "LONG"
    own = _known(piv["low"] if long else piv["high"], i + 1)
    far = _known(piv["high"] if long else piv["low"], i + 1)
    stops = [p[1] for p in own if (p[1] < close if long else p[1] > close)]
    targets = [p[1] for p in far if (p[1] > close if long else p[1] < close)]
    if not stops or not targets:
        return None
    sw = stops[-1]
    stop = sw * (1 - STOP_BUFFER) if long else sw * (1 + STOP_BUFFER)
    tgt = min(targets) if long else max(targets)
    risk, reward = abs(close - stop), abs(tgt - close)
    if risk <= 0 or reward / risk < min_rr:
        return None
    return {"sl": stop, "target": tgt, "rr": round(reward / risk, 2)}


# ── the full smart-money sequence: sweep -> CHoCH -> BOS -> FVG ─────────────
# SHORT shown (LONG mirrors): a wick above the latest confirmed swing high that
# closes back below it (the sweep); within SMC_CHOCH_BARS a close below the last
# confirmed swing low before it (CHoCH); within SMC_BOS_BARS after that, after
# a bounce, a close below every low since the sweep (BOS); the bearish gap that
# move leaves; a limit at the gap's near edge (or midpoint), the stop
# STOP_BUFFER beyond the sweep. Shared by the scalp-smc backtest and the live
# scalp book (scalp_live: a +20 bonus when one just completed the same way).
SMC_CHOCH_BARS = 12
SMC_BOS_BARS = 16
SMC_MIN_STOP_PCT = 0.0015
SMC_MAX_STOP_PCT = 0.03


def _fv(bar: Dict, k: str) -> float:
    return float(bar[k])


def smc_setups(bars: Sequence[Dict], *, start_ms: int = 0, piv: Optional[Dict] = None,
               entry_at: str = "edge") -> List[Dict]:
    """Every full sweep -> CHoCH -> BOS -> FVG setup, both ways: {"direction",
    "sweep", "choch", "bos", "ready" (the candle that completed the gap),
    "limit", "sl", "slot_ms"}. Pure, no look-ahead."""
    piv = piv or pivots(bars)
    n = len(bars)
    out: List[Dict] = []
    for d in ("SHORT", "LONG"):
        short = d == "SHORT"
        i = SWING * 2 + 2
        while i < n:
            b = bars[i]
            swing = _latest(piv["high" if short else "low"], i)
            guard = _latest(piv["low" if short else "high"], i)
            if not swing or not guard:
                i += 1
                continue
            lvl = swing[1]
            swept = (_fv(b, "high") > lvl and _fv(b, "close") < lvl) if short else \
                (_fv(b, "low") < lvl and _fv(b, "close") > lvl)
            if not swept:
                i += 1
                continue
            extreme = _fv(b, "high") if short else _fv(b, "low")
            choch = bos = None
            for j in range(i + 1, min(n, i + 1 + SMC_CHOCH_BARS)):
                c = _fv(bars[j], "close")
                if (c > extreme) if short else (c < extreme):
                    break                                   # the trap failed
                extreme = max(extreme, _fv(bars[j], "high")) if short else \
                    min(extreme, _fv(bars[j], "low"))
                if (c < guard[1]) if short else (c > guard[1]):
                    choch = j
                    break
            if choch is not None:
                for k in range(choch + 2, min(n, choch + 1 + SMC_BOS_BARS)):
                    c = _fv(bars[k], "close")
                    if (c > extreme) if short else (c < extreme):
                        break
                    prior = bars[i:k]
                    edge = min(_fv(x, "low") for x in prior) if short else \
                        max(_fv(x, "high") for x in prior)
                    if (c < edge) if short else (c > edge):
                        bos = k
                        break
            gap = None
            if bos is not None:
                for m in range(max(1, bos - 1), min(n - 1, bos + 2)):
                    a, z = bars[m - 1], bars[m + 1]
                    if short and _fv(z, "high") < _fv(a, "low"):
                        gap = (_fv(z, "high"), _fv(a, "low"), m + 1)       # (near, far, ready)
                        break
                    if not short and _fv(z, "low") > _fv(a, "high"):
                        gap = (_fv(z, "low"), _fv(a, "high"), m + 1)
                        break
            if gap is None:
                i += 1
                continue
            near, far, ready = gap
            limit = near if entry_at == "edge" else (near + far) / 2
            stop = extreme * (1 + STOP_BUFFER) if short else extreme * (1 - STOP_BUFFER)
            risk = abs(stop - limit) / limit
            close_ms = int(bars[ready]["timestamp"]) + M15
            if not (SMC_MIN_STOP_PCT <= risk <= SMC_MAX_STOP_PCT):
                i += 1                                      # unusable stop: keep looking
                continue
            if close_ms >= start_ms:
                out.append({"direction": d, "sweep": i, "choch": choch, "bos": bos,
                            "ready": ready, "limit": limit, "sl": stop, "slot_ms": close_ms})
            i = ready + 1                                   # one setup per sweep
    return sorted(out, key=lambda s: s["slot_ms"])
