"""
When will there be enough closed v45 trades to run the postmortem?

v45 reset the sample to zero. This projects the two milestones from the rate
trades are actually closing at, so the dates come from data. The tests hold the
arithmetic and, more importantly, its honesty: the rate is measured from the
first publication (so the lag before the first close is not hidden), cancelled
and open orders are not counted as closed trades, a green sample is flagged soft
rather than projected to a false date, and `now` is always explicit so the
projection is reproducible.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import postmortem_report as pm                                      # noqa: E402


DAY = 86_400_000
T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
T0_MS = int(T0.timestamp() * 1000)


def _sig(*, gen_day, close_day=None, status="TP_HIT", ret=None):
    """A signal published `gen_day` days after T0, optionally closed later."""
    row = {"status": status,
           "generated_at": T0_MS + int(gen_day * DAY),
           "realized_return_pct": ret}
    if close_day is not None:
        row["closed_at"] = T0_MS + int(close_day * DAY)
    return row


def _now(day):
    return T0 + timedelta(days=day)


# ── Counts and exclusions ───────────────────────────────────────────────────

def test_only_analysable_closes_are_counted():
    rows = [
        _sig(gen_day=0, close_day=1, status="TP_HIT", ret=2.0),   # win
        _sig(gen_day=0, close_day=1, status="SL_HIT", ret=-2.0),  # loss
        _sig(gen_day=0, close_day=1, status="EXPIRED", ret=None), # expired, unpriced
        _sig(gen_day=0, close_day=1, status="CANCELLED", ret=None),  # not a trade
        _sig(gen_day=0, status="OPEN", ret=None),                 # not closed
    ]
    c = pm.cadence_report(rows, now=_now(5))["counts"]
    assert c["analysable_closed"] == 3
    assert c["wins"] == 1 and c["losses"] == 1 and c["expired"] == 1
    assert c["cancelled"] == 1 and c["still_open"] == 1


def test_a_positive_sl_hit_counts_as_a_win():
    rows = [_sig(gen_day=0, close_day=1, status="SL_HIT", ret=1.0)]
    assert pm.cadence_report(rows, now=_now(3))["counts"]["wins"] == 1


# ── The rate is measured from first publish, lag included ────────────────────

def test_rate_is_closes_per_day_since_first_publish():
    # 4 closed, first published at day 0, evaluated at day 4 → 1.0/day, even
    # though the first close was at day 2. The lag is real elapsed time.
    rows = [_sig(gen_day=0, close_day=2, status="TP_HIT", ret=1.0),
            _sig(gen_day=0, close_day=3, status="SL_HIT", ret=-1.0),
            _sig(gen_day=1, close_day=3, status="TP_HIT", ret=1.0),
            _sig(gen_day=1, close_day=4, status="SL_HIT", ret=-1.0)]
    rep = pm.cadence_report(rows, now=_now(4))
    assert rep["elapsed_days_since_first_publish"] == 4.0
    assert rep["closes_per_day"] == 1.0


def test_the_rate_does_not_hide_the_pre_first_close_lag():
    """Measuring from the first CLOSE instead would inflate the rate."""
    rows = [_sig(gen_day=0, close_day=4, status="TP_HIT", ret=1.0),
            _sig(gen_day=0, close_day=4, status="SL_HIT", ret=-1.0)]
    rep = pm.cadence_report(rows, now=_now(4))
    # 2 closes over 4 elapsed days = 0.5/day, NOT 2 over the 0-day close window.
    assert rep["closes_per_day"] == 0.5


# ── Projections ─────────────────────────────────────────────────────────────

def test_projection_reaches_the_targets_at_the_observed_rate():
    # 6 closed over 6 days = 1/day. 15 target → 9 more → 9 days out.
    rows = [_sig(gen_day=0, close_day=i, status="TP_HIT", ret=1.0)
            for i in range(1, 7)]
    rep = pm.cadence_report(rows, now=_now(6), targets=(15, 30))
    p15 = _proj(rep, 15)
    assert p15["remaining"] == 9
    assert p15["eta_days"] == 9.0
    assert p15["eta_date"][:10] == "2026-08-16"      # T0(Aug1)+day15 = Aug16
    assert _proj(rep, 30)["eta_days"] == 24.0


def test_a_reached_target_says_so():
    rows = [_sig(gen_day=0, close_day=i % 5 + 1, status="TP_HIT", ret=1.0)
            for i in range(16)]
    p = _proj(pm.cadence_report(rows, now=_now(6), targets=(15,)), 15)
    assert p["reached"] is True and p["remaining"] == 0


# ── Honesty: soft and empty ─────────────────────────────────────────────────

def test_an_early_run_flags_its_projection_soft():
    """Two days in with two closes is too green to promise a date."""
    rows = [_sig(gen_day=0, close_day=1, status="TP_HIT", ret=1.0),
            _sig(gen_day=0, close_day=2, status="SL_HIT", ret=-1.0)]
    rep = pm.cadence_report(rows, now=_now(2))
    assert rep["projection_is_soft"] is True
    assert any("SOFT" in n or "soft" in n for n in rep["notes"])


def test_a_mature_run_is_not_soft():
    rows = [_sig(gen_day=0, close_day=i, status="TP_HIT", ret=1.0)
            for i in range(1, 8)]
    assert pm.cadence_report(rows, now=_now(8))["projection_is_soft"] is False


def test_no_closes_yet_gives_no_rate_and_no_eta():
    rows = [_sig(gen_day=0, status="OPEN", ret=None) for _ in range(3)]
    rep = pm.cadence_report(rows, now=_now(1))
    assert rep["closes_per_day"] is None
    assert _proj(rep, 15)["eta_date"] is None
    assert _proj(rep, 15)["remaining"] == 15


def test_an_empty_book_does_not_raise():
    rep = pm.cadence_report([], now=_now(0))
    assert rep["counts"]["published"] == 0
    assert rep["closes_per_day"] is None


# ── Powered flag mirrors the report ─────────────────────────────────────────

def test_powered_needs_both_cohorts():
    rows = ([_sig(gen_day=0, close_day=1, status="TP_HIT", ret=1.0)
             for _ in range(5)]
            + [_sig(gen_day=0, close_day=1, status="SL_HIT", ret=-1.0)
               for _ in range(5)])
    assert pm.cadence_report(rows, now=_now(4))["powered_for_discriminators"] is True


def test_all_wins_is_not_powered():
    rows = [_sig(gen_day=0, close_day=1, status="TP_HIT", ret=1.0)
            for _ in range(10)]
    assert pm.cadence_report(rows, now=_now(4))["powered_for_discriminators"] is False


# ── now is explicit, never the wall clock ───────────────────────────────────

def test_now_must_be_supplied():
    with pytest.raises(ValueError):
        pm.cadence_report([_sig(gen_day=0, close_day=1, ret=1.0)], now=None)


def test_now_accepts_iso_and_epoch_and_datetime():
    rows = [_sig(gen_day=0, close_day=2, status="TP_HIT", ret=1.0)]
    a = pm.cadence_report(rows, now=_now(4))
    b = pm.cadence_report(rows, now="2026-08-05T00:00:00+00:00")
    c = pm.cadence_report(rows, now=int(_now(4).timestamp() * 1000))
    assert a["closes_per_day"] == c["closes_per_day"]
    assert b["now"].startswith("2026-08-05")


def test_the_same_rows_and_now_are_reproducible():
    rows = [_sig(gen_day=0, close_day=i, status="TP_HIT", ret=1.0)
            for i in range(1, 6)]
    assert pm.cadence_report(rows, now=_now(6)) == pm.cadence_report(rows, now=_now(6))


# ── helper ──────────────────────────────────────────────────────────────────

def _proj(rep, target):
    for p in rep["projections"]:
        if p["target"] == target:
            return p
    raise AssertionError(f"no projection for {target}")
