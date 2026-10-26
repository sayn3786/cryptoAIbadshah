"""
Trend-or-range reads on candles, shared by the live 15m scalp book and its
backtest (scalp_chop_compare), so the two compute the same number.

  chop  Choppiness Index (14): ~100 = pure range, ~0 = one straight move
  adx   Wilder's ADX (14): trend strength regardless of direction (< 20 = none)

Pure: candles in (oldest first, closed only), one value per candle out, None
until there is enough history.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

PERIOD = 14


def _tr(bars: Sequence[Dict], i: int) -> float:
    h, lo = float(bars[i]["high"]), float(bars[i]["low"])
    if i == 0:
        return h - lo
    pc = float(bars[i - 1]["close"])
    return max(h - lo, abs(h - pc), abs(lo - pc))


def chop(bars: Sequence[Dict], n: int = PERIOD) -> List[Optional[float]]:
    """Choppiness Index: 100·log10(ΣTR(n) / (max high − min low over n)) / log10(n).
    ~100 = pure range, ~0 = one straight move; 61.8 / 38.2 are the usual lines."""
    out: List[Optional[float]] = [None] * len(bars)
    trs = [_tr(bars, i) for i in range(len(bars))]
    for i in range(n, len(bars)):
        win = bars[i - n + 1:i + 1]
        span = max(float(c["high"]) for c in win) - min(float(c["low"]) for c in win)
        total = sum(trs[i - n + 1:i + 1])
        if span > 0 and total > 0:
            out[i] = round(100 * math.log10(total / span) / math.log10(n), 2)
    return out


def adx(bars: Sequence[Dict], n: int = PERIOD) -> List[Optional[float]]:
    """Wilder's ADX: trend strength regardless of direction (< 20 = no trend)."""
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) < 2 * n + 1:
        return out
    tr_s = pdm_s = mdm_s = 0.0
    dxs: List[float] = []
    adx_v: Optional[float] = None
    for i in range(1, len(bars)):
        up = float(bars[i]["high"]) - float(bars[i - 1]["high"])
        dn = float(bars[i - 1]["low"]) - float(bars[i]["low"])
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        tr = _tr(bars, i)
        if i <= n:
            tr_s, pdm_s, mdm_s = tr_s + tr, pdm_s + pdm, mdm_s + mdm
            if i < n:
                continue
        else:
            tr_s = tr_s - tr_s / n + tr
            pdm_s = pdm_s - pdm_s / n + pdm
            mdm_s = mdm_s - mdm_s / n + mdm
        if tr_s <= 0:
            continue
        pdi, mdi = 100 * pdm_s / tr_s, 100 * mdm_s / tr_s
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        if adx_v is None:
            dxs.append(dx)
            if len(dxs) == n:
                adx_v = sum(dxs) / n
        else:
            adx_v = (adx_v * (n - 1) + dx) / n
        if adx_v is not None:
            out[i] = round(adx_v, 2)
    return out


def last_adx(bars: Sequence[Dict], n: int = PERIOD) -> Optional[float]:
    """ADX at the newest candle, or None without enough history."""
    vals = adx(bars, n)
    return vals[-1] if vals else None
