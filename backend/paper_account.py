"""
Paper account — what auto-executing the published signals would have made or lost.

Every published signal is already tracked to an outcome with a weighted realised
return (the postmortem feed). A paper account is a pure function over those closed
rows: put a fixed notional on each filled trade, subtract round-trip taker fees,
and walk the equity forward. No keys, no orders, no exchange — a truthful preview
of a live bot sized at ``trade_size_usd`` before a single real dollar moves.

Honest by construction:
  * Only FILLED trades count. A cancelled (never-filled) order made no money and
    lost none — it is excluded, exactly as a real account would show nothing.
  * ``realized_return_pct`` is the SAME weighted return the postmortem scores, so
    the paper P&L cannot flatter the strategy relative to what actually happened.
  * Fees are modelled per side (entry + exit) on the notional. Funding is NOT
    modelled — perps charge/earn it every 8h and we do not store per-trade
    holding funding, so the paper P&L is slightly optimistic on trades held long
    enough to pay funding. Flagged, never hidden.

PURE. No database, no network, no clock. Reporting only: this never places an
order — it says what one would have done. The live executor is a separate,
explicitly-armed module.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from postmortem_report import classify_outcome

__all__ = ["build_paper_account", "PaperAccountConfigError",
           "HYPERLIQUID_MIN_ORDER_USD", "DEFAULT_FEE_BPS"]

# Hyperliquid's minimum perp order value. A live order under this is REJECTED, so
# a paper size below it is fine for simulation but cannot be traded live as-is.
HYPERLIQUID_MIN_ORDER_USD = 10.0

# Taker fee per side, in basis points (0.035% = 3.5 bps). A round trip pays it
# twice — once to enter, once to exit — so a full trade costs ~7 bps of notional.
DEFAULT_FEE_BPS = 3.5


def _f(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class PaperAccountConfigError(ValueError):
    """A starting balance, trade size, fee or leverage that cannot be simulated."""


def _require_finite(value, name, *, positive=False, non_negative=False, minimum=None):
    v = _f(value)
    if v is None:
        raise PaperAccountConfigError(f"{name} must be a finite number, got {value!r}")
    import math
    if not math.isfinite(v):
        raise PaperAccountConfigError(f"{name} must be finite, got {value!r}")
    if positive and v <= 0:
        raise PaperAccountConfigError(f"{name} must be > 0, got {v}")
    if non_negative and v < 0:
        raise PaperAccountConfigError(f"{name} must be >= 0, got {v}")
    if minimum is not None and v < minimum:
        raise PaperAccountConfigError(f"{name} must be >= {minimum}, got {v}")
    return v


def _risk_fraction(row) -> Optional[float]:
    """|entry − stop| / entry — the share of NOTIONAL genuinely at risk to the
    stop, distinct from the notional itself. None when entry/stop are missing."""
    e = _f(row.get("entry_price") or row.get("entry"))
    s = _f(row.get("stop_loss") or row.get("sl"))
    if e is None or s is None or e <= 0:
        return None
    frac = abs(e - s) / e
    import math
    return frac if math.isfinite(frac) else None


def _max_concurrent(intervals) -> int:
    """Peak number of trades live at once, from (open, close) epoch pairs. A sweep
    line over the endpoints; opens before closes at a tie count as overlapping."""
    events = []
    for a, b in intervals:
        if a is None or b is None or b < a:
            continue
        events.append((a, 1))
        events.append((b, -1))
    if not events:
        return 0
    events.sort(key=lambda x: (x[0], -x[1]))     # opens (+1) before closes (−1) on a tie
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def build_paper_account(
    rows: Sequence[Dict[str, Any]],
    *,
    trade_size_usd: float = 1.0,
    start_balance_usd: float = 1000.0,
    fee_bps: float = DEFAULT_FEE_BPS,
    leverage: float = 1.0,
    strategy_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Walk a fixed-notional paper account over closed signal rows (oldest first).

    ``rows`` are closed-signal dicts (``list_closed_with_snapshots`` /
    ``list_signals`` shape): each needs ``status`` and ``realized_return_pct``;
    ``entry_price``/``stop_loss`` give the amount at risk, and ``closed_at`` /
    ``generated_at`` / ``symbol`` / ``direction`` enrich the ledger.

    Each filled trade puts ``trade_size_usd`` of NOTIONAL on the book at
    ``leverage``, so it ties up ``notional / leverage`` of capital as margin. A
    trade is SKIPPED when the balance cannot cover that margin (counted as
    ``skipped_insufficient_capital``), the balance is never allowed below zero,
    and once a loss would wipe it out the account is marked ruined and opens
    nothing further. P&L is ``notional × realised_return% − round-trip fee``.

    SEQUENTIAL ONLY: the walk holds one position at a time. It reports the peak
    number of trades that were actually live simultaneously (from the
    timestamps) so the reader can see where the sequential model understates the
    capital a concurrent book would need — but it does not simulate concurrency,
    liquidation or funding, and says so. Raises PaperAccountConfigError on a
    non-finite/zero/negative balance, a non-finite/negative size or fee, or
    leverage below 1.
    """
    start_balance = _require_finite(start_balance_usd, "start_balance_usd", positive=True)
    size = _require_finite(trade_size_usd, "trade_size_usd", positive=True)
    fee_bps_v = _require_finite(fee_bps, "fee_bps", non_negative=True)
    lev = _require_finite(leverage, "leverage", minimum=1.0)

    fee_frac = fee_bps_v / 10_000.0
    round_trip_fee = size * fee_frac * 2.0            # entry + exit, taker both sides
    margin_per_trade = size / lev

    def _when(r):
        return (_f(r.get("closed_at")) or _f(r.get("generated_at")) or 0.0)
    ordered = sorted(rows or [], key=_when)

    balance = start_balance
    peak = balance
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0
    wins = losses = filled = 0
    skipped_insufficient_capital = 0
    gross_pnl = fees_paid = 0.0
    risk_sum = 0.0
    peak_amount_at_risk = 0.0
    account_ruined = False
    ruined_at_trade = None
    curve: List[Dict[str, Any]] = []
    ledger: List[Dict[str, Any]] = []
    fill_intervals: List[tuple] = []

    for r in ordered:
        outcome = classify_outcome(r)
        if outcome in ("cancelled", "open"):
            continue                                  # never filled / not resolved
        ret = _f(r.get("realized_return_pct"))
        if ret is None:
            continue                                  # terminal but unpriced

        # Capital check BEFORE opening: a ruined or under-margined account cannot
        # take the trade. Never open on capital that is not there.
        if account_ruined or balance + 1e-9 < margin_per_trade:
            skipped_insufficient_capital += 1
            continue

        gross = size * (ret / 100.0)
        net = gross - round_trip_fee
        proposed = balance + net
        # The account can never go silently negative: a loss larger than the
        # balance wipes it out and the run is ruined from here.
        if proposed <= 0:
            net = -balance                            # lose no more than you have
            proposed = 0.0
            if not account_ruined:
                account_ruined = True
                ruined_at_trade = filled + 1

        balance = proposed
        filled += 1
        gross_pnl += gross
        fees_paid += round_trip_fee
        if net > 0:
            wins += 1
        elif net < 0:
            losses += 1
        peak = max(peak, balance)
        dd = peak - balance
        max_drawdown_usd = max(max_drawdown_usd, dd)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, dd / peak * 100.0)

        rf = _risk_fraction(r)
        amt_at_risk = (size * rf) if rf is not None else None
        if amt_at_risk is not None:
            risk_sum += amt_at_risk
            peak_amount_at_risk = max(peak_amount_at_risk, amt_at_risk)

        fill_intervals.append((_f(r.get("generated_at")), _f(r.get("closed_at"))))
        ledger.append({
            "symbol": r.get("symbol"),
            "direction": r.get("direction"),
            "return_pct": round(ret, 4),
            "notional_usd": round(size, 6),
            "amount_at_risk_usd": round(amt_at_risk, 6) if amt_at_risk is not None else None,
            "gross_usd": round(gross, 6),
            "fee_usd": round(round_trip_fee, 6),
            "net_usd": round(net, 6),
            "balance_usd": round(balance, 6),
            "closed_at": r.get("closed_at"),
        })
        curve.append({"closed_at": r.get("closed_at"), "balance_usd": round(balance, 6)})

    net_pnl = balance - start_balance
    decided = wins + losses
    max_concurrent = _max_concurrent(fill_intervals)
    return {
        "strategy_version": strategy_version,
        "config": {
            "trade_size_usd": round(size, 6),
            "start_balance_usd": round(start_balance, 2),
            "fee_bps": float(fee_bps_v),
            "leverage": round(lev, 4),
            "margin_per_trade_usd": round(margin_per_trade, 6),
            "round_trip_fee_usd": round(round_trip_fee, 6),
        },
        "summary": {
            "filled_trades": filled,
            "skipped_insufficient_capital": skipped_insufficient_capital,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / decided * 100, 1) if decided else None,
            "end_balance_usd": round(balance, 6),
            "net_pnl_usd": round(net_pnl, 6),
            "net_return_pct": round(net_pnl / start_balance * 100, 4),
            "gross_pnl_usd": round(gross_pnl, 6),
            "fees_paid_usd": round(fees_paid, 6),
            "max_drawdown_usd": round(max_drawdown_usd, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "account_ruined": account_ruined,
            "ruined_at_trade": ruined_at_trade,
            # Notional is the position value; amount-at-risk is what the stop puts
            # on the line. Reported apart so the two are never conflated.
            "notional_per_trade_usd": round(size, 6),
            "avg_amount_at_risk_usd": round(risk_sum / filled, 6) if filled else None,
            "peak_amount_at_risk_usd": round(peak_amount_at_risk, 6) if filled else None,
            # Sequential model: one position at a time, so peak exposure is one
            # notional. The observed overlap shows what a concurrent book would
            # have carried instead.
            "peak_exposure_usd": round(size, 6) if filled else 0.0,
            "observed_max_concurrent_positions": max_concurrent,
            "peak_exposure_if_concurrent_usd": round(size * max_concurrent, 6),
            "fees_as_pct_of_gross": (round(fees_paid / abs(gross_pnl) * 100, 1)
                                     if gross_pnl else None),
        },
        "concurrency_model": "sequential_only",
        "equity_curve": curve,
        "ledger": ledger,
        "live_readiness": {
            "hyperliquid_min_order_usd": HYPERLIQUID_MIN_ORDER_USD,
            "size_meets_live_minimum": size >= HYPERLIQUID_MIN_ORDER_USD,
            "concurrency_modelled": False,
            "liquidation_modelled": False,
            "funding_modelled": False,
            "live_ready": False,          # never, until the above are modelled
            "note": ("simulation only — no order was placed. NOT live-ready: "
                     "concurrency, liquidation and funding are not modelled, and a "
                     f"live Hyperliquid order must be >= ${HYPERLIQUID_MIN_ORDER_USD:.0f} "
                     "(a smaller paper size models a trade the exchange would reject)."),
        },
        "caveats": [
            "Reporting only — this places no orders and moves no funds.",
            "SEQUENTIAL: one position at a time. observed_max_concurrent_positions "
            "shows where a concurrent book would have needed more capital than this "
            "sequential walk assumes.",
            "Funding is not modelled; perps pay/earn it every 8h, so paper P&L is "
            "slightly optimistic on trades held long enough to be charged.",
            "Slippage and liquidation are not modelled; fills are assumed at the "
            "signal's tracked entry/exit, so live results would be modestly worse.",
        ],
    }
