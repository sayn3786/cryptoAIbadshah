"""
Paper account — a fixed-notional preview of what auto-executing the signals would
have made or lost. Pure, no orders. These pin the money math (P&L net of fees),
the fill discipline (cancelled orders never count), the equity/drawdown walk, and
the live-minimum flag that keeps a $1 paper size from masquerading as tradeable.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import paper_account as pa                                            # noqa: E402


# A monotonic clock so bare _row() calls get unique, NON-overlapping windows by
# default (each trade opens and closes before the next opens) — the intuitive
# "one after another" shape. Tests that care about overlap pass explicit
# entry_filled_at / closed_at.
_CLOCK = [1_000_000]


def _row(ret, *, status=None, symbol="BTC", direction="LONG", closed_at=None,
         generated_at=None, entry_filled_at=None):
    if status is None:
        status = "TP_HIT" if (ret or 0) > 0 else "SL_HIT"
    if closed_at is None and generated_at is None and entry_filled_at is None:
        t0 = _CLOCK[0]
        _CLOCK[0] += 1000
        generated_at = entry_filled_at = t0
        closed_at = t0 + 100
    elif closed_at is not None and generated_at is None and entry_filled_at is None:
        # explicit close only → open just before it, so the window has duration.
        generated_at = entry_filled_at = closed_at - 1
    return {"status": status, "realized_return_pct": ret, "symbol": symbol,
            "direction": direction, "closed_at": closed_at,
            "generated_at": generated_at, "entry_filled_at": entry_filled_at}


def test_pnl_is_notional_times_return_minus_round_trip_fees():
    # One +2% winner at $10 notional, 3.5 bps/side → gross $0.20, fee ~$0.007.
    acct = pa.build_paper_account([_row(2.0)], trade_size_usd=10.0,
                                  start_balance_usd=100.0, fee_bps=3.5)
    s = acct["summary"]
    assert abs(s["gross_pnl_usd"] - 0.20) < 1e-9
    assert abs(acct["config"]["round_trip_fee_usd"] - 0.007) < 1e-9
    assert abs(s["net_pnl_usd"] - (0.20 - 0.007)) < 1e-9
    assert abs(s["end_balance_usd"] - (100.0 + 0.193)) < 1e-9


def test_cancelled_orders_never_filled_are_excluded():
    rows = [_row(2.0), _row(-1.0), _row(None, status="CANCELLED")]
    acct = pa.build_paper_account(rows, trade_size_usd=10.0)
    assert acct["summary"]["filled_trades"] == 2        # the cancel does not count


def test_open_trades_are_not_counted():
    acct = pa.build_paper_account([_row(None, status="OPEN"), _row(1.0)],
                                  trade_size_usd=10.0)
    assert acct["summary"]["filled_trades"] == 1


def test_equity_curve_walks_oldest_first_and_tracks_drawdown():
    # Order given newest-first; the curve must read forward in time.
    rows = [_row(-3.0, closed_at=300), _row(2.0, closed_at=200), _row(1.0, closed_at=100)]
    acct = pa.build_paper_account(rows, trade_size_usd=100.0,
                                  start_balance_usd=100.0, fee_bps=0.0)
    curve = acct["equity_curve"]
    assert [c["closed_at"] for c in curve] == [100, 200, 300]      # forward
    # +1 → 101, +2 → 103 (peak), −3 → 100. Drawdown from 103 to 100 = 3.
    assert abs(curve[-1]["balance_usd"] - 100.0) < 1e-9
    assert abs(acct["summary"]["max_drawdown_usd"] - 3.0) < 1e-9


def test_a_dollar_trade_is_flagged_below_the_live_minimum():
    acct = pa.build_paper_account([_row(1.0)], trade_size_usd=1.0)
    lr = acct["live_readiness"]
    assert lr["size_meets_live_minimum"] is False
    assert lr["hyperliquid_min_order_usd"] == 10.0
    assert "reject" in lr["note"]


def test_a_ten_dollar_trade_meets_the_live_minimum():
    acct = pa.build_paper_account([_row(1.0)], trade_size_usd=10.0)
    assert acct["live_readiness"]["size_meets_live_minimum"] is True


def test_fees_dominate_at_tiny_notional():
    # At $1 the fee share of a small edge is large — the point of the whole read.
    small = pa.build_paper_account([_row(0.1)] * 10, trade_size_usd=1.0, fee_bps=3.5)
    assert small["summary"]["fees_as_pct_of_gross"] > 5.0    # fees eat real share


def test_empty_input_is_a_flat_account():
    acct = pa.build_paper_account([], trade_size_usd=1.0, start_balance_usd=500.0)
    s = acct["summary"]
    assert s["filled_trades"] == 0 and s["net_pnl_usd"] == 0.0
    assert s["end_balance_usd"] == 500.0 and s["win_rate_pct"] is None


# ── Item 6: capital modeling ─────────────────────────────────────────────────

import pytest                                                        # noqa: E402


@pytest.mark.parametrize("bad", [0, -100, float("nan"), float("inf"), None, "x"])
def test_rejects_bad_starting_balance(bad):
    with pytest.raises(pa.PaperAccountConfigError):
        pa.build_paper_account([_row(1.0)], start_balance_usd=bad)


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf"), None])
def test_rejects_bad_trade_size(bad):
    with pytest.raises(pa.PaperAccountConfigError):
        pa.build_paper_account([_row(1.0)], trade_size_usd=bad)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_rejects_bad_fee(bad):
    with pytest.raises(pa.PaperAccountConfigError):
        pa.build_paper_account([_row(1.0)], fee_bps=bad)


@pytest.mark.parametrize("bad", [0.5, 0, -2, float("nan")])
def test_rejects_leverage_below_one(bad):
    with pytest.raises(pa.PaperAccountConfigError):
        pa.build_paper_account([_row(1.0)], leverage=bad)


def test_a_trade_is_skipped_when_capital_cannot_cover_margin():
    # $100 notional at 1x needs $100 margin; a $10 account cannot open it.
    acct = pa.build_paper_account([_row(2.0), _row(1.0)], trade_size_usd=100.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    assert acct["summary"]["filled_trades"] == 0
    assert acct["summary"]["skipped_insufficient_capital"] == 2
    assert acct["summary"]["end_balance_usd"] == 10.0


def test_leverage_lets_a_small_account_open_a_larger_notional():
    # $100 notional at 10x needs only $10 margin — the account can trade.
    acct = pa.build_paper_account([_row(1.0)], trade_size_usd=100.0,
                                  start_balance_usd=10.0, fee_bps=0.0, leverage=10.0)
    assert acct["summary"]["filled_trades"] == 1
    assert acct["config"]["margin_per_trade_usd"] == 10.0


def test_balance_never_goes_negative_and_ruin_is_flagged():
    # A −200% glitch on a $10 notional would be −$20 on a $10 account: wiped out.
    acct = pa.build_paper_account([_row(-200.0), _row(5.0)], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    s = acct["summary"]
    assert s["end_balance_usd"] == 0.0            # never negative
    assert s["account_ruined"] is True
    assert s["ruined_at_trade"] == 1
    # The trade AFTER ruin cannot open — no capital.
    assert s["skipped_insufficient_capital"] >= 1
    assert s["filled_trades"] == 1


def test_notional_and_amount_at_risk_are_distinct():
    # entry 100, stop 95 → 5% of notional at risk. $100 notional → $5 at risk.
    row = {"status": "SL_HIT", "realized_return_pct": -1.0,
           "entry_price": 100.0, "stop_loss": 95.0, "symbol": "BTC",
           "direction": "LONG", "entry_filled_at": 0, "closed_at": 100}
    acct = pa.build_paper_account([row], trade_size_usd=100.0, fee_bps=0.0)
    s = acct["summary"]
    assert s["notional_per_trade_usd"] == 100.0
    assert s["peak_amount_at_risk_usd"] == 5.0    # 5% of notional, not the notional
    assert acct["ledger"][0]["amount_at_risk_usd"] == 5.0


def test_max_drawdown_pct_is_reported():
    rows = [_row(10.0, closed_at=1), _row(-5.0, closed_at=2)]
    acct = pa.build_paper_account(rows, trade_size_usd=100.0,
                                  start_balance_usd=1000.0, fee_bps=0.0)
    # peak 1100 after +10%*100=+10 → 1010; −5%*100 = −5 → 1005. dd = 5/1010.
    assert acct["summary"]["max_drawdown_pct"] > 0


def _ov(sym, ret, filled_at, closed_at):
    return {"status": "TP_HIT" if ret > 0 else "SL_HIT", "realized_return_pct": ret,
            "entry_filled_at": filled_at, "closed_at": closed_at,
            "symbol": sym, "direction": "LONG"}


def test_two_overlapping_trades_with_capital_for_only_one():
    # A [0,100] and B [50,150] overlap; $10 balance covers one $10-margin position.
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 50, 150)
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    s = acct["summary"]
    assert acct["concurrency_model"] == "concurrent_margin"
    assert s["filled_trades"] == 1
    assert s["skipped_insufficient_capital"] == 1
    assert s["actual_max_concurrent_positions"] == 1
    assert s["observed_max_concurrent_positions"] == 2          # they DO overlap in time


def test_two_overlapping_trades_with_sufficient_capital():
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 50, 150)
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=100.0, fee_bps=0.0)
    s = acct["summary"]
    assert s["filled_trades"] == 2 and s["skipped_insufficient_capital"] == 0
    assert s["actual_max_concurrent_positions"] == 2
    assert s["peak_exposure_usd"] == 20.0                       # two $10 positions held at once


def test_margin_is_released_after_a_trade_closes():
    # A [0,100] then B [200,300]: B opens AFTER A closes, so $10 covers both in turn.
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 200, 300)
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    s = acct["summary"]
    assert s["filled_trades"] == 2                              # released margin funded B
    assert s["actual_max_concurrent_positions"] == 1           # never both at once


def test_trades_opening_at_the_same_timestamp():
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 0, 100)                                 # same open instant
    both = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=100.0, fee_bps=0.0)
    assert both["summary"]["actual_max_concurrent_positions"] == 2
    one = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                 start_balance_usd=10.0, fee_bps=0.0)
    assert one["summary"]["filled_trades"] == 1                 # capital for only one
    assert one["summary"]["skipped_insufficient_capital"] == 1


def test_not_described_as_live_ready():
    acct = pa.build_paper_account([_row(1.0)], trade_size_usd=10.0)
    lr = acct["live_readiness"]
    assert lr["live_ready"] is False                            # never, while liq/funding unmodelled
    assert lr["concurrency_modelled"] is True                  # concurrency IS now modelled
    assert lr["funding_modelled"] is False
    assert lr["liquidation_modelled"] is False


# ── Reconciliation, chronology, and invariants ────────────────────────────────

def test_end_balance_reconciles_with_ledger_net_totals():
    rows = [_row(3.0), _row(-1.0), _row(2.5), _row(-4.0)]
    acct = pa.build_paper_account(rows, trade_size_usd=50.0,
                                  start_balance_usd=1000.0, fee_bps=3.5)
    s = acct["summary"]
    assert s["reconciles"] is True
    assert abs((1000.0 + s["ledger_net_total_usd"]) - s["end_balance_usd"]) < 1e-6
    # gross − fees == net, in aggregate.
    assert abs((s["gross_pnl_usd"] - s["fees_paid_usd"]) - s["net_pnl_usd"]) < 1e-6


def test_loss_and_ruin_reconcile_with_capped_gross():
    # A −300% glitch on a $10 notional is −$30, capped to the $20 account. Start
    # balance clears margin + fee so the trade opens, then the loss wipes it out.
    acct = pa.build_paper_account([_row(-300.0)], trade_size_usd=10.0,
                                  start_balance_usd=20.0, fee_bps=3.5)
    s = acct["summary"]
    assert s["account_ruined"] is True
    assert s["end_balance_usd"] == 0.0
    row = acct["ledger"][0]
    assert row["net_usd"] == -20.0                              # capped at equity, not −30
    assert abs((row["gross_usd"] - row["fee_usd"]) - row["net_usd"]) < 1e-6  # reconciles
    assert s["reconciles"] is True


def test_balance_never_negative_invariant_across_a_messy_cohort():
    rows = ([_row(-200.0)] + [_row(r) for r in (5.0, -3.0, 8.0, -50.0, 2.0)])
    acct = pa.build_paper_account(rows, trade_size_usd=20.0,
                                  start_balance_usd=30.0, fee_bps=3.5)
    for e in acct["equity_curve"]:
        assert e["balance_usd"] >= 0.0
    assert acct["summary"]["end_balance_usd"] >= 0.0


def test_entry_filled_at_is_preferred_over_generated_at():
    # generated_at is early, but the FILL is late — the position must open at the fill.
    a = {"status": "TP_HIT", "realized_return_pct": 1.0, "generated_at": 0,
         "entry_filled_at": 500, "closed_at": 600, "symbol": "BTC", "direction": "LONG"}
    # Another trade lives [100,400]: it overlaps generated_at(0) but NOT the fill(500).
    b = {"status": "TP_HIT", "realized_return_pct": 1.0, "entry_filled_at": 100,
         "closed_at": 400, "symbol": "ETH", "direction": "LONG"}
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    # Using entry_filled_at, A(500-600) and B(100-400) do NOT overlap → both fill on $10.
    assert acct["summary"]["filled_trades"] == 2
    assert acct["summary"]["actual_max_concurrent_positions"] == 1


def test_missing_or_invalid_timestamps_are_skipped_safely():
    good = _ov("BTC", 1.0, 0, 100)
    no_open = {"status": "TP_HIT", "realized_return_pct": 1.0, "closed_at": 100,
               "symbol": "ETH", "direction": "LONG"}                # no fill/generated
    reversed_ts = {"status": "TP_HIT", "realized_return_pct": 1.0,
                   "entry_filled_at": 200, "closed_at": 100,        # close before open
                   "symbol": "SOL", "direction": "LONG"}
    acct = pa.build_paper_account([good, no_open, reversed_ts],
                                  trade_size_usd=10.0, start_balance_usd=100.0)
    assert acct["summary"]["filled_trades"] == 1                   # only the good one
    assert acct["summary"]["skipped_invalid_timestamp"] == 2


# ── Codex review fixes: simultaneous-close ordering & the zero-equity boundary ─

def test_simultaneous_winner_and_loser_are_order_independent():
    # Two positions closing on the SAME candle: a +150% winner and a −250% loser
    # on $10 notionals from a $20 account. The loser alone would breach equity,
    # but the same-candle winner covers it — end balance must be $10 and NOT
    # ruined, regardless of the input row order (the P1 finding).
    winner = _ov("BTC", 150.0, 0, 100)
    loser = _ov("ETH", -250.0, 0, 100)
    for rows in ([winner, loser], [loser, winner]):
        acct = pa.build_paper_account(rows, trade_size_usd=10.0,
                                      start_balance_usd=20.0, fee_bps=0.0)
        s = acct["summary"]
        assert s["filled_trades"] == 2
        assert s["end_balance_usd"] == 10.0          # +15 then −25 from 20 → 10
        assert s["account_ruined"] is False
        assert s["reconciles"] is True


def test_a_close_consuming_exactly_all_equity_is_ruin():
    # $10 account, $10 notional, 0 fees, −100% → equity lands EXACTLY at 0. That
    # is a wipeout: account_ruined must be True, not a silent zero balance (P2).
    acct = pa.build_paper_account([_row(-100.0)], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    s = acct["summary"]
    assert s["end_balance_usd"] == 0.0
    assert s["account_ruined"] is True
    assert s["ruined_at_trade"] == 1
    assert s["reconciles"] is True


# ── Second Codex review: per-timestamp drawdown & unrounded reconciliation ────

def test_same_timestamp_closes_do_not_print_an_artificial_drawdown():
    # +100% and −100% on $10 notionals from $20, closing on the SAME candle. The
    # portfolio's timestamp-level equity is unchanged at $20, so there must be NO
    # drawdown from an intra-tick +$10 then −$10 wobble.
    winner = _ov("BTC", 100.0, 0, 100)
    loser = _ov("ETH", -100.0, 0, 100)
    acct = pa.build_paper_account([winner, loser], trade_size_usd=10.0,
                                  start_balance_usd=20.0, fee_bps=0.0)
    s = acct["summary"]
    assert s["end_balance_usd"] == 20.0
    assert s["max_drawdown_usd"] == 0.0
    assert s["max_drawdown_pct"] == 0.0
    # One equity-curve point for the shared candle, not two.
    assert len(acct["equity_curve"]) == 1
    assert acct["equity_curve"][0]["balance_usd"] == 20.0


def test_a_genuine_cross_candle_drawdown_is_still_reported():
    # Different candles: +10% then −20% → a real peak-to-trough drawdown.
    up = _ov("BTC", 10.0, 0, 100)
    down = _ov("ETH", -20.0, 200, 300)
    acct = pa.build_paper_account([up, down], trade_size_usd=100.0,
                                  start_balance_usd=1000.0, fee_bps=0.0)
    assert acct["summary"]["max_drawdown_usd"] > 0
    assert len(acct["equity_curve"]) == 2


def test_reconciliation_survives_many_fractional_trades():
    # 500 identical small trades with fractional fees: summing 6dp-rounded ledger
    # nets would drift past 1e-6, but reconciliation is on the unrounded total.
    rows = [_row(0.137) for _ in range(500)]
    acct = pa.build_paper_account(rows, trade_size_usd=7.31,
                                  start_balance_usd=100000.0, fee_bps=3.5)
    s = acct["summary"]
    assert s["filled_trades"] == 500
    assert s["reconciles"] is True
    assert abs((100000.0 + s["ledger_net_total_usd"]) - s["end_balance_usd"]) < 1e-2


# ── Third Codex review: aggregate stop-risk & fee-aware admission ─────────────

def _ov_risk(sym, ret, filled_at, closed_at, entry, stop):
    return {"status": "TP_HIT" if ret > 0 else "SL_HIT", "realized_return_pct": ret,
            "entry_filled_at": filled_at, "closed_at": closed_at,
            "entry_price": entry, "stop_loss": stop, "symbol": sym, "direction": "LONG"}


def test_peak_amount_at_risk_aggregates_concurrent_positions():
    # Two overlapping $100 positions, each risking 5% ($5) to its stop → the peak
    # portfolio stop risk is $10, not $5 (the largest single trade).
    a = _ov_risk("BTC", 1.0, 0, 100, 100.0, 95.0)
    b = _ov_risk("ETH", 1.0, 0, 100, 100.0, 95.0)
    acct = pa.build_paper_account([a, b], trade_size_usd=100.0,
                                  start_balance_usd=1000.0, fee_bps=0.0)
    assert acct["summary"]["actual_max_concurrent_positions"] == 2
    assert acct["summary"]["peak_amount_at_risk_usd"] == 10.0


def test_a_single_position_peak_risk_is_its_own():
    a = _ov_risk("BTC", 1.0, 0, 100, 100.0, 95.0)
    acct = pa.build_paper_account([a], trade_size_usd=100.0,
                                  start_balance_usd=1000.0, fee_bps=0.0)
    assert acct["summary"]["peak_amount_at_risk_usd"] == 5.0


def test_admission_accounts_for_the_entry_fee_at_the_boundary():
    # $20 account, two $10 positions at 1x → $20 margin exactly, but with a fee the
    # pair is unaffordable, so only one opens (checking margin alone would open both).
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 0, 100)
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=20.0, fee_bps=3.5)
    s = acct["summary"]
    assert s["filled_trades"] == 1
    assert s["skipped_insufficient_capital"] == 1


def test_zero_fee_boundary_still_opens_both():
    # With no fee, $20 funds two $10 positions exactly — the fee change must not
    # over-tighten the zero-fee case.
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 0, 100)
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=20.0, fee_bps=0.0)
    assert acct["summary"]["filled_trades"] == 2


# ── Fourth Codex review: decision-time open priority & cumulative fee reserve ─

def test_open_priority_is_decision_time_not_future_close_order():
    # Two simultaneous fills, capital for only one. Row order is reversed between
    # the two calls (as the store's closed_at-DESC ordering would vary); the SAME
    # trade must be funded both times — chosen by generated_at, not row order.
    early = {"status": "TP_HIT", "realized_return_pct": 5.0, "generated_at": 10,
             "entry_filled_at": 100, "closed_at": 200, "symbol": "AAA", "direction": "LONG"}
    late = {"status": "TP_HIT", "realized_return_pct": 5.0, "generated_at": 90,
            "entry_filled_at": 100, "closed_at": 150, "symbol": "ZZZ", "direction": "LONG"}
    out1 = pa.build_paper_account([early, late], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    out2 = pa.build_paper_account([late, early], trade_size_usd=10.0,
                                  start_balance_usd=10.0, fee_bps=0.0)
    # Both fund exactly one, and it's the EARLIER-published trade (AAA) both times.
    assert out1["summary"]["filled_trades"] == 1
    assert out2["summary"]["filled_trades"] == 1
    assert out1["ledger"][0]["symbol"] == "AAA"
    assert out2["ledger"][0]["symbol"] == "AAA"


def test_three_concurrent_positions_reserve_each_entry_fee():
    # $30.01 account, three simultaneous $10 positions at 1x with a fee: $30 margin
    # plus three entry fees exceeds the balance, so only two are funded.
    rows = [{"status": "TP_HIT", "realized_return_pct": 1.0, "generated_at": i,
             "entry_filled_at": 100, "closed_at": 200, "symbol": s, "direction": "LONG"}
            for i, s in enumerate(("AAA", "BBB", "CCC"))]
    acct = pa.build_paper_account(rows, trade_size_usd=10.0,
                                  start_balance_usd=30.01, fee_bps=3.5)
    s = acct["summary"]
    assert s["filled_trades"] == 2
    assert s["skipped_insufficient_capital"] == 1


# ── Fifth Codex review: split entry/exit fees ────────────────────────────────

def test_admission_reserves_only_the_entry_fee_not_the_exit_fee():
    # Two $10 positions at 1x, 3.5 bps: entry fee $0.0035 each → $20.007 needed at
    # entry. A $20.01 balance funds BOTH (the exit fee is paid later at close);
    # requiring the full round-trip up front would wrongly reject the second.
    a = _ov("BTC", 1.0, 0, 100)
    b = _ov("ETH", 1.0, 0, 100)
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=20.01, fee_bps=3.5)
    assert acct["summary"]["filled_trades"] == 2


def test_liquidated_position_retains_its_entry_fee():
    # A [0,100] ruins the account; B [0,200] is still open and gets liquidated at
    # B's close — but B's entry fee (paid when it opened) must be retained, not
    # dropped to zero. So fees_paid = A(entry+exit) + B(entry) = 3 fee-sides.
    a = {"status": "SL_HIT", "realized_return_pct": -300.0, "entry_filled_at": 0,
         "closed_at": 100, "symbol": "AAA", "direction": "LONG"}
    b = {"status": "TP_HIT", "realized_return_pct": 1.0, "entry_filled_at": 0,
         "closed_at": 200, "symbol": "BBB", "direction": "LONG"}
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0,
                                  start_balance_usd=21.0, fee_bps=3.5)
    s = acct["summary"]
    one_side = 10.0 * 3.5 / 10_000.0                  # $0.0035
    assert s["account_ruined"] is True
    assert abs(s["fees_paid_usd"] - one_side * 3) < 1e-9   # A entry+exit, B entry
    liq = [row for row in acct["ledger"] if row["liquidated"]]
    assert len(liq) == 1
    assert abs(liq[0]["fee_usd"] - one_side) < 1e-9        # B's entry fee, not 0
    assert s["reconciles"] is True


# ── Sixth Codex review: entry-fee debit is observed by the drawdown ──────────

def test_entry_fee_dip_is_recorded_in_drawdown_even_if_recovered():
    # A single trade: the entry fee dips equity at open, then a small profit fully
    # recovers it. Drawdown must observe the fee dip, not report zero.
    a = {"status": "TP_HIT", "realized_return_pct": 0.2, "entry_filled_at": 0,
         "closed_at": 100, "symbol": "BTC", "direction": "LONG"}
    acct = pa.build_paper_account([a], trade_size_usd=100.0,
                                  start_balance_usd=1000.0, fee_bps=50.0)  # 0.5%/side
    assert acct["summary"]["max_drawdown_usd"] > 0     # the entry-fee dip is seen
    assert acct["summary"]["reconciles"] is True
