"""
Unusual-candle reads for the morning Market Update (Nov 2026), from CLOSED
candles only:

  streak      N candles in a row closed the same colour (e.g. "7 red weeks
              in a row"), shown once the run is long for this timeframe, with
              the coin's own history: how often a run this long happened in
              the candles we have, when last, and what price did a few candles
              after those times (history, not a forecast)
  lost level  price CLOSED above a prior swing high (a horizontal level), then
              closed back below it within a few candles - a failed breakout,
              "the break back below horizontal". Mirrored: closed below a
              swing low, then closed back above it (a failed breakdown).

Both are CONTEXT reads (`context: True`): listed with the others, but they
never count toward the weekly star, the market lean or the conflict tags,
and the 4H publish never reads them. Pure: no network, no clock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

# A run is worth a line from this many candles (rare enough on that chart).
MIN_STREAK = {"1D": 6, "1W": 4, "BW": 4, "1M": 4}
# Candles after a past run reached this length, to say what price did next
# (about two weeks on the weekly charts, a week on the daily one).
AFTER = {"1D": 7, "1W": 2, "BW": 2, "1M": 2}
UNIT = {"1D": ("day", "days"), "1W": ("week", "weeks"), "BW": ("USA week", "USA weeks"),
        "1M": ("month", "months")}

PIVOT_K = 3            # a swing high / low: the extreme of 3 candles each side
LOST_WITHIN = 4        # the close back through the level came within this many candles
MIN_LEVEL_AGE = 6      # the level formed at least this many candles before the break
BREAK_MARGIN = 0.002   # a close counts as through the level by 0.2% (no hairline breaks)


def _colour(c: Dict[str, Any]) -> int:
    o, cl = float(c["open"]), float(c["close"])
    return 1 if cl > o else -1 if cl < o else 0


def _runs(colours: Sequence[int]) -> List[tuple]:
    """(start, end, colour) of every same-colour run (a flat candle ends one)."""
    out, i = [], 0
    while i < len(colours):
        j = i
        while j + 1 < len(colours) and colours[j + 1] == colours[i] and colours[i] != 0:
            j += 1
        if colours[i] != 0:
            out.append((i, j, colours[i]))
        i = j + 1
    return out


def _date(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, timezone.utc).strftime("%b %Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def streak(closed: Sequence[Dict[str, Any]], tf: str) -> Optional[Dict[str, Any]]:
    """The run that ends on the latest closed candle, when it is at least
    MIN_STREAK long for `tf`, with its history in these candles."""
    need = MIN_STREAK.get(tf)
    if not need or len(closed) < need + 1:
        return None
    colours = [_colour(c) for c in closed]
    runs = _runs(colours)
    if not runs or runs[-1][1] != len(closed) - 1:
        return None
    start, end, col = runs[-1]
    n = end - start + 1
    if n < need:
        return None
    # Past runs of the same colour that reached n: when, and what came after
    # the candle on which they reached n.
    past, after = [], []
    h = AFTER.get(tf, 2)
    for s, e, c in runs[:-1]:
        if c == col and e - s + 1 >= n:
            hit = s + n - 1
            past.append(closed[e]["timestamp"])
            if hit + h < len(closed):
                p0, p1 = float(closed[hit]["close"]), float(closed[hit + h]["close"])
                if p0 > 0:
                    after.append((p1 - p0) / p0 * 100)
    one, many = UNIT.get(tf, ("candle", "candles"))
    word = "red" if col < 0 else "green"
    label = f"{n} {word} {many} in a row"
    if past:
        label += f" — longest since {_date(past[-1])}"
    else:
        label += f" — the longest in the {len(closed)} {many} of data"
    note = None
    if after:
        up = sum(1 for x in after if x > 0)
        avg = sum(after) / len(after)
        note = (f"{len(past)} time{'s' if len(past) != 1 else ''} before; "
                f"{h} {one if h == 1 else many} later: {up} up, {len(after) - up} down, "
                f"avg {avg:+.1f}% (history, not a forecast)")
    elif past:
        note = f"{len(past)} time{'s' if len(past) != 1 else ''} before"
    return {"kind": "streak", "context": True, "status": "active", "label": label,
            "direction": "bearish" if col < 0 else "bullish", "length": n,
            "history_note": note, "bars_ago": 0,
            "break_ts": closed[-1]["timestamp"]}


def _pivots(closed: Sequence[Dict[str, Any]], k: int = PIVOT_K):
    hi, lo = [], []
    for i in range(k, len(closed) - k):
        h, l_ = float(closed[i]["high"]), float(closed[i]["low"])
        win = closed[i - k:i + k + 1]
        if h >= max(float(c["high"]) for c in win):
            hi.append((i, h))
        if l_ <= min(float(c["low"]) for c in win):
            lo.append((i, l_))
    return hi, lo


def lost_level(closed: Sequence[Dict[str, Any]], tf: str) -> Optional[Dict[str, Any]]:
    """A failed breakout (or breakdown) completed on the LATEST closed candle:
    an earlier close beyond a prior swing level within LOST_WITHIN candles,
    and now a close back on the other side."""
    n = len(closed)
    if n < MIN_LEVEL_AGE + 2 * PIVOT_K + 2:
        return None
    last = n - 1
    close = [float(c["close"]) for c in closed]
    hi, lo = _pivots(closed[:last])          # levels from before the latest candle
    best = None
    for side, levels in (("up", hi), ("down", lo)):
        for pi, lvl in levels:
            # the breakout candle: within LOST_WITHIN before the latest close,
            # at least MIN_LEVEL_AGE after the level formed
            for b in range(max(pi + MIN_LEVEL_AGE, last - LOST_WITHIN), last):
                beyond = (close[b] > lvl * (1 + BREAK_MARGIN) if side == "up"
                          else close[b] < lvl * (1 - BREAK_MARGIN))
                if not beyond:
                    continue
                # no close back through before the latest one (that would be
                # an older failure), and the latest closes back through
                held = all((close[j] > lvl) if side == "up" else (close[j] < lvl)
                           for j in range(b, last))
                back = (close[last] < lvl * (1 - BREAK_MARGIN) if side == "up"
                        else close[last] > lvl * (1 + BREAK_MARGIN))
                if held and back:
                    dist = abs(close[last] - lvl) / lvl
                    if best is None or dist < best[0]:
                        best = (dist, side, lvl, b, pi)
                break
    if not best:
        return None
    _d, side, lvl, b, pi = best
    one, many = UNIT.get(tf, ("candle", "candles"))
    ago = last - b
    when = f"{ago} {one if ago == 1 else many} ago"
    if side == "up":
        label = f"Lost {lvl:,.6g} again — broke above {when}, closed back below (failed breakout)"
    else:
        label = f"Reclaimed {lvl:,.6g} — broke below {when}, closed back above (failed breakdown)"
    return {"kind": "lost_level", "context": True, "status": "active", "label": label,
            "direction": "bearish" if side == "up" else "bullish", "level": lvl,
            "bars_ago": 0, "break_ts": closed[-1]["timestamp"],
            "level_ts": closed[pi]["timestamp"]}


def reads(closed: Sequence[Dict[str, Any]], tf: str) -> List[Dict[str, Any]]:
    out = []
    for fn in (streak, lost_level):
        try:
            r = fn(closed, tf)
        except Exception:                                # noqa: BLE001
            r = None
        if r:
            out.append(r)
    return out
