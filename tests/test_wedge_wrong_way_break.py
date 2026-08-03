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
    pats = _wedges([(190, 180, 182)])          # closes through the rising floor
    assert _status(pats) == ["invalidated"], _status(pats)
    p = pats[0]
    assert p["failure_reason"] == "closed below the lower rail"
    assert p["failed_ts"] is not None, "no trace of WHEN it broke"


def test_an_invalidated_pattern_cannot_reach_the_score():
    """
    Both scoring paths in signals.py gate on `confirmed`. Clearing it is what
    lets an invalidation be VISIBLE without being tradeable - which is what
    makes this safe to land during a freeze on strategy behaviour.
    """
    for p in _wedges([(190, 180, 182)]):
        assert p["confirmed"] is False, "an ended pattern still reads as confirmed"


def test_it_disappears_once_the_break_has_aged_out():
    """
    Visible for FAILURE_SHOW_BARS closes, then gone - the same window a failed
    breakout gets, and what was asked for: "invalidated also to be in the UI
    until the last 3 candles aged and closed".
    """
    assert patterns.FAILURE_SHOW_BARS == 3
    stale = _wedges([(190, 180, 182)] + [(184, 178, 180)] * 6)
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
