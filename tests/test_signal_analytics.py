"""
Read-off analytics over closed signals — calibration, excursions, target reach,
timing and the fill funnel.

The point of these tests is that each number means what it says: a strength
bucket that does not clear MIN_BUCKET is flagged thin rather than mined for a
trend; a win rate that fails to rise with strength is called out as
mis-calibration; the winners' 75th-percentile heat is the stop floor and not a
recommendation; and a TP rung is "reached" only when the excursion actually
touched it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_analytics as an                                        # noqa: E402


def _row(ret, *, strength=None, mfe=None, mae=None, status=None, tps=None,
         generated_at=None, closed_at=None, direction="LONG", symbol="BTC"):
    if status is None:
        status = "TP_HIT" if (ret or 0) > 0 else "SL_HIT"
    row = {"status": status, "realized_return_pct": ret, "direction": direction,
           "symbol": symbol, "confidence_score": strength,
           "mfe_pct": mfe, "mae_pct": mae,
           "generated_at": generated_at, "closed_at": closed_at}
    if tps is not None:
        row["snapshot"] = {"indicator_values": {"take_profit_pcts": tps}}
    return row


# ── Strength calibration ─────────────────────────────────────────────────────

def _tier(rep, label):
    for b in rep["buckets"]:
        if b["tier"] == label:
            return b
    raise AssertionError(f"{label} not in report")


def test_rising_win_rate_with_strength_is_called_calibrated():
    rows = ([_row(-1.0, strength=40) for _ in range(3)]      # Moderate: 40% win
            + [_row(2.0, strength=40) for _ in range(2)]
            + [_row(2.0, strength=60) for _ in range(4)]     # Strong: 80% win
            + [_row(-1.0, strength=60)])
    rep = an.strength_calibration(rows)
    assert _tier(rep, "Moderate")["win_rate_pct"] == 40.0
    assert _tier(rep, "Strong")["win_rate_pct"] == 80.0
    assert rep["monotonic"] is True
    assert "right order" in rep["verdict"]


def test_a_score_that_does_not_sort_is_flagged_miscalibrated():
    rows = ([_row(2.0, strength=40) for _ in range(4)]       # Moderate: 80% win
            + [_row(-1.0, strength=40)]
            + [_row(-1.0, strength=60) for _ in range(4)]     # Strong: 20% win
            + [_row(2.0, strength=60)])
    rep = an.strength_calibration(rows)
    assert rep["monotonic"] is False
    assert "does NOT rise" in rep["verdict"]


def test_thin_tiers_are_not_used_to_judge_calibration():
    # Two Strong losses must not make the score look mis-calibrated on their own.
    rows = ([_row(2.0, strength=40) for _ in range(6)]        # Moderate powered
            + [_row(-1.0, strength=60), _row(-1.0, strength=60)])  # Strong: thin
    rep = an.strength_calibration(rows)
    assert _tier(rep, "Strong")["thin"] is True
    assert rep["monotonic"] is None          # only one populated tier
    assert "at least two tiers" in rep["verdict"]


# ── Excursions ───────────────────────────────────────────────────────────────

def test_candidate_stop_floor_is_the_winners_p75_adverse_excursion():
    winners = [_row(2.0, mae=m) for m in (-0.5, -1.0, -1.5, -2.0, -4.0)]
    rep = an.excursion_report(winners)
    # p75 of [-4,-2,-1.5,-1,-0.5] sorted asc → interpolated near -1.0
    assert rep["winners"]["n"] == 5
    assert rep["candidate_stop_floor_pct"] == rep["winners"]["mae"]["p75"]
    assert -1.6 <= rep["candidate_stop_floor_pct"] <= -0.9


def test_excursions_split_winners_from_losers():
    rows = [_row(2.0, mfe=3.0, mae=-0.5), _row(-1.0, mfe=0.2, mae=-3.0)]
    rep = an.excursion_report(rows)
    assert rep["winners"]["mfe"]["median"] == 3.0
    assert rep["losers"]["mae"]["median"] == -3.0


# ── Target reach ─────────────────────────────────────────────────────────────

def test_target_reach_counts_only_rungs_the_excursion_touched():
    # MFE 4.0 with a [2,5,9] ladder reaches TP1 only.
    rows = [_row(2.0, mfe=4.0, tps=[2.0, 5.0, 9.0]),
            _row(2.0, mfe=6.0, tps=[2.0, 5.0, 9.0])]     # reaches TP1 and TP2
    rep = an.target_reach(rows)
    assert rep["considered"] == 2
    assert rep["reached_tp1"] == 2
    assert rep["reached_tp2"] == 1
    assert rep["reached_tp3"] == 0
    assert rep["reach_rate_tp3"] == 0.0


def test_target_reach_uses_magnitudes_for_shorts():
    # A SHORT's tps may be stored negative; reach compares magnitudes.
    rows = [_row(2.0, mfe=3.0, tps=[-2.0, -5.0, -9.0], direction="SHORT")]
    rep = an.target_reach(rows)
    assert rep["reached_tp1"] == 1 and rep["reached_tp2"] == 0


def test_rows_without_a_ladder_are_skipped():
    rep = an.target_reach([_row(2.0, mfe=4.0)])   # no tps
    assert rep["considered"] == 0
    assert rep["reach_rate_tp1"] is None


# ── Timing & fill funnel ─────────────────────────────────────────────────────

def test_durations_are_split_by_outcome():
    rows = [_row(2.0, generated_at=0, closed_at=3_600_000),        # 60 min win
            _row(-1.0, generated_at=0, closed_at=7_200_000)]       # 120 min loss
    rep = an.timing_report(rows)
    assert rep["median_minutes_to_win"] == 60.0
    assert rep["median_minutes_to_loss"] == 120.0


def test_win_rate_is_grouped_into_4h_publication_slots():
    # 13:00 UTC → the 12 slot. A whole number of days since epoch IS UTC midnight.
    at_1300 = 20450 * 86_400_000 + 13 * 3_600_000
    rows = [_row(2.0, generated_at=at_1300), _row(-1.0, generated_at=at_1300)]
    rep = an.timing_report(rows)
    assert "12" in rep["by_publication_slot"]
    assert rep["by_publication_slot"]["12"]["n"] == 2


def test_fill_funnel_counts_never_filled_and_expired():
    rows = [_row(2.0), _row(-1.0),
            _row(None, status="CANCELLED"), _row(None, status="EXPIRED")]
    rep = an.fill_funnel(rows)
    assert rep["never_filled"] == 1 and rep["never_filled_pct"] == 25.0
    assert rep["expired"] == 1
    assert rep["filled"] == 3


# ── Composition ──────────────────────────────────────────────────────────────

def test_build_analytics_composes_every_section():
    rows = [_row(2.0, strength=60, mfe=3.0, mae=-0.5, tps=[2.0, 5.0, 9.0],
                 generated_at=0, closed_at=3_600_000)]
    rep = an.build_analytics(rows, strategy_version="v47_4h_avg")
    assert rep["strategy_version"] == "v47_4h_avg"
    for key in ("strength_calibration", "excursions", "target_reach",
                "timing", "fill_funnel", "cohort", "caveats"):
        assert key in rep
    assert rep["cohort"]["closed_rows"] == 1


def test_empty_input_does_not_raise():
    rep = an.build_analytics([])
    assert rep["cohort"]["closed_rows"] == 0
    assert rep["cohort"]["win_rate_pct"] is None
    assert rep["strength_calibration"]["monotonic"] is None
