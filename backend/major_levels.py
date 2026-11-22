"""
Multi-year MAJOR LEVELS and the MEGAPHONE (broadening) shape (Nov 22 2026) -
DISPLAY ONLY, never scored (price levels tested as a coin flip for bounces:
range / retest / order-block / value-area tests). They mark where a trader
would put a stop or take profit, as the 2W charts traders post do.

Major levels: swing highs and lows of the WEEKLY history (about 8 years),
grouped into bands (each swing within BAND_PCT of its neighbour, a band at most
2 x BAND_PCT wide), ranked by
how many times price turned there. Each band says how it acted: as
resistance (swing highs), support (swing lows) or both, and whether it
flipped ("was resistance in 2021, support in 2026").

Megaphone: on the timeframe's own candles, the last swing highs rising AND
the last swing lows falling - a widening range. The two lines are drawn
through the first and last of each; a close outside one is the break.

Pure: candles in, levels out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

SWING_W = 3
BAND_PCT = 3.0
MIN_TOUCHES = 2
KEEP = 2
MAX_DIST_PCT = 45.0
MEGA_PIVOTS = 3
MEGA_W = 3


def _pivots(c: Sequence[Dict], w: int):
    hs, ls = [], []
    for i in range(w, len(c) - w):
        span = c[i - w:i + w + 1]
        if float(c[i]["high"]) == max(float(x["high"]) for x in span):
            hs.append(i)
        if float(c[i]["low"]) == min(float(x["low"]) for x in span):
            ls.append(i)
    return hs, ls


def _year(ts) -> int:
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).year


def bands(weekly: Sequence[Dict]) -> List[Dict]:
    """Every band of 2+ swings, strongest first."""
    hs, ls = _pivots(weekly, SWING_W)
    pts = sorted([(float(weekly[i]["high"]), "high", weekly[i]["timestamp"]) for i in hs]
                 + [(float(weekly[i]["low"]), "low", weekly[i]["timestamp"]) for i in ls])
    # neighbour-to-neighbour within BAND_PCT, the whole band at most 2 x BAND_PCT wide
    groups: List[List] = []
    for p in pts:
        if groups and p[0] <= groups[-1][-1][0] * (1 + BAND_PCT / 100) \
                and p[0] <= groups[-1][0][0] * (1 + 2 * BAND_PCT / 100):
            groups[-1].append(p)
        else:
            groups.append([p])
    out = []
    for g in groups:
        if len(g) < MIN_TOUCHES:
            continue
        kinds = [k for _, k, _ in g]
        by_time = sorted(g, key=lambda x: int(x[2]))
        first, last = by_time[0], by_time[-1]
        role = ("support and resistance" if "high" in kinds and "low" in kinds
                else "resistance" if "high" in kinds else "support")
        flip = None
        if first[1] != last[1]:
            flip = (f"was {'resistance' if first[1] == 'high' else 'support'} in {_year(first[2])}, "
                    f"{'resistance' if last[1] == 'high' else 'support'} in {_year(last[2])}")
        out.append({"bottom": round(min(x[0] for x in g), 8), "top": round(max(x[0] for x in g), 8),
                    "touches": len(g), "role": role, "flip": flip,
                    "first_year": _year(first[2]), "last_year": _year(last[2])})
    return sorted(out, key=lambda b: -b["touches"])


def levels(weekly: Sequence[Dict], price: float) -> Optional[Dict]:
    """The nearest major bands above and below price (and one price is in)."""
    if not weekly or len(weekly) < 4 * SWING_W or not price:
        return None
    bs = bands(weekly)
    near = [b for b in bs if min(abs(b["bottom"] / price - 1), abs(b["top"] / price - 1)) * 100 <= MAX_DIST_PCT
            or b["bottom"] <= price <= b["top"]]
    for b in near:
        mid = (b["bottom"] + b["top"]) / 2
        b["dist_pct"] = round((mid / price - 1) * 100, 1)
    above = sorted([b for b in near if b["bottom"] > price], key=lambda b: b["bottom"])[:KEEP]
    below = sorted([b for b in near if b["top"] < price], key=lambda b: -b["top"])[:KEEP]
    inside = next((b for b in near if b["bottom"] <= price <= b["top"]), None)
    return {"above": above, "below": below, "inside": inside, "weeks": len(weekly)}


def megaphone(candles: Sequence[Dict]) -> Optional[Dict]:
    """Rising swing highs AND falling swing lows over the last MEGA_PIVOTS of
    each, overlapping in time: a broadening range. None otherwise."""
    if not candles or len(candles) < 6 * MEGA_W:
        return None
    hs, ls = _pivots(candles, MEGA_W)
    hs, ls = hs[-MEGA_PIVOTS:], ls[-MEGA_PIVOTS:]
    if len(hs) < 2 or len(ls) < 2:
        return None
    hv = [float(candles[i]["high"]) for i in hs]
    lv = [float(candles[i]["low"]) for i in ls]
    if not all(b > a for a, b in zip(hv, hv[1:])) or not all(b < a for a, b in zip(lv, lv[1:])):
        return None
    if max(hs[0], ls[0]) > min(hs[-1], ls[-1]):
        return None                                      # the two legs must overlap in time
    n = len(candles) - 1

    def line(i0, v0, i1, v1):
        slope = (v1 - v0) / (i1 - i0)
        return v0 + slope * (n - i0)
    upper = line(hs[0], hv[0], hs[-1], hv[-1])
    lower = line(ls[0], lv[0], ls[-1], lv[-1])
    close = float(candles[-1]["close"])
    status = "broke above" if close > upper else "broke below" if close < lower else "inside"
    return {"upper_now": round(upper, 8), "lower_now": round(lower, 8), "status": status,
            "upper_points": [{"timestamp": candles[i]["timestamp"], "price": float(candles[i]["high"])} for i in hs],
            "lower_points": [{"timestamp": candles[i]["timestamp"], "price": float(candles[i]["low"])} for i in ls]}
