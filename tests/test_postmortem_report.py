"""
The postmortem must read the losers honestly.

The user's ask was specific: when a trade hits its stop, what did we already
know at decision time that we could have used? The trap is a risk flag that
looks damning before losses until you notice it fires just as often before wins
— so every feature is scored as its rate in losers AGAINST its rate in winners,
and only the difference counts.

These tests hold the arithmetic and the honesty:

  * a flag common to both cohorts is NOT flagged, however common;
  * a flag genuinely concentrated in losers IS, once the sample can support it;
  * a small sample is called under-powered rather than mined for a finding;
  * "we didn't record it" is counted as unknown, never as "all clear";
  * a TP1-then-breakeven trade that ended net-positive is a WIN, not a loss,
    even though its status is SL_HIT — the scale-out distinction the whole
    tracker exists to preserve;
  * the report states, every time, that it does not tune anything.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import postmortem_report as pm                                      # noqa: E402


def _row(outcome_return, *, status=None, symbol="BTC", direction="LONG",
         snapshot=None, mfe_pct=None, sl_pct=None):
    """A closed-signal row. `status` defaults to match the return sign."""
    if status is None:
        status = "TP_HIT" if outcome_return and outcome_return > 0 else "SL_HIT"
    snap = dict(snapshot or {})
    if sl_pct is not None:
        snap.setdefault("stop_loss_pct", sl_pct)
    return {"status": status, "realized_return_pct": outcome_return,
            "symbol": symbol, "direction": direction, "mfe_pct": mfe_pct,
            "snapshot": {"indicator_values": snap}}


def _flagged(n, feature_snap, ret):
    return [_row(ret, snapshot=feature_snap) for _ in range(n)]


# ── Outcome classification ──────────────────────────────────────────────────

def test_a_positive_return_is_a_win_even_when_status_is_sl_hit():
    """
    TP1 banked 50%, remainder stopped at breakeven, net +1%. Status is SL_HIT,
    but it made money — reading status alone would file a winner as a loss.
    """
    assert pm.classify_outcome(
        {"status": "SL_HIT", "realized_return_pct": 1.0}) == "win"


def test_a_negative_return_is_a_loss():
    assert pm.classify_outcome(
        {"status": "SL_HIT", "realized_return_pct": -3.0}) == "loss"


def test_a_cancelled_order_is_neither_win_nor_loss():
    assert pm.classify_outcome(
        {"status": "CANCELLED", "realized_return_pct": None}) == "cancelled"


def test_an_open_trade_is_excluded():
    assert pm.classify_outcome(
        {"status": "OPEN", "realized_return_pct": None}) == "open"


def test_a_flat_close_is_a_scratch():
    assert pm.classify_outcome(
        {"status": "CLOSED", "realized_return_pct": 0.0}) == "scratch"


# ── Cohort accounting ───────────────────────────────────────────────────────

def test_cancelled_and_open_are_kept_out_of_the_rates():
    rows = ([_row(2.0) for _ in range(3)] + [_row(-2.0) for _ in range(2)]
            + [{"status": "CANCELLED", "realized_return_pct": None}]
            + [{"status": "OPEN", "realized_return_pct": None}])
    rep = pm.build_report(rows)
    c = rep["cohort"]
    assert c["wins"] == 3 and c["losses"] == 2
    assert c["cancelled_excluded"] == 1 and c["still_open_excluded"] == 1
    assert c["win_rate_pct"] == 60.0


def test_expectancy_is_the_mean_realised_return():
    rows = [_row(3.0), _row(3.0), _row(-3.0)]
    assert rep_exp(rows) == pytest.approx(1.0, abs=1e-6)


def rep_exp(rows):
    return pm.build_report(rows)["cohort"]["expectancy_pct"]


# ── Discriminators: the core honesty test ───────────────────────────────────

def test_a_flag_common_to_winners_and_losers_is_not_flagged():
    """
    The trap. `structure_fought` fires before every trade here — losers AND
    winners. It is not a discriminator, and must not be reported as one.
    """
    snap = {"structure_adjustment": -5}
    rows = _flagged(8, snap, -2.0) + _flagged(8, snap, 2.0)
    rep = pm.build_report(rows)
    d = _feature(rep, "structure_fought_the_trade")
    assert d["lift"] == pytest.approx(1.0, abs=0.01)
    assert d["over_represented_in_losses"] is False


def test_a_flag_concentrated_in_losers_is_flagged():
    losers = _flagged(8, {"stop_liquidity": {"blocked": True}}, -2.0)
    winners = [_row(2.0, snapshot={"stop_liquidity": {"blocked": False}})
               for _ in range(8)]
    rep = pm.build_report(losers + winners)
    d = _feature(rep, "stop_sat_in_a_sweep_zone")
    assert d["loser_rate"] == 1.0 and d["winner_rate"] == 0.0
    assert d["lift_unbounded"] is True and d["lift"] is None
    assert d["over_represented_in_losses"] is True


def test_a_partial_concentration_reports_the_lift():
    # Flag on 6 of 8 losers, 2 of 8 winners → lift 3.0.
    losers = (_flagged(6, {"structure_adjustment": -5}, -2.0)
              + _flagged(2, {"structure_adjustment": 5}, -2.0))
    winners = (_flagged(2, {"structure_adjustment": -5}, 2.0)
               + _flagged(6, {"structure_adjustment": 5}, 2.0))
    rep = pm.build_report(losers + winners)
    d = _feature(rep, "structure_fought_the_trade")
    assert d["loser_rate"] == 0.75 and d["winner_rate"] == 0.25
    assert d["lift"] == pytest.approx(3.0, abs=0.01)
    assert d["over_represented_in_losses"] is True


def test_discriminators_are_ranked_strongest_first():
    # A partial-lift flag (3.0) and a bounded weaker one, so the ordering has
    # something to sort. structure_fought: 6/8 losers vs 2/8 winners → lift 3.0.
    losers = (_flagged(6, {"structure_adjustment": -5}, -2.0)
              + _flagged(2, {"structure_adjustment": 5}, -2.0))
    winners = (_flagged(2, {"structure_adjustment": -5}, 2.0)
               + _flagged(6, {"structure_adjustment": 5}, 2.0))
    ds = pm.build_report(losers + winners)["discriminators"]
    ranked = [(d["lift_unbounded"], d["lift"] if d["lift"] is not None else -1)
              for d in ds]
    assert ranked == sorted(ranked, reverse=True)


def test_an_unbounded_flag_outranks_a_finite_one():
    losers = (_flagged(8, {"stop_liquidity": {"blocked": True},  # unbounded
                           "structure_adjustment": -5}, -2.0))
    winners = (_flagged(3, {"stop_liquidity": {"blocked": False},
                            "structure_adjustment": -5}, 2.0)     # structure finite
               + _flagged(5, {"stop_liquidity": {"blocked": False},
                              "structure_adjustment": 5}, 2.0))
    ds = pm.build_report(losers + winners)["discriminators"]
    assert ds[0]["feature"] == "stop_sat_in_a_sweep_zone"
    assert ds[0]["lift_unbounded"] is True


# ── Power / small samples ───────────────────────────────────────────────────

def test_a_thin_sample_is_under_powered_and_flags_nothing():
    """One loser at 100% for a flag is not evidence. Do not dress it as one."""
    rows = ([_row(-2.0, snapshot={"stop_liquidity": {"blocked": True}})]
            + [_row(2.0) for _ in range(3)])
    rep = pm.build_report(rows)
    assert rep["powered"] is False
    assert all(not d["over_represented_in_losses"] for d in rep["discriminators"])


def test_the_power_note_states_what_is_missing():
    rep = pm.build_report([_row(-2.0), _row(2.0)])
    assert "under-powered" in rep["power_note"]
    assert str(pm.MIN_COHORT) in rep["power_note"]


def test_enough_in_both_cohorts_is_powered():
    rep = pm.build_report(_flagged(5, {}, -2.0) + _flagged(5, {}, 2.0))
    assert rep["powered"] is True


# ── Unknown is not absent ───────────────────────────────────────────────────

def test_a_missing_field_counts_as_unknown_not_all_clear():
    """
    A row predating a snapshot field must not be read as 'the flag was off'. It
    is unknown, and drops out of the rate denominator entirely.
    """
    losers = ([_row(-2.0, snapshot={"stop_liquidity": {"blocked": True}})
               for _ in range(4)] + [_row(-2.0, snapshot={})])  # 5th: no field
    winners = _flagged(5, {"stop_liquidity": {"blocked": False}}, 2.0)
    rep = pm.build_report(losers + winners)
    d = _feature(rep, "stop_sat_in_a_sweep_zone")
    assert d["coverage"]["losers_unknown"] == 1
    assert d["coverage"]["losers_known"] == 4
    assert d["loser_rate"] == 1.0            # 4 of 4 KNOWN, not 4 of 5


def test_unknown_everywhere_yields_a_null_rate_not_a_zero():
    rows = _flagged(6, {}, -2.0) + _flagged(6, {}, 2.0)   # no stop_liquidity
    d = _feature(pm.build_report(rows), "stop_sat_in_a_sweep_zone")
    assert d["loser_rate"] is None and d["winner_rate"] is None
    assert d["over_represented_in_losses"] is False


# ── Stop placement vs signal quality ────────────────────────────────────────

def test_losers_that_first_ran_in_favour_point_at_the_stop():
    """
    Every loser first ran +1.5R your way (mfe 6% on a 4% stop) before reversing.
    That is a stop-placement problem, and the verdict must say so.
    """
    losers = [_row(-4.0, mfe_pct=6.0, sl_pct=4.0) for _ in range(6)]
    winners = [_row(3.0, mfe_pct=3.0, sl_pct=4.0) for _ in range(6)]
    sp = pm.build_report(losers + winners)["stop_placement"]
    assert sp["share"] == 1.0
    assert "STOP" in sp["verdict"]


def test_losers_that_went_straight_against_point_at_the_signal():
    losers = [_row(-4.0, mfe_pct=0.2, sl_pct=4.0) for _ in range(6)]
    winners = [_row(3.0, mfe_pct=3.0, sl_pct=4.0) for _ in range(6)]
    sp = pm.build_report(losers + winners)["stop_placement"]
    assert sp["share"] == 0.0
    assert "SIGNAL" in sp["verdict"]


def test_no_excursion_data_is_admitted_not_guessed():
    losers = [_row(-4.0) for _ in range(6)]               # no mfe_pct
    sp = pm.build_report(losers + _flagged(6, {}, 2.0))["stop_placement"]
    assert sp["share"] is None
    assert "no excursion data" in sp["verdict"]


# ── Breakdowns ──────────────────────────────────────────────────────────────

def test_by_symbol_and_direction_split_the_book():
    rows = [_row(2.0, symbol="BTC", direction="LONG"),
            _row(-2.0, symbol="BTC", direction="LONG"),
            _row(-2.0, symbol="ETH", direction="SHORT")]
    rep = pm.build_report(rows)
    assert rep["by_symbol"]["BTC"]["n"] == 2
    assert rep["by_symbol"]["ETH"]["losses"] == 1
    assert rep["by_direction"]["SHORT"]["win_rate_pct"] == 0.0


# ── The standing rule ───────────────────────────────────────────────────────

def test_the_report_always_says_it_does_not_tune():
    caveats = " ".join(pm.build_report([_row(-2.0)])["caveats"]).lower()
    assert "never changes live parameters" in caveats
    assert "correlation, not causation" in caveats


def test_an_empty_book_does_not_raise():
    rep = pm.build_report([])
    assert rep["cohort"]["analysable"] == 0
    assert rep["cohort"]["win_rate_pct"] is None
    assert rep["powered"] is False


def test_the_strategy_version_is_echoed():
    assert pm.build_report([], strategy_version="v45_4h_avg")["strategy_version"] \
        == "v45_4h_avg"


def test_opposed_btc_direction_reads_the_btc_conflict_key():
    """
    build_snapshot stores this on market_context as `btc_conflict`. The reader
    used to look for a `conflict` key that never existed, so every row read
    'unknown' and the flag was blind. With the key aligned, a conflict
    concentrated in losers is seen and can become a finding.
    """
    def _r(ret, conflict):
        status = "TP_HIT" if ret > 0 else "SL_HIT"
        return {"status": status, "realized_return_pct": ret, "symbol": "BTC",
                "direction": "LONG", "mfe_pct": None,
                "snapshot": {"market_context": {"btc_conflict": conflict}}}

    rows = ([_r(-1.0, True) for _ in range(5)]       # losers all fought BTC
            + [_r(2.0, False) for _ in range(6)])     # winners all aligned
    feat = _feature(pm.build_report(rows), "opposed_btc_direction")
    assert feat["coverage"]["losers_known"] == 5      # no longer 'unknown'
    assert feat["coverage"]["winners_known"] == 6
    assert feat["loser_rate"] == 1.0 and feat["winner_rate"] == 0.0
    assert feat["over_represented_in_losses"] is True


def test_btc_conflict_none_is_unknown_not_all_clear():
    """A row whose btc_conflict was never recorded stays unknown, not False."""
    row = {"status": "SL_HIT", "realized_return_pct": -1.0, "symbol": "BTC",
           "direction": "LONG", "mfe_pct": None,
           "snapshot": {"market_context": {"btc_conflict": None}}}
    assert pm._btc_conflict(row) is None


# ── helpers ─────────────────────────────────────────────────────────────────

def _feature(report, name):
    for d in report["discriminators"]:
        if d["feature"] == name:
            return d
    raise AssertionError(f"{name} not in report")
