"""
Range boxes and deviations (Nov 2026), for every coin and timeframe, from
CLOSED candles.

A RANGE is a stretch of at least MIN_BARS candles whose BODIES (open / close)
stayed inside a band no taller than BAND_ATR x the ATR at its start: price
going sideways, the boxes traders draw by hand ("the box"). The box spans the
highest and lowest body in that stretch; wicks may poke out.

After a range ends, every later close is ABOVE the box, INSIDE it or BELOW it,
and the latest close can mean:

  broke above / below   it closed outside, the close before was inside
  deviation below       it closed back INSIDE after 1-DEV_MAX closes below the
                        box: the break down failed ("deviation confirmed")
  deviation above       the mirror: a failed break up
  back inside           it closed back inside after more than DEV_MAX closes
                        outside: price returned to an old range (a retest)

Pure: no network, no clock.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

MIN_BARS = 12          # a range lasts at least this many candles
BAND_ATR = 3.0         # its body band is at most this many ATRs tall
ATR_N = 14
EDGE_TOL_ATR = 0.25    # an established range continues while bodies stay this close
DEV_MAX = 6            # closes outside before a return still counts as a deviation
MAX_BOXES = 3          # the most recent ranges kept
NEAR_PCT = 35.0        # a range further than this from price is dropped


def _atr(c: Sequence[Dict[str, Any]], i: int, n: int = ATR_N) -> Optional[float]:
    if i - n < 1:
        return None
    trs = []
    for k in range(i - n, i):
        h, lo, pc = float(c[k]["high"]), float(c[k]["low"]), float(c[k - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / n


def _body(b: Dict[str, Any]):
    o, cl = float(b["open"]), float(b["close"])
    return max(o, cl), min(o, cl)


def find_ranges(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sideways stretches, oldest first (greedy, non-overlapping)."""
    n = len(candles)
    out, i = [], ATR_N + 1
    while i <= n - MIN_BARS:
        a = _atr(candles, i)
        if not a:
            i += 1
            continue
        s0, j = i, i
        top, bot = _body(candles[i])
        while j + 1 < n:
            t, b = _body(candles[j + 1])
            if j - s0 + 1 >= MIN_BARS:
                # Established: the edges are fixed. It goes on only while the
                # bodies stay inside (a little tolerance); a close outside
                # ends it - that close is what a break / deviation is about.
                tol = EDGE_TOL_ATR * a
                if t > top + tol or b < bot - tol:
                    break
                j += 1
                continue
            if max(top, t) - min(bot, b) > BAND_ATR * a:
                break
            top, bot, j = max(top, t), min(bot, b), j + 1
            if j - s0 + 1 == MIN_BARS:
                # The run may have swallowed the tail of the move INTO the
                # range: drop leading candles while that shrinks the box 15%+
                # (it then keeps growing until it is MIN_BARS long again).
                while j - s0 + 1 > 2:
                    rest = [_body(candles[k]) for k in range(s0 + 1, j + 1)]
                    t2, b2 = max(x[0] for x in rest), min(x[1] for x in rest)
                    if (t2 - b2) < 0.85 * (top - bot):
                        s0, top, bot = s0 + 1, t2, b2
                    else:
                        break
        if j - s0 + 1 >= MIN_BARS:
            out.append({"i0": s0, "i1": j, "top": top, "bottom": bot})
            i = j + 1
        else:
            i += 1
    return out


def _side(close: float, box: Dict[str, Any]) -> int:
    return 1 if close > box["top"] else -1 if close < box["bottom"] else 0


def _events(candles: Sequence[Dict[str, Any]], box: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every event after the range ended, oldest first."""
    sides = [_side(float(c["close"]), box) for c in candles]
    out = []
    for k in range(box["i1"] + 1, len(candles)):
        now, before = sides[k], sides[k - 1]
        if now == before:
            continue
        if now != 0 and before == 0:
            out.append({"i": k, "event": "broke_above" if now > 0 else "broke_below"})
        elif now == 0:
            run, m = 0, k - 1
            while m > box["i1"] and sides[m] == before:
                run, m = run + 1, m - 1
            kind = ("deviation_below" if before < 0 else "deviation_above") if run <= DEV_MAX \
                else "back_inside"
            out.append({"i": k, "event": kind, "outside": run,
                        "from": "below" if before < 0 else "above"})
        else:                                   # straight through, above <-> below
            out.append({"i": k, "event": "broke_above" if now > 0 else "broke_below"})
    return out


EVENT_TEXT = {
    "broke_above": "closed above the range",
    "broke_below": "closed below the range",
    "deviation_below": "dipped below the range and closed back inside — deviation confirmed "
                       "(the breakdown failed)",
    "deviation_above": "poked above the range and closed back inside — deviation confirmed "
                       "(the breakout failed)",
    "back_inside": "came back inside the range",
}


def boxes(candles: Sequence[Dict[str, Any]], *, tf_ms: int = 0) -> List[Dict[str, Any]]:
    """The recent ranges near price, each with where price is now, its latest
    event, and a one-line explanation for the dashboard."""
    if len(candles) < ATR_N + MIN_BARS + 1:
        return []
    last = len(candles) - 1
    px = float(candles[last]["close"])
    found = find_ranges(candles)[-MAX_BOXES:]
    out = []
    for r in found:
        top, bot = r["top"], r["bottom"]
        inside_now = bot <= px <= top
        far = px > 0 and min(abs(px - top), abs(px - bot)) / px * 100 > NEAR_PCT
        if px <= 0 or (far and not inside_now):
            continue
        current = r["i1"] >= last
        side = _side(px, r)
        ev = None if current else (_events(candles, r) or [None])[-1]
        where = ("inside it" if side == 0 else
                 f"{(px - top) / top * 100:+.1f}% above it" if side > 0 else
                 f"{(px - bot) / bot * 100:+.1f}% below it")
        text = (f"Price went sideways here for {r['i1'] - r['i0'] + 1} candles between "
                f"{bot:,.6g} and {top:,.6g}. ")
        text += ("Price is still in this range." if current else f"Price is now {where}.")
        if ev:
            ago = last - ev["i"]
            text += (f" Last event: {EVENT_TEXT[ev['event']]}"
                     + (" on the latest candle." if ago == 0 else
                        f" {ago} candle{'s' if ago != 1 else ''} ago."))
        out.append({
            "start_ts": candles[r["i0"]]["timestamp"],
            "end_ts": int(candles[r["i1"]]["timestamp"]) + (tf_ms or 0),
            "top": round(top, 10), "bottom": round(bot, 10),
            "bars": r["i1"] - r["i0"] + 1, "current": current,
            "position": "inside" if side == 0 else "above" if side > 0 else "below",
            "event": ev["event"] if ev else None,
            "event_ago": (last - ev["i"]) if ev else None,
            "event_ts": candles[ev["i"]]["timestamp"] if ev else None,
            "outside_candles": ev.get("outside") if ev else None,
            "text": text,
        })
    return out


# Only confirmed deviations go to the Market Update. On random price paths
# some range event fires on ~40% of charts every day (mostly plain breaks):
# across 49 coins x 4 timeframes that would bury everything else. Breaks and
# returns stay on the dashboard chart.
TELEGRAM_EVENTS = ("deviation_below", "deviation_above")


# Tested Nov 2026 (range_vs_random, pre-registered, 4H and 1D, two years):
# neither "deviation -> opposite edge" nor "held break -> continue" beat a
# coin flip in both years; the 1D readings even flipped between them. So a
# range event is shown with NO direction (neutral), as information only.
TESTED_NOTE = "tested over 2 years: not a reliable up/down signal"


def reads(candles: Sequence[Dict[str, Any]], tf: str) -> List[Dict[str, Any]]:
    """Market Update context reads: a deviation confirmed on the LATEST
    closed candle. Always neutral (see TESTED_NOTE)."""
    out = []
    for b in boxes(candles):
        if b["event_ago"] != 0 or b["event"] not in TELEGRAM_EVENTS:
            continue
        lo, hi = f"{b['bottom']:,.6g}", f"{b['top']:,.6g}"
        ev = b["event"]
        if ev == "deviation_below":
            label = (f"Deviation below the {lo}–{hi} range confirmed — closed back inside "
                     f"after {b['outside_candles']} candle(s) below")
            d = "bullish"
        elif ev == "deviation_above":
            label = (f"Deviation above the {lo}–{hi} range confirmed — closed back inside "
                     f"after {b['outside_candles']} candle(s) above")
            d = "bearish"
        elif ev == "broke_above":
            label, d = f"Closed above the {lo}–{hi} range", "bullish"
        elif ev == "broke_below":
            label, d = f"Closed below the {lo}–{hi} range", "bearish"
        else:
            label = f"Back inside the {lo}–{hi} range (an old range, retested)"
            d = "neutral"
        out.append({"kind": "range_event", "context": True, "status": "active",
                    "label": label, "direction": "neutral", "textbook": d,
                    "history_note": TESTED_NOTE, "event": ev, "bars_ago": 0,
                    "level": None, "break_ts": candles[-1]["timestamp"],
                    "range": [b["bottom"], b["top"]]})
    return out
