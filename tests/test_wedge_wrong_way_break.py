"""
A wedge broken the WRONG way must not simply vanish.

Reported on a $TAO ascending wedge: the card was there ("forming — awaiting a
break above the rail", target $214.7, lower rail $190.475), price closed through
the lower rail, and on the next refresh the card was gone. No status, no trace,
nothing to say it had ever existed.

That is the failure most worth seeing. It is the one that would have cost money,
and it was disappearing faster than the failures the code does keep: a breakout
that confirms and then reverses is recorded as `failed` and shown for three more
closes, while a break the wrong way was `return None` on the spot.

It is now recorded the same way — `confirmed` cleared so scoring and alerts skip
it (both gate on `confirmed`), visible for FAILURE_SHOW_BARS closes, then gone.

A second pass added the classical filter on top: one close through the wrong
rail is NOT a break. Two consecutive closes are required (Edwards & Magee's
two-day rule), and a single close that gets reclaimed is reported as a Wyckoff
spring or upthrust — a stop-run, and a point for the pattern rather than
against it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns                                                       # noqa: E402


BASE_TS = 1_767_268_800_000
STEP = 14_400_000          # 4H


def _candles(extra=()):
    """
    A forming ASCENDING TRIANGLE — flat highs near 200, lows climbing from 176.
    Bullish, so it is drawn expecting a break UP through the rail, which makes a
    close through the floor the wrong-way break under test. Each swing is padded
    either side so the pivot finder can isolate it.
    """
    pairs, lo = [], 176
    for _ in range(5):
        pairs += [(196, 190, 193), (200, 194, 199), (196, 190, 192)]     # swing high
        pairs += [(lo + 6, lo + 2, lo + 4), (lo + 4, lo, lo + 1),
                  (lo + 6, lo + 2, lo + 4)]                              # swing low
        lo += 3
    pairs += list(extra)
    return [{"timestamp": BASE_TS + i * STEP, "open": cl, "high": hi,
             "low": lo_, "close": cl, "volume": 100.0}
            for i, (hi, lo_, cl) in enumerate(pairs)]


def _wedges(extra=()):
    return patterns.detect_triangles_wedges(_candles(extra), "4H")


def _status(pats):
    return [p.get("status") for p in pats]


# -- The report -------------------------------------------------------------

def test_a_pattern_broken_the_wrong_way_is_still_shown():
    """
    The card has to survive the break that killed the idea. Before this it was
    `return None` on the spot: no card, no status, no trace it had existed.
    """
    # Two consecutive closes through the rising floor — see the two-close
    # filter below; one close alone is a sweep, not a break.
    pats = _wedges([(190, 180, 182), (188, 178, 180)])
    assert _status(pats) == ["invalidated"], _status(pats)
    p = pats[0]
    assert "closed below the lower rail" in p["failure_reason"]
    assert p["failed_ts"] is not None, "no trace of WHEN it broke"


def test_an_invalidated_pattern_cannot_reach_the_score():
    """
    Both scoring paths in signals.py gate on `confirmed`. Clearing it is what
    lets an invalidation be VISIBLE without being tradeable - which is what
    makes this safe to land during a freeze on strategy behaviour.
    """
    for p in _wedges([(190, 180, 182), (188, 178, 180)]):
        assert p["confirmed"] is False, "an ended pattern still reads as confirmed"


def test_it_disappears_once_the_break_has_aged_out():
    """
    Visible for FAILURE_SHOW_BARS closes, then gone - the same window a failed
    breakout gets, and what was asked for: "invalidated also to be in the UI
    until the last 3 candles aged and closed".
    """
    assert patterns.FAILURE_SHOW_BARS == 3
    stale = _wedges([(190, 180, 182), (188, 178, 180)] + [(184, 178, 180)] * 6)
    assert "invalidated" not in _status(stale), _status(stale)


def test_an_intact_pattern_still_reads_as_forming():
    """The change must not turn a live pattern into an ended one."""
    assert _status(_wedges([(198, 192, 195)])) == ["forming"]


def test_a_break_the_right_way_still_confirms():
    """An ascending triangle breaking UP is the outcome it was drawn for."""
    pats = _wedges([(212, 200, 210)])
    assert _status(pats) == ["confirmed"]
    assert pats[0]["confirmed"] is True


def test_ended_patterns_sort_ahead_of_forming_ones():
    """
    "This one is over" is the more urgent card. The rank table has to know the
    new status or an invalidation sorts as though it were still forming.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend",
                            "patterns.py"), encoding="utf-8").read()
    rank = src[src.index("_rank = {"):src.index(chr(10), src.index("_rank = {"))]
    assert '"invalidated": 0' in rank, "the sort rank does not know about invalidation"


# -- The two-close filter, and the spring -----------------------------------
#
# Edwards & Magee's two-day rule: price must close beyond the line on TWO
# consecutive bars before the pattern is given up on. Their other filter — 3%
# penetration — is deliberately not copied; it was written for daily bars on
# mid-century equities and is meaningless on a 4H altcoin candle.
#
# A single close that is reclaimed is a SPRING below support (or an UPTHRUST
# above resistance) in Wyckoff's vocabulary: a stop-run, and classically a point
# FOR the pattern rather than against it.

def _sweep(pats):
    return [(p.get("sweep") or {}).get("type") for p in pats]


def test_one_close_through_the_rail_is_not_a_break():
    """
    The two-day rule. One close beyond the line, then a reclaim, and the
    pattern is intact — it never broke.
    """
    pats = _wedges([(190, 180, 182), (196, 188, 193)])
    assert _status(pats) == ["forming"]
    assert "invalidated" not in _status(pats)


def test_a_reclaimed_sweep_is_reported_as_a_spring():
    """
    Not merely "still forming" — the sweep is the event. Price took the stops
    under the rail and gave the level straight back.
    """
    pats = _wedges([(190, 180, 182), (196, 188, 193)])
    assert _sweep(pats) == ["spring"]
    s = pats[0]["sweep"]
    assert s["swept_ts"] is not None and s["reclaimed_ts"] is not None
    assert s["level"] is not None
    assert s["reclaimed_ts"] > s["swept_ts"], "reclaim must come after the sweep"


def test_two_consecutive_closes_through_do_invalidate():
    """Once the filter is satisfied it is a real break, as before."""
    pats = _wedges([(190, 180, 182), (188, 178, 180)])
    assert _status(pats) == ["invalidated"]
    assert "two consecutive bars" in pats[0]["failure_reason"]


def test_a_confirmed_break_is_not_undone_by_a_later_reclaim():
    """
    Two closes through settle it. Price wandering back inside afterwards does
    not resurrect the pattern — that would be a card that un-breaks itself.
    """
    pats = _wedges([(190, 180, 182), (188, 178, 180), (196, 188, 193)])
    assert _status(pats) == ["invalidated"]
    assert _sweep(pats) == [None]


def test_a_single_close_with_nothing_after_it_says_nothing_yet():
    """
    One close through and no bar after it: the filter cannot be evaluated.
    Reporting a break here and withdrawing it next bar is worse than waiting.
    """
    pats = _wedges([(190, 180, 182)])
    assert _status(pats) == ["forming"]
    assert _sweep(pats) == [None], "a sweep must not be claimed before the reclaim"


def test_an_old_sweep_stops_being_news():
    """
    A spring six bars back is history, not a live event — the same ageing the
    invalidations get.
    """
    pats = _wedges([(190, 180, 182), (196, 188, 193)] + [(197, 191, 194)] * 6)
    assert _status(pats) == ["forming"]
    assert _sweep(pats) == [None]


def test_the_filter_is_the_classical_two_bar_rule():
    assert patterns.WRONG_WAY_CONFIRM_BARS == 2


def test_a_spring_is_not_tradeable_on_its_own():
    """
    Reporting only, like everything else added during the freeze. `confirmed`
    stays False, and both scoring paths in signals.py gate on it.
    """
    for p in _wedges([(190, 180, 182), (196, 188, 193)]):
        assert p["confirmed"] is False
