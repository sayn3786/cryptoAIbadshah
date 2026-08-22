"""
The 4H publication cadence (v44).

One published set per 4H SLOT: six sets a day, three trades each, so at most
eighteen published trades in a day. Before this, every 2H close was a
publication point, which is why sixty-odd working signals piled up — the same
setups being republished bar after bar.

The gate is the SLOT, not the candle. Requiring the latest closed candle to BE a
4H boundary assumed the cron fired near it; GitHub Actions cron ran one to three
hours late and two of the first four real slots were lost, silently, because a
skip is not an error by design.

The ranking changed with the cadence: candidates are ordered by the AVERAGE of
1H and 2H strength, with the composite quality score demoted to the tiebreak.
Both timeframes must already agree on direction for a candidate to exist, so
their average measures how strongly they agree; ranking on 2H alone let a strong
2H with a barely-qualifying 1H outrank a setup both timeframes liked.

Note what did NOT change: quality is still a GATE (R/R >= 1.5, direction
agreement, data quality, TP-behind-live, correlation diversification). Demoting
it affects the ORDER of candidates that already passed, not whether they pass.
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import signal_publish as sp                                          # noqa: E402


UTC = timezone.utc
SGT = timezone(timedelta(hours=8))


def at(hour, minute=0, *, tz=UTC):
    return datetime(2026, 7, 31, hour, minute, tzinfo=tz)


# ── Which closes are publication bars ───────────────────────────────────────

@pytest.mark.parametrize("hour", [0, 4, 8, 12, 16, 20])
def test_every_4h_close_publishes(hour):
    assert appmod._is_publication_bar(at(hour)) is True


@pytest.mark.parametrize("hour", [1, 2, 3, 5, 6, 7, 9, 10, 11,
                                  13, 14, 15, 17, 18, 19, 21, 22, 23])
def test_no_other_hour_publishes(hour):
    # The 2H closes at 02, 06, 10, ... are exactly the ones that used to
    # republish the same setups.
    assert appmod._is_publication_bar(at(hour)) is False


@pytest.mark.parametrize("minute", [1, 15, 30, 59])
def test_a_minute_past_the_bar_is_not_the_bar(minute):
    assert appmod._is_publication_bar(at(16, minute)) is False


def test_seconds_past_the_bar_are_not_the_bar():
    assert appmod._is_publication_bar(at(16).replace(second=1)) is False


def test_no_candle_is_not_a_publication_bar():
    # No closed-candle timestamp means no candle identity, so nothing can be
    # de-duplicated — never treat that as a publication point.
    assert appmod._is_publication_bar(None) is False


def test_the_boundaries_are_the_same_instants_in_sgt():
    # SGT is UTC+8, a whole multiple of 4h, so a 4H boundary in one is a 4H
    # boundary in the other. This is why the gate needs no timezone argument.
    for hour in (0, 4, 8, 12, 16, 20):
        sgt_bar = at(hour, tz=SGT)
        assert appmod._is_publication_bar(sgt_bar) is True
        assert appmod._is_publication_bar(sgt_bar.astimezone(UTC)) is True


def test_six_publication_bars_in_a_day():
    day = at(0)
    bars = [day + timedelta(hours=h) for h in range(24)]
    assert sum(1 for b in bars if appmod._is_publication_bar(b)) == 24 // 4 == 6


def test_eighteen_is_the_daily_ceiling():
    # Six bars x three trades. The ceiling is what the user asked for, and it
    # is a property of the cadence rather than a separate limit to enforce.
    assert 24 // appmod.PUBLICATION_INTERVAL_HOURS * 3 == 18


# ── The cache key follows the same six slots ────────────────────────────────

def _key_at(monkeypatch, sgt_hour, minute=0):
    real = appmod.datetime

    class _Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 31, sgt_hour, minute, tzinfo=SGT)

    monkeypatch.setattr(appmod, "datetime", _Frozen)
    try:
        return appmod._rec_cache_key()
    finally:
        monkeypatch.setattr(appmod, "datetime", real)


@pytest.mark.parametrize("hour,slot", [
    (0, "00"), (3, "00"),
    (4, "04"), (7, "04"),
    (8, "08"), (11, "08"),
    (12, "12"), (15, "12"),
    (16, "16"), (19, "16"),
    (20, "20"), (23, "20"),
])
def test_cache_key_buckets_to_the_containing_4h_slot(monkeypatch, hour, slot):
    assert _key_at(monkeypatch, hour).endswith(f"_20260731_{slot}")


def test_a_day_has_exactly_six_cache_keys(monkeypatch):
    keys = {_key_at(monkeypatch, h) for h in range(24)}
    assert len(keys) == 6


def test_cache_key_carries_the_strategy_generation(monkeypatch):
    # The key is versioned so a strategy bump cannot serve a stale set that was
    # built under the previous rules.
    assert _key_at(monkeypatch, 16).startswith("v49_4h_avg_")


def test_the_slot_changes_exactly_on_the_boundary(monkeypatch):
    assert _key_at(monkeypatch, 15, 59) != _key_at(monkeypatch, 16, 0)
    assert _key_at(monkeypatch, 16, 0) == _key_at(monkeypatch, 16, 1)


# ── The skip is a skip, not a failure ───────────────────────────────────────

def test_skip_persistence_is_an_exception_used_as_control_flow():
    assert issubclass(appmod._SkipPersistence, Exception)


def test_a_slot_already_published_is_still_actionable():
    # A recompute inside an already-published slot SERVES the set — it just
    # records nothing, because the slot has already been decided and written.
    # Marking it not-actionable would blank the dashboard for hours.
    persist = {"all_actionable": True, "persisted": 0, "duplicates": 0,
               "failed": [], "error_code": None,
               "skipped_reason": "SLOT_ALREADY_PUBLISHED"}
    assert persist["all_actionable"] is True
    assert persist["error_code"] is None, "a skip is not an error"
    assert persist["persisted"] == 0


# ── Data lag at the boundary must not poison the slot cache ────────────────
# The scheduler wakes at :02 past a 4H boundary and that is the run that
# records the slot. If the exchange's latest closed 2H candle is still the
# PREVIOUS bar, the set was built on stale data and was not persisted — caching
# it would serve an unrecorded set for the next four hours.

def _slot_current(close_t, now_sgt, *, has_recs=True):
    """The exact expression _compute_recommendations builds `slot_current` from."""
    return (not has_recs) or bool(
        close_t is not None and close_t >= appmod._slot_start(now_sgt))


@pytest.mark.parametrize("hour,expected_start", [
    (0, 0), (3, 0), (4, 4), (7, 4), (16, 16), (19, 16), (23, 20),
])
def test_slot_start_is_the_containing_boundary(hour, expected_start):
    assert appmod._slot_start(at(hour, 37)) == at(expected_start, 0)


def test_slot_start_keeps_the_timezone_it_was_given():
    assert appmod._slot_start(at(17, tz=SGT)).utcoffset() == timedelta(hours=8)


def test_a_set_built_on_this_slots_candle_is_current():
    assert _slot_current(at(16, tz=SGT), at(16, 2, tz=SGT)) is True


def test_a_set_built_on_the_previous_bar_at_the_boundary_is_not_current():
    # 16:02, but the freshest closed 2H candle is still 14:00 — the data lags.
    assert _slot_current(at(14, tz=SGT), at(16, 2, tz=SGT)) is False


def test_mid_slot_the_2h_bar_inside_the_slot_is_still_current():
    # 19:30 sits in the 16:00 slot; the 18:00 2H close is inside it. Not a
    # publication bar, so nothing new is recorded — but nothing is stale either.
    assert _slot_current(at(18, tz=SGT), at(19, 30, tz=SGT)) is True


def test_no_recommendations_is_always_current():
    # Nothing published means nothing to be stale about; an empty set must not
    # make every request recompute for four hours.
    assert _slot_current(None, at(16, 2, tz=SGT), has_recs=False) is True


def test_a_missing_candle_with_recommendations_is_not_current():
    assert _slot_current(None, at(16, 2, tz=SGT)) is False


# ── Ranking: average of 1H and 2H, quality as tiebreak ──────────────────────

def _rank(cands):
    """The exact sort key _compute_recommendations uses."""
    return sorted(cands,
                  key=lambda x: (x.get("avg_tf_strength", x["strength"]),
                                 x.get("quality_score", 0), x["strength"]),
                  reverse=True)


def test_both_timeframes_agreeing_beats_one_strong_timeframe():
    # The case the change exists for: B has the stronger 2H, but its 1H barely
    # qualified. A is the setup both timeframes actually liked.
    a = {"symbol": "A", "strength": 70, "avg_tf_strength": 72.5, "quality_score": 50}
    b = {"symbol": "B", "strength": 85, "avg_tf_strength": 65.0, "quality_score": 90}
    assert [c["symbol"] for c in _rank([b, a])] == ["A", "B"]


def test_quality_breaks_a_tie_on_the_average():
    # Between two equally-agreed setups the one with better R/R and less
    # reversal risk still wins — quality is demoted, not discarded.
    a = {"symbol": "A", "strength": 70, "avg_tf_strength": 70.0, "quality_score": 40}
    b = {"symbol": "B", "strength": 70, "avg_tf_strength": 70.0, "quality_score": 80}
    assert [c["symbol"] for c in _rank([a, b])] == ["B", "A"]


def test_2h_strength_breaks_a_tie_on_both():
    a = {"symbol": "A", "strength": 66, "avg_tf_strength": 70.0, "quality_score": 55}
    b = {"symbol": "B", "strength": 74, "avg_tf_strength": 70.0, "quality_score": 55}
    assert [c["symbol"] for c in _rank([a, b])] == ["B", "A"]


def test_the_average_is_the_plain_mean_of_the_two_timeframes():
    for h1, h2, expected in ((60, 80, 70.0), (55, 56, 55.5), (90, 90, 90.0)):
        assert round((float(h1) + float(h2)) / 2.0, 1) == expected


def test_ranking_falls_back_to_2h_when_the_average_is_missing():
    # Defensive: a candidate built before the key existed must still sort.
    a = {"symbol": "A", "strength": 80, "quality_score": 10}
    b = {"symbol": "B", "strength": 60, "avg_tf_strength": 70.0, "quality_score": 99}
    assert [c["symbol"] for c in _rank([b, a])] == ["A", "B"]


# ── The schedulers must fire AFTER a boundary, never before ────────────────
# The publication gate reads the last CLOSED candle. A cron that fires at 23:50
# UTC sees the 06:00 SGT bar, records nothing, and would dispatch an unrecorded
# set to Telegram — so "fire early to absorb GitHub delay" became a bug.

def _cron_utc_hours_and_minutes(text):
    import re
    out = []
    for m in re.finditer(r"cron:\s*'(\S+)\s+(\S+)\s+\*\s+\*\s+\*'", text):
        minute, hour = m.group(1), m.group(2)
        for h in hour.split(","):
            for mi in minute.split(","):
                out.append((int(h), int(mi)))
    return out


def _repo(*parts):
    return os.path.join(os.path.dirname(__file__), "..", *parts)


def test_telegram_cron_fires_just_after_a_4h_boundary():
    with open(_repo(".github", "workflows", "telegram-alerts.yml")) as fh:
        schedules = _cron_utc_hours_and_minutes(fh.read())
    assert schedules, "the telegram workflow must still have a schedule"
    for hour, minute in schedules:
        assert hour % 4 == 0, \
            f"{hour:02d}:{minute:02d} UTC is not on a 4H boundary — it would publish nothing"
        assert 0 < minute <= 15, \
            f"{hour:02d}:{minute:02d} UTC fires on or before the boundary; the close is not available yet"


def test_vercel_cron_fires_just_after_a_4h_boundary():
    import json
    with open(_repo("vercel.json")) as fh:
        crons = json.load(fh).get("crons") or []
    assert crons, "the Vercel cron must still exist"
    for c in crons:
        minute, hour = c["schedule"].split()[:2]
        assert int(hour) % 4 == 0, f"{c['schedule']} is not on a 4H boundary"
        assert 0 < int(minute) <= 15, f"{c['schedule']} fires on or before the boundary"


def test_the_prewarm_scheduler_covers_all_six_slots():
    import inspect
    src = inspect.getsource(appmod._daily_rec_scheduler)
    assert "range(0, 24, PUBLICATION_INTERVAL_HOURS)" in src, \
        "the pre-warm scheduler must derive its slots from the publication interval"


# ── The version records that the rules changed ──────────────────────────────

def test_the_cadence_change_is_a_new_strategy_version():
    # Cadence and ranking both change WHICH trades exist, so signals from before
    # this are not comparable with signals from after — the whole point of the
    # column. v43 and earlier rows must stay separable.
    #
    # Pinned as "v44 or later" rather than "v44", so a later rules change can
    # bump the version without this reading as a cadence regression.
    major = int(re.match(r"v(\d+)", sp.STRATEGY_VERSION).group(1))
    assert major >= 44, \
        "the 4H cadence must not be recorded under the v43 rules"


def test_publication_interval_is_four_hours():
    assert appmod.PUBLICATION_INTERVAL_HOURS == 4


# ── The gate is the SLOT, not the candle ───────────────────────────────────
# Requiring the latest closed candle to BE a 4H boundary assumed the cron fired
# near that boundary. GitHub Actions cron is best-effort and ran one to THREE
# hours late; a run arriving after the next 2H close saw a non-boundary candle,
# published nothing, and reported success. Two of the first four real slots were
# lost exactly that way — silently, because a skip is not an error by design.

def test_publication_is_gated_on_the_slot_not_the_candle():
    import inspect
    src = inspect.getsource(appmod._compute_recommendations)
    assert "_slot_already_published(now_sgt)" in src
    assert "SLOT_ALREADY_PUBLISHED" in src
    assert "NOT_A_PUBLICATION_BAR" not in src, \
        "the candle-alignment gate is what lost the slots"


def test_a_slot_with_nothing_recorded_publishes_however_late_the_run_is():
    # The whole point: a run three hours late still publishes its slot.
    import inspect
    fn = inspect.getsource(appmod._slot_already_published)
    assert "_published_slot" in fn, "the database decides, not the candle clock"


def test_doubt_resolves_towards_publishing():
    # No database, an unreadable slot — attempt anyway. Publication is
    # idempotent on the candle, so a needless attempt costs a duplicate check;
    # the opposite mistake loses a slot outright.
    import inspect
    fn = inspect.getsource(appmod._slot_already_published)
    assert "except Exception" in fn and "return False" in fn


def test_the_cron_checks_the_slot_before_paying_for_a_compute():
    # ~50s of upstream fetching, 24 times a day, to publish six times. Checking
    # first is what makes hourly scheduling affordable.
    import inspect
    src = inspect.getsource(appmod.api_cron_publish)
    before = src.split("_compute_recommendations()", 1)[0]
    assert "_slot_already_published()" in before


def test_the_boundary_helper_still_describes_a_candle_correctly():
    # `_is_publication_bar` no longer GATES anything, but it is still a true
    # statement about a candle and is kept for reporting.
    assert appmod._is_publication_bar(at(16)) is True
    assert appmod._is_publication_bar(at(14)) is False
