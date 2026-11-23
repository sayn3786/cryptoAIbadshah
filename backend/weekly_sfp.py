"""
The weekly BULLISH swing failure (SFP), live - display only, never scored.

A closed weekly candle wicks below the lowest low of the prior 26 weeks and
CLOSES back above that level (the stops under a 6-month low are taken, price
is reclaimed). weekly_sfp_test (pre-registered, Nov 23 2026) found it
marked a reversal in both eras: LONG from the weekly close won 78% before
2022 and 68% since, vs 66% / 51% for a long every week. The bearish mirror
(a 26-week high swept) had no edge, so it is not shown.

Shown for 4 weeks after the sweep, with whether price still holds the level.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Sequence

LOOK_W = 26
SHOW_W = 4
WEEK_MS = 7 * 86_400_000
NOTE = ("tested since 2018 (weekly_sfp_test): long from the weekly close won 78% before 2022 / 68% since, "
        "vs 66% / 51% for any week · bearish sweeps had no edge · context only, not scored")


def bullish_level(weeks: Sequence[Dict], i: int) -> Optional[float]:
    """The swept 26-week low if week i is a bullish SFP, else None."""
    if i < LOOK_W:
        return None
    lvl = min(float(x["low"]) for x in weeks[i - LOOK_W:i])
    w = weeks[i]
    return lvl if float(w["low"]) < lvl < float(w["close"]) else None


def bearish_level(weeks: Sequence[Dict], i: int) -> Optional[float]:
    """The swept 26-week high if week i is a bearish SFP (tested: no edge)."""
    if i < LOOK_W:
        return None
    lvl = max(float(x["high"]) for x in weeks[i - LOOK_W:i])
    w = weeks[i]
    return lvl if float(w["high"]) > lvl > float(w["close"]) else None


def latest(weeks: Optional[Sequence[Dict]], price: Optional[float] = None) -> Optional[Dict]:
    """The newest bullish SFP among the last SHOW_W CLOSED weeks, or None."""
    if not weeks or len(weeks) <= LOOK_W:
        return None
    n = len(weeks)
    for i in range(n - 1, max(LOOK_W, n - SHOW_W) - 1, -1):
        lvl = bullish_level(weeks, i)
        if lvl is None:
            continue
        w = weeks[i]
        closed_ms = int(w["timestamp"]) + WEEK_MS
        px = float(price) if price is not None else float(weeks[-1]["close"])
        return {"level": round(lvl, 8), "wick_low": float(w["low"]), "close": float(w["close"]),
                "weeks_ago": n - 1 - i,
                "closed": datetime.fromtimestamp(closed_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "holding": px > lvl, "dist_pct": round((px - lvl) / lvl * 100, 2), "note": NOTE}
    return None
