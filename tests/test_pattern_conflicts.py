"""
Two confirmed structures on the same candles, pointing opposite ways.

Seen live on a 1H chart: a Descending Triangle "confirmed — broke down, target
$183.64" sitting directly above a Falling Wedge "confirmed — broke up, target
$195.71". Same symbol, same timeframe, same candles, opposite conclusions, both
presented with equal confidence.

WHERE IT CAME FROM. detect_triangles_wedges deliberately returns two fits of
the same pivots — the peeled one (breakout pivots removed) and the unpeeled
one — so a resolved structure and the one forming in its place are both
visible. But `_same_structure` only dedupes when the TYPE matches, so two
different pattern types fitted to the same swings sailed through.

WHY IT MATTERED BEYOND THE DISPLAY. Both reached the score. The pattern scorer
dedupes by DIRECTION, so it scored the first bullish AND the first bearish: +12
and -12. They cancel to zero, which reads as "patterns say nothing" when the
truth is "patterns are in direct conflict" — and when only one of the pair is
fresh they do not cancel at all, and one side scores alone off a structure the
other half contradicts.

THE CHOICE. Both are cleared rather than picking a winner, because choosing
would need a strength heuristic invented with no evidence behind it. Two
opposite confirmations mean the structure has not resolved.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns                                                       # noqa: E402


def _pat(direction, ptype, confirmed=True, status="confirmed", **extra):
    p = {"type": ptype, "label": ptype.replace("_", " ").title(),
         "direction": direction, "confirmed": confirmed, "status": status,
         "breakout_dir": "up" if direction == "bullish" else "down",
         "target": 100.0, "conflict": None}
    p.update(extra)
    return p


# ── The conflict ────────────────────────────────────────────────────────────

def test_two_confirmed_fits_disagreeing_are_both_stood_down():
    """The reported case: one broke down, the other up, on the same candles."""
    out = patterns._mark_conflicts([
        _pat("bearish", "descending_triangle"),
        _pat("bullish", "falling_wedge"),
    ])
    assert [p["status"] for p in out] == ["conflicted", "conflicted"]
    assert all(p["confirmed"] is False for p in out), \
        "an ambiguous chart must not read as a confirmed signal"


def test_each_side_names_the_other():
    """
    "Ambiguous" alone is not useful. A reader needs to know what it is
    ambiguous WITH, and which way that one broke.
    """
    out = patterns._mark_conflicts([
        _pat("bearish", "descending_triangle"),
        _pat("bullish", "falling_wedge"),
    ])
    bear, bull = out[0], out[1]
    assert bear["conflict"]["type"] == "falling_wedge"
    assert bear["conflict"]["direction"] == "bullish"
    assert bear["conflict"]["breakout_dir"] == "up"
    assert bull["conflict"]["type"] == "descending_triangle"
    assert bull["conflict"]["breakout_dir"] == "down"


def test_neither_side_can_reach_the_score():
    """
    Both scoring paths in signals.py gate on `confirmed`. Clearing it on both
    is what makes an ambiguous chart contribute nothing, rather than
    contributing both sides of a contradiction.
    """
    out = patterns._mark_conflicts([
        _pat("bearish", "descending_triangle"),
        _pat("bullish", "falling_wedge"),
    ])
    scoreable = [p for p in out if p.get("confirmed")]
    assert scoreable == []


# ── What must NOT be treated as a conflict ──────────────────────────────────

def test_one_confirmed_pattern_is_left_alone():
    out = patterns._mark_conflicts([_pat("bullish", "falling_wedge")])
    assert out[0]["confirmed"] is True
    assert out[0]["status"] == "confirmed"
    assert out[0]["conflict"] is None


def test_two_confirmed_patterns_agreeing_are_left_alone():
    """
    Two structures pointing the SAME way is corroboration, not ambiguity. The
    scorer already refuses to count the same direction twice.
    """
    out = patterns._mark_conflicts([
        _pat("bullish", "falling_wedge"),
        _pat("bullish", "ascending_triangle"),
    ])
    assert all(p["confirmed"] is True for p in out)
    assert all(p["conflict"] is None for p in out)


def test_an_unconfirmed_opposite_is_not_a_conflict():
    """
    A forming bearish structure alongside a confirmed bullish one is ordinary —
    only two CONFIRMED opposites are a contradiction.
    """
    out = patterns._mark_conflicts([
        _pat("bullish", "falling_wedge"),
        _pat("bearish", "descending_triangle", confirmed=False, status="forming"),
    ])
    assert out[0]["confirmed"] is True and out[0]["conflict"] is None


def test_a_neutral_pattern_never_conflicts():
    """A symmetrical triangle has no direction to disagree with."""
    out = patterns._mark_conflicts([
        _pat("bullish", "falling_wedge"),
        _pat("neutral", "symmetrical_triangle"),
    ])
    assert out[0]["confirmed"] is True


def test_an_empty_list_is_fine():
    assert patterns._mark_conflicts([]) == []


# ── It has to survive the round trip ────────────────────────────────────────

def test_conflicted_is_a_status_the_log_accepts():
    """
    A status the allow-list does not know is silently DROPPED — the exact bug
    that lost every failed flag before it was found. An ambiguous chart is
    worth counting: it measures how often the detector produces something
    unreadable.
    """
    import pattern_store as ps
    assert "conflicted" in ps.OBSERVABLE_STATUSES
    from datetime import datetime, timezone
    rows = ps.build_events("btc", "1H", datetime.now(timezone.utc), [
        {"kind": "triangle", "status": "conflicted", "type": "falling_wedge",
         "direction": "bullish"}])
    assert len(rows) == 1 and rows[0]["status"] == "conflicted"


def test_the_sort_rank_knows_about_it():
    """
    "This chart is unreadable" is an urgent card and must not sort below the
    ones still forming.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend",
                            "patterns.py"), encoding="utf-8").read()
    rank = src[src.index("_rank = {"):src.index(chr(10), src.index("_rank = {"))]
    assert '"conflicted": 0' in rank
