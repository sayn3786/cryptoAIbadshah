"""
Volume profile (Nov 22 2026) - INFORMATION ONLY until tested
(volume_profile_test).

Where the volume traded over the last WINDOW candles: each candle's volume
spread evenly across its high-low range into BINS price bins.
  POC   point of control - the bin with the most volume
  VAH / VAL   value area high / low - the range around the POC holding 70% of
        the volume (grown one bin at a time toward the bigger neighbour)

Pure: candles in, levels out. Nothing here is scored.
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence

WINDOW = 30
BINS = 50
VALUE_AREA = 0.70


def profile(candles: Sequence[Dict], window: int = WINDOW, bins: int = BINS) -> Optional[Dict]:
    win = [c for c in list(candles)[-window:] if float(c.get("volume") or 0) > 0]
    if len(win) < 5:
        return None
    lo = min(float(c["low"]) for c in win)
    hi = max(float(c["high"]) for c in win)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    vol = [0.0] * bins
    for c in win:
        cl, ch, v = float(c["low"]), float(c["high"]), float(c["volume"])
        a = min(bins - 1, int((cl - lo) / step))
        b = min(bins - 1, int((ch - lo) / step))
        share = v / (b - a + 1)
        for k in range(a, b + 1):
            vol[k] += share
    total = sum(vol)
    if total <= 0:
        return None
    poc = max(range(bins), key=lambda k: vol[k])
    left, right, acc = poc, poc, vol[poc]
    while acc < VALUE_AREA * total and (left > 0 or right < bins - 1):
        down = vol[left - 1] if left > 0 else -1.0
        up = vol[right + 1] if right < bins - 1 else -1.0
        if up >= down:
            right += 1
            acc += vol[right]
        else:
            left -= 1
            acc += vol[left]
    mid = lambda k: lo + (k + 0.5) * step                       # noqa: E731
    return {"poc": round(mid(poc), 8), "vah": round(lo + (right + 1) * step, 8),
            "val": round(lo + left * step, 8), "window": len(win),
            "low": round(lo, 8), "high": round(hi, 8)}


def read(candles: Sequence[Dict], window: int = WINDOW) -> Optional[Dict]:
    """The profile plus where the latest close sits against it."""
    p = profile(candles, window)
    if not p:
        return None
    px = float(candles[-1]["close"])
    where = ("above the value area" if px > p["vah"] else "below the value area" if px < p["val"]
             else "inside the value area")
    return {**p, "price": px, "where": where,
            "poc_dist_pct": round((p["poc"] / px - 1) * 100, 2)}
