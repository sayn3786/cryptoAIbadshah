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
    assert rec["key"].startswith("falling_wedge:bullish:")


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


# ── reconcile ───────────────────────────────────────────────────────────────

def test_a_dropped_pattern_is_resurfaced_when_still_live():
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    additions, updated = pp.reconcile([], [rec], cs, 192.0, "TAO", "1D",
                                      BASE + 46 * DAY)
    assert len(additions) == 1
    assert additions[0]["display_only"] is True
    assert len(updated) == 1                       # still tracked


def test_a_resolved_pattern_is_dropped_from_the_store():
    rec = _rec()
    cs = [_c(i, 280, 260, 276) for i in range(3)]   # hit target
    additions, updated = pp.reconcile([], [rec], cs, 276.0, "TAO", "1D",
                                      BASE + 44 * DAY)
    assert additions == [] and updated == []


def test_a_fresh_detection_is_not_duplicated_by_the_tracked_copy():
    """When the detector still surfaces it, the tracked copy must not double it."""
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    fresh = pp.to_display_pattern(rec, cs, 192.0)
    fresh = {**fresh, "display_only": False, "tracked": False}   # a fresh detection
    additions, updated = pp.reconcile([fresh], [rec], cs, 192.0, "TAO", "1D",
                                      BASE + 46 * DAY)
    assert additions == []                          # already on the card
    assert len(updated) == 1                        # still tracked


def test_the_tracked_copy_dedupes_against_its_own_fresh_detection():
    """
    On the day a wedge is both freshly detected AND tracked, it must appear
    once, not twice. The tracked copy's rail values are taken at the last pivot
    — exactly where the fresh detector reports them — so `_same_structure`
    matches and the duplicate is dropped.
    """
    import patterns
    rec = _rec()
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    disp = pp.to_display_pattern(rec, cs, 192.0)
    # A fresh detection reports upper_now/lower_now at the last pivot.
    fresh = {**disp, "upper_now": rec["upper_line"][-1]["price"],
             "lower_now": rec["lower_line"][-1]["price"],
             "display_only": False, "tracked": False}
    assert patterns._same_structure(disp, fresh), \
        "the tracked copy must match its own fresh detection"
    additions, _ = pp.reconcile([fresh], [rec], cs, 192.0, "TAO", "1D",
                                BASE + 46 * DAY)
    assert additions == []


def test_a_fresh_confirmation_is_stored_for_later():
    cs = [_c(i, 200, 188, 192) for i in range(5)]
    fresh = _pattern()
    additions, updated = pp.reconcile([fresh], [], cs, 192.0, "TAO", "1D",
                                      BASE + 46 * DAY)
    assert len(updated) == 1                        # captured for future re-fits
    assert updated[0]["key"].startswith("falling_wedge:bullish:")


def test_the_store_is_capped():
    cs = [_c(i, 200, 188, 192) for i in range(3)]
    records = [pp.record_from_pattern(_pattern(break_off=b), "TAO", "1D", BASE)
               for b in range(41, 41 + pp.MAX_TRACKED + 4)]
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
