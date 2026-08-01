"""
One shape for "how old is this pattern, and does it still count?".

Every detector had its own answer, and none of them said it out loud. CHoCH
faded over 10 candles and the liquidity grab over 5 — both as bare divisions
buried inside `signals.py`, invisible to anything else. Flags and wedges carried
a status but no weight. RSI divergence had no age term at all and scored the
same on candle 1 as on candle 29.

So the scorer knew how stale a pattern was and the UI did not, the windows lived
as magic numbers a long way from the detector they belonged to, and adding a
sixth detector meant inventing a seventh convention.

This module owns the windows and the vocabulary. A detector reports its age; the
lifecycle says what that age MEANS:

    forming    not yet a fact — waiting on a close to confirm it
    confirmed  inside its window, full weight
    expired    past the window, fading over the grace bars
    (dropped)  beyond that, gone

`freshness` is the number the scorer multiplies by. The fade across the grace
bars is deliberate: a cliff would let one candle close flip a signal from full
weight to nothing, and a trade's direction should not turn on which side of an
arbitrary boundary a candle landed.

Nothing here reads the market. It is arithmetic on a candle count, so it is
exhaustively testable and cannot disagree with itself between two callers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["FRESH_BARS", "GRACE_BARS", "classify", "annotate", "STATUSES"]

STATUSES = ("forming", "confirmed", "expired")

# How long each pattern stays worth acting on, in CLOSED candles since the event
# that created it. These were previously inline divisions in signals.py; the
# values are preserved exactly, they simply live next to each other now where
# they can be compared.
# Two shapes of decay, and they are NOT interchangeable.
#
#   "linear" — fades from the moment it happens, reaching zero at fresh_bars.
#             CHoCH and the liquidity grab have always worked this way
#             (`1 - candles_ago / 10` and `/ 5`), and adopting the other curve
#             would silently double the weight of a 5-candle-old CHoCH. That is
#             a strategy change nobody asked for, so the curve is preserved.
#   "window" — full weight inside fresh_bars, then fades across the grace bars.
#             A called turn is either live or it is not; it does not get weaker
#             the day after it prints.
CURVE: Dict[str, str] = {
    "rsi_divergence": "window",
    "flag":           "window",
    "triangle":       "window",
    "choch":          "linear",
    "liquidity_grab": "linear",
    "engulfing":      "linear",
    "fvg":            "window",
    "acc_eql_fvg":    "linear",
}

FRESH_BARS: Dict[str, int] = {
    "rsi_divergence": 12,   # a called turn: either it happens soon or it did not
    "choch":          10,   # was `1 - candles_ago / 10`
    "liquidity_grab":  5,   # was `1 - candles_ago / 5` — a sweep is a fast signal
    "engulfing":       5,
    "fvg":            30,   # a gap is a level; it matters until filled, not by age
    "acc_eql_fvg":    10,
    "flag":            8,
    "triangle":        8,
}

# Kept visible for this many candles after expiry, so a setup that lapsed reads
# as lapsed instead of silently vanishing. Same window `patterns.FAILURE_SHOW_BARS`
# already used for failed flags — deliberately one number, not two.
GRACE_BARS = 3

_DEFAULT_FRESH = 10


def window(kind: str) -> int:
    """Fresh window for a pattern kind. Unknown kinds get a sane middle value
    rather than an exception — a new detector should degrade, not crash."""
    return FRESH_BARS.get(kind, _DEFAULT_FRESH)


def classify(age_candles: Optional[int], kind: str, *,
             forming: bool = False,
             grace: int = GRACE_BARS) -> Optional[Dict[str, Any]]:
    """
    What an age means for this pattern kind.

    Returns ``{"status", "age_candles", "fresh_bars", "freshness"}``, or **None**
    when the pattern is old enough to drop entirely — the caller should then
    report nothing rather than something stale.

    ``forming`` short-circuits: a pattern still waiting on a close has no
    freshness to lose, because it has not started counting yet.
    """
    fresh = window(kind)
    curve = CURVE.get(kind, "window")
    if forming:
        return {"status": "forming", "age_candles": age_candles,
                "fresh_bars": fresh, "freshness": 1.0}

    try:
        age = int(age_candles)
    except (TypeError, ValueError):
        # No age reported. Treating that as stale would silently mute a live
        # pattern, so assume fresh and let the detector's own rules decide.
        return {"status": "confirmed", "age_candles": None,
                "fresh_bars": fresh, "freshness": 1.0}

    age = max(0, age)

    if curve == "linear":
        # Reaches exactly zero AT fresh_bars, matching the arithmetic these
        # detectors have always used. Once it is worth nothing it is history:
        # shown for the grace bars, then dropped.
        f = max(0.0, 1 - age / float(fresh))
        if f > 0:
            return {"status": "confirmed", "age_candles": age,
                    "fresh_bars": fresh, "freshness": round(f, 4)}
        if age > fresh + grace:
            return None
        return {"status": "expired", "age_candles": age,
                "fresh_bars": fresh, "freshness": 0.0}

    if age <= fresh:
        return {"status": "confirmed", "age_candles": age,
                "fresh_bars": fresh, "freshness": 1.0}
    if age > fresh + grace:
        return None                              # gone
    return {"status": "expired", "age_candles": age, "fresh_bars": fresh,
            "freshness": round(max(0.0, 1 - (age - fresh) / (grace + 1)), 3)}


def annotate(payload: Optional[Dict[str, Any]], kind: str, *,
             age_key: str = "candles_ago",
             forming_key: str = "forming",
             grace: int = GRACE_BARS) -> Optional[Dict[str, Any]]:
    """
    Add the lifecycle fields to a detector's own payload, in place-safe fashion.

    Returns None when the pattern has aged out, so a caller can drop it with
    ``if not (d := annotate(d, "choch")): ...`` rather than checking a status
    string it might typo.
    """
    if not payload:
        return payload
    life = classify(payload.get(age_key), kind,
                    forming=bool(payload.get(forming_key)), grace=grace)
    if life is None:
        return None
    out = dict(payload)
    out.update(life)
    return out


def decay(points: float, freshness: Optional[float]) -> int:
    """
    Weight points by freshness without rounding a live signal away to nothing.

    2 points at freshness 0.2 floors to 0, which would silently drop a pattern
    that still counts. Only a freshness of zero may produce zero.
    """
    f = 1.0 if freshness is None else max(0.0, min(1.0, float(freshness)))
    out = int(round(points * f))
    if out == 0 and f > 0 and points:
        return 1 if points > 0 else -1
    return out
