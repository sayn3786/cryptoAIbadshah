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


def _row(ret, *, status=None, symbol="BTC", direction="LONG", closed_at=None):
    if status is None:
        status = "TP_HIT" if (ret or 0) > 0 else "SL_HIT"
    return {"status": status, "realized_return_pct": ret, "symbol": symbol,
            "direction": direction, "closed_at": closed_at}


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
           "direction": "LONG", "closed_at": 1}
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


def test_overlapping_trades_are_observed_but_walked_sequentially():
    # Two trades whose [generated, closed] windows overlap.
    a = {"status": "TP_HIT", "realized_return_pct": 1.0, "generated_at": 0,
         "closed_at": 100, "symbol": "BTC", "direction": "LONG"}
    b = {"status": "TP_HIT", "realized_return_pct": 1.0, "generated_at": 50,
         "closed_at": 150, "symbol": "ETH", "direction": "LONG"}
    acct = pa.build_paper_account([a, b], trade_size_usd=10.0)
    assert acct["concurrency_model"] == "sequential_only"
    assert acct["summary"]["observed_max_concurrent_positions"] == 2
    assert acct["summary"]["peak_exposure_usd"] == 10.0            # sequential: one at a time
    assert acct["summary"]["peak_exposure_if_concurrent_usd"] == 20.0


def test_not_described_as_live_ready():
    acct = pa.build_paper_account([_row(1.0)], trade_size_usd=10.0)
    lr = acct["live_readiness"]
    assert lr["live_ready"] is False
    assert lr["concurrency_modelled"] is False
    assert lr["funding_modelled"] is False
    assert lr["liquidation_modelled"] is False
