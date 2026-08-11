"""
A confirmed wedge stays on the card until it actually resolves.

The triangle/wedge detector is stateless — it re-fits from the newest pivots
every load, so a confirmed breakout vanishes the moment a retest prints a fresh
pivot and the rails re-fit away, even though price never hit target and never
failed (seen on TAO 1D). This tracks a confirmed breakout forward by its fixed
lines and levels until it hits target, fails, or expires at the apex.

Two things are load-bearing and tested here:

  * The re-evaluation is honest — it resolves on a real target hit, a real close
    back through the level, a give-back of the whole move, or reaching the apex;
    and it keeps showing otherwise.
  * It is DISPLAY ONLY — every re-surfaced pattern is tagged `display_only`, and
    a separate test proves the scorer skips those, so nothing here changes what
    trades or touches the v45 freeze.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pattern_persist as pp                                          # noqa: E402


BASE = 1_767_268_800_000
DAY = 86_400_000


def _pattern(*, direction="bullish", break_off=41, target=275.0,
             type_="falling_wedge"):
    """A confirmed detected wedge, rails falling over 40 daily bars."""
    start, end = BASE, BASE + 40 * DAY
    return {
        "type": type_, "label": "Falling Wedge", "direction": direction,
        "status": "confirmed", "confirmed": True,
        "break_ts": BASE + break_off * DAY, "target": target,
        "converge_pct": 90.0, "breakout_dir": "up" if direction == "bullish" else "down",
        "breakout_volume": {"level": "normal", "ratio": 1.2},
        "upper_line": [{"timestamp": start, "price": 210.0},
                       {"timestamp": end, "price": 190.0}],
        "lower_line": [{"timestamp": start, "price": 185.0},
                       {"timestamp": end, "price": 182.0}],
    }


def _c(i, hi, lo, close):
    return {"timestamp": BASE + (41 + i) * DAY, "high": hi, "low": lo, "close": close}


# ── record_from_pattern ─────────────────────────────────────────────────────

def test_a_confirmed_pattern_becomes_a_trackable_record():
    rec = pp.record_from_pattern(_pattern(), "TAO", "1D", BASE + 41 * DAY)
    assert rec is not None
    assert rec["direction"] == "bullish"
    assert rec["break_level"] is not None and rec["fail_level"] is not None
    assert rec["apex_ts"] is not None
    # Keyed on the STRUCTURE, so a later re-fit cannot spawn a second record.
    assert rec["key"] == "falling_wedge:bullish"
    # A textbook target date and a void deadline are captured.
    assert rec["target_eta_ts"] is not None
    assert rec["apex_ts"] is not None


def test_the_two_textbook_dates_are_independent_projections():
    """
    The apex and the measured-move-in-time ETA are two INDEPENDENT dates —
    neither caps the other. The ETA is the raw formation-time projection
    (breakout + formation width), even when that lands past the apex; the
    pattern then expires at whichever date comes first (checked separately in
    the reevaluate tests).
    """
    rec = pp.record_from_pattern(_pattern(), "TAO", "1D", BASE + 41 * DAY)
    assert rec["target_eta_ts"] is not None and rec["apex_ts"] is not None
    # The ETA is the uncapped formation-time projection: break + (break - start).
    assert rec["target_eta_ts"] == rec["break_ts"] + (rec["break_ts"] - rec["pattern_start_ts"])
    assert rec["target_eta_ts"] >= rec["break_ts"], "the ETA is after the breakout"
    # In this fixture the rails converge well before the measured-move window
    # elapses, so the ETA lands PAST the apex — proving no cap is applied.
    assert rec["target_eta_ts"] > rec["apex_ts"]


def test_the_pattern_expires_at_whichever_date_comes_first():
    """Past the earlier of {apex, ETA} with no target hit → expired."""
    rec = _rec()
    first = min(rec["apex_ts"], rec["target_eta_ts"])
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    # Just before the earlier deadline it still holds.
    assert pp.reevaluate(rec, cs, 192.0, int(first) - DAY)["state"] == "live"
    # At/after the earlier deadline, with target not reached, it expires.
    assert pp.reevaluate(rec, cs, 192.0, int(first) + DAY)["state"] == "expired"


def test_reaching_the_target_wins_over_the_deadline():
    """A target hit resolves even once the earlier deadline has lapsed."""
    rec = _rec()
    first = min(rec["apex_ts"], rec["target_eta_ts"])
    cs = [_c(i, 280, 260, 276) for i in range(5)]
    assert pp.reevaluate(rec, cs, 276.0, int(first) + DAY)["state"] == "target_hit"


def test_the_display_carries_the_target_and_void_dates():
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(3)]
    d = pp.to_display_pattern(rec, cs, 192.0)
    assert d["target_eta_ts"] == rec["target_eta_ts"]
    assert d["expiry_ts"] == rec["apex_ts"]


def test_the_break_level_is_the_broken_rail_the_fail_level_the_other():
    rec = pp.record_from_pattern(_pattern(), "TAO", "1D", BASE + 41 * DAY)
    # Up-break clears the UPPER rail, so break_level (upper) sits above fail_level.
    assert rec["break_level"] > rec["fail_level"]


def test_a_forming_or_unconfirmed_pattern_is_not_stored():
    for bad in ({**_pattern(), "status": "forming", "confirmed": False},
                {**_pattern(), "direction": "neutral"},
                {**_pattern(), "target": None},
                {**_pattern(), "break_ts": None}):
        assert pp.record_from_pattern(bad, "TAO", "1D", BASE) is None


# ── reevaluate: the four terminal states + live ─────────────────────────────

def _rec():
    return pp.record_from_pattern(_pattern(), "TAO", "1D", BASE + 41 * DAY)


def test_holding_the_breakout_side_stays_live():
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    assert pp.reevaluate(_rec(), cs, 192.0, BASE + 46 * DAY)["state"] == "live"


def test_reaching_the_target_resolves():
    cs = [_c(i, 280, 260, 276) for i in range(5)]
    assert pp.reevaluate(_rec(), cs, 276.0, BASE + 46 * DAY)["state"] == "target_hit"


def test_a_short_target_is_a_downward_hit():
    rec = pp.record_from_pattern(_pattern(direction="bearish", target=150.0),
                                 "TAO", "1D", BASE + 41 * DAY)
    cs = [_c(i, 160, 148, 149) for i in range(3)]
    assert pp.reevaluate(rec, cs, 149.0, BASE + 44 * DAY)["state"] == "target_hit"


def test_closing_back_through_the_fail_level_fails():
    rec = _rec()
    cs = [_c(0, 200, 188, 192), _c(1, 193, 178, rec["fail_level"] - 1)]
    v = pp.reevaluate(rec, cs, rec["fail_level"] - 1, BASE + 42 * DAY)
    assert v["state"] == "failed" and v["failed_ts"] is not None


def test_giving_back_the_whole_move_fails():
    rec = _rec()
    brk = rec["break_level"]
    # Ran to a high peak, then collapsed below the give-back floor.
    cs = [_c(0, brk + 60, brk, brk + 55), _c(1, brk + 50, brk - 60, brk - 55)]
    assert pp.reevaluate(rec, cs, brk - 55, BASE + 42 * DAY)["state"] == "failed"


def test_a_routine_retest_does_not_count_as_a_give_back():
    """A pullback to the level is healthy, not a failure — the buffer protects it."""
    rec = _rec()
    brk = rec["break_level"]
    cs = [_c(0, brk + 8, brk - 2, brk + 5)]      # small move, small pullback
    assert pp.reevaluate(rec, cs, brk + 1, BASE + 42 * DAY)["state"] == "live"


def test_reaching_the_apex_expires():
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(3)]
    assert pp.reevaluate(rec, cs, 192.0, int(rec["apex_ts"]) + DAY)["state"] == "expired"


def test_an_old_failure_expires_rather_than_lingering():
    rec = _rec()
    cs = ([_c(0, 193, 178, rec["fail_level"] - 1)]
          + [_c(i, 200, 188, 192) for i in range(1, 12)])   # failed long ago
    assert pp.reevaluate(rec, cs, 192.0, BASE + 53 * DAY)["state"] == "expired"


# ── to_display_pattern ──────────────────────────────────────────────────────

def test_a_tracked_pattern_is_tagged_display_only():
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    d = pp.to_display_pattern(rec, cs, 192.0)
    assert d["display_only"] is True and d["tracked"] is True
    assert d["confirmed"] is True and d["status"] == "confirmed"
    assert d["label"] == "Falling Wedge" and d["target"] == 275.0
    # The timeframe must carry through, else the card's TF pill reads "undefined".
    assert d["timeframe"] == "1D"


def test_a_tracked_failure_is_not_confirmed():
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(3)]
    d = pp.to_display_pattern(rec, cs, 192.0, failed_ts=BASE + 43 * DAY)
    assert d["status"] == "failed" and d["confirmed"] is False


def test_the_display_pattern_carries_a_retest_read():
    rec = _rec()
    cs = [_c(0, 200, 188, 192), _c(1, 195, 189, rec["break_level"])]  # at the level
    d = pp.to_display_pattern(rec, cs, rec["break_level"], failed_ts=None)
    assert d["retest"] is not None


# ── reconcile: freeze, hold until resolved, suppress re-fits ────────────────

def test_a_dropped_pattern_is_held_when_still_live():
    """The detector stopped surfacing it, but it hasn't resolved — keep showing it."""
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    display, updated = pp.reconcile([], [rec], cs, 192.0, "TAO", "1D",
                                    BASE + 46 * DAY)
    assert len(display) == 1
    assert display[0]["display_only"] is True and display[0]["tracked"] is True
    assert len(updated) == 1                        # still tracked


def test_a_confirmed_pattern_is_frozen_and_does_not_re_measure():
    """
    THE requirement: once confirmed, the target must not drift. A later re-fit
    of the same structure (different rails and target) is suppressed; the frozen
    original is what shows.
    """
    original = _pattern(target=287.0)               # break_off=41
    rec = pp.record_from_pattern(original, "TAO", "1D", BASE + 41 * DAY)
    cs = [_c(i, 205, 190, 196) for i in range(5)]
    refit = _pattern(target=220.0, break_off=43)     # same type+direction, new fit
    display, updated = pp.reconcile([refit], [rec], cs, 196.0, "TAO", "1D",
                                    BASE + 46 * DAY)
    assert len(display) == 1, "the re-fit must not appear alongside the frozen one"
    assert display[0]["target"] == 287.0, "the target must stay frozen at 287"
    assert len(updated) == 1 and updated[0]["target"] == 287.0


def test_reaching_target_shows_it_reached_once_then_drops():
    rec = _rec()
    cs = [_c(i, 280, 260, 276) for i in range(3)]    # price at/over target
    display, updated = pp.reconcile([], [rec], cs, 276.0, "TAO", "1D",
                                    BASE + 44 * DAY)
    assert len(display) == 1 and display[0]["status"] == "target_hit"
    assert display[0]["target_reached"] is True
    assert updated == []                             # no longer tracked
    # Next load: gone.
    again, _ = pp.reconcile([], updated, cs, 276.0, "TAO", "1D", BASE + 45 * DAY)
    assert again == []


def test_an_invalidation_shows_failed_then_drops():
    rec = _rec()
    cs = [_c(0, 200, 188, 192), _c(1, 193, 178, rec["fail_level"] - 1)]  # closed back through
    display, updated = pp.reconcile([], [rec], cs, rec["fail_level"] - 1,
                                    "TAO", "1D", BASE + 43 * DAY)
    assert len(display) == 1 and display[0]["status"] == "failed"


def test_a_fresh_re_fit_of_a_tracked_structure_is_suppressed():
    """A fresh detection of the SAME type+direction as a tracked one is dropped."""
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    fresh = _pattern(break_off=44)                   # same falling_wedge:bullish
    display, updated = pp.reconcile([fresh], [rec], cs, 192.0, "TAO", "1D",
                                    BASE + 46 * DAY)
    assert len(display) == 1                         # only the frozen one
    assert display[0]["tracked"] is True


def test_a_different_structure_still_shows_alongside():
    """A genuinely different structure (other direction) is NOT suppressed."""
    rec = _rec()                                     # falling_wedge:bullish
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    other = _pattern(direction="bearish", type_="rising_wedge")
    display, _ = pp.reconcile([other], [rec], cs, 192.0, "TAO", "1D",
                              BASE + 46 * DAY)
    labels = {(d.get("type"), d.get("direction")) for d in display}
    assert ("falling_wedge", "bullish") in labels
    assert ("rising_wedge", "bearish") in labels


def test_a_fresh_confirmation_is_stored_for_later():
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    fresh = _pattern()
    display, updated = pp.reconcile([fresh], [], cs, 192.0, "TAO", "1D",
                                    BASE + 46 * DAY)
    assert len(updated) == 1                         # captured, and now frozen
    assert updated[0]["key"] == "falling_wedge:bullish"


def test_the_store_is_capped():
    # Distinct structures (type x direction) — keyed separately, so several can
    # be tracked at once and the cap must still bound them.
    cs = [_c(i, 200, 188, 192) for i in range(3)]
    types = ("falling_wedge", "rising_wedge", "ascending_triangle",
             "descending_triangle", "symmetrical_triangle")
    records = []
    for t in types:
        for d in ("bullish", "bearish"):
            records.append(pp.record_from_pattern(
                _pattern(type_=t, direction=d), "TAO", "1D", BASE))
    records = [r for r in records if r]
    assert len(records) > pp.MAX_TRACKED, "need more than the cap to test it"
    _, updated = pp.reconcile([], records, cs, 192.0, "TAO", "1D", BASE + 60 * DAY)
    assert len(updated) <= pp.MAX_TRACKED


# ── The scoring guard ───────────────────────────────────────────────────────

def test_the_scorer_skips_display_only_patterns():
    """
    THE guard that keeps this out of the v45 freeze. A display_only pattern
    must contribute nothing to the score; a real one still does.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend",
                            "signals.py"), encoding="utf-8").read()
    # Both triangle consumers must skip display_only.
    assert src.count('display_only') >= 2
    assert 'if t.get("display_only"):' in src
    assert 'not _pat.get("display_only")' in src


def test_a_display_only_pattern_does_not_move_the_score():
    """
    The behavioural proof, not just a grep: appending a strong confirmed
    display_only wedge to an analysis must leave direction, strength and score
    byte-identical. If this ever fails, the feature has leaked into scoring and
    the v45 freeze is contaminated.
    """
    import copy
    import math
    import random

    import candle_analysis as ca
    import signals

    b = 1_767_268_800_000
    rnd = random.Random(3)
    px, candles = 100.0, []
    for i in range(200):
        px *= (1 + rnd.gauss(0.0005 * math.sin(i / 11), 0.012))
        candles.append({"timestamp": b + i * DAY, "open": px, "high": px * 1.01,
                        "low": px * 0.99, "close": px, "volume": 1000})
    analysis = ca.build_candle_analysis(candles, "1D", "TEST")
    base = signals.generate_signal(copy.deepcopy(analysis))

    a2 = copy.deepcopy(analysis)
    a2["triangle_patterns"] = list(a2.get("triangle_patterns") or []) + [{
        "type": "falling_wedge", "label": "Falling Wedge", "direction": "bullish",
        "status": "confirmed", "confirmed": True, "display_only": True,
        "tracked": True, "break_ts": candles[-2]["timestamp"], "target": px * 1.3,
        "breakout_dir": "up", "breakout_volume": {"level": "strong", "ratio": 2.0},
        "retest": None, "upper_line": [], "lower_line": []}]
    after = signals.generate_signal(a2)

    assert (base.get("direction"), base.get("strength"), base.get("score")) == \
           (after.get("direction"), after.get("strength"), after.get("score"))


def test_the_persisted_pattern_is_only_wired_on_the_interactive_path():
    """
    KV must not be on the 60s publish scan. The reconcile is invoked from
    api_analysis, never from build_analysis or _compute_recommendations.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()
    assert "_with_tracked_patterns" in src
    import re
    build = src[src.index("def build_analysis("):src.index("def api_analysis(")]
    assert "_with_tracked_patterns" not in build
    assert "pattern_persist" not in build


# ── KV round-trip via the local fallback ────────────────────────────────────

def test_records_survive_a_save_and_load(tmp_path, monkeypatch):
    import kv
    monkeypatch.setattr(kv, "_FILE", str(tmp_path / "kv.json"))
    monkeypatch.setattr(kv, "_KV_URL", "")          # force the file fallback
    rec = _rec()
    pp.save("TAO", "1D", "test", [rec])
    back = pp.load("TAO", "1D", "test")
    assert len(back) == 1 and back[0]["key"] == rec["key"]


def test_load_is_empty_and_silent_when_nothing_is_stored(tmp_path, monkeypatch):
    import kv
    monkeypatch.setattr(kv, "_FILE", str(tmp_path / "none.json"))
    monkeypatch.setattr(kv, "_KV_URL", "")
    assert pp.load("NOPE", "1D", "test") == []
