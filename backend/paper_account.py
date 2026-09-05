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

__all__ = ["build_paper_account", "HYPERLIQUID_MIN_ORDER_USD", "DEFAULT_FEE_BPS"]

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


def build_paper_account(
    rows: Sequence[Dict[str, Any]],
    *,
    trade_size_usd: float = 1.0,
    start_balance_usd: float = 1000.0,
    fee_bps: float = DEFAULT_FEE_BPS,
    strategy_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Walk a fixed-notional paper account over closed signal rows (oldest first).

    ``rows`` are closed-signal dicts (``list_closed_with_snapshots`` /
    ``list_signals`` shape): each needs ``status`` and ``realized_return_pct``;
    ``closed_at``/``generated_at``/``symbol``/``direction`` enrich the ledger when
    present. Each filled trade risks ``trade_size_usd`` of notional; the $ P&L is
    ``notional * realised_return% − round-trip fee``. Cancelled orders never
    filled and are skipped.
    """
    size = max(0.0, float(trade_size_usd or 0.0))
    fee_frac = max(0.0, float(fee_bps or 0.0)) / 10_000.0
    round_trip_fee = size * fee_frac * 2.0            # entry + exit, taker both sides

    # Oldest-first so the equity curve reads forward in time. Rows arrive newest
    # first from the store; sort on close time, falling back to generated time.
    def _when(r):
        return (_f(r.get("closed_at")) or _f(r.get("generated_at")) or 0.0)
    ordered = sorted(rows or [], key=_when)

    balance = float(start_balance_usd)
    peak = balance
    max_drawdown_usd = 0.0
    wins = losses = filled = 0
    gross_pnl = fees_paid = 0.0
    curve: List[Dict[str, Any]] = []
    ledger: List[Dict[str, Any]] = []

    for r in ordered:
        outcome = classify_outcome(r)
        if outcome == "cancelled" or outcome == "open":
            continue                                  # never filled / not resolved
        ret = _f(r.get("realized_return_pct"))
        if ret is None:
            continue
        filled += 1
        gross = size * (ret / 100.0)
        net = gross - round_trip_fee
        balance += net
        gross_pnl += gross
        fees_paid += round_trip_fee
        if net > 0:
            wins += 1
        elif net < 0:
            losses += 1
        peak = max(peak, balance)
        max_drawdown_usd = max(max_drawdown_usd, peak - balance)
        entry = {
            "symbol": r.get("symbol"),
            "direction": r.get("direction"),
            "return_pct": round(ret, 4),
            "gross_usd": round(gross, 6),
            "fee_usd": round(round_trip_fee, 6),
            "net_usd": round(net, 6),
            "balance_usd": round(balance, 6),
            "closed_at": r.get("closed_at"),
        }
        ledger.append(entry)
        curve.append({"closed_at": r.get("closed_at"), "balance_usd": round(balance, 6)})

    net_pnl = balance - start_balance_usd
    decided = wins + losses
    return {
        "strategy_version": strategy_version,
        "config": {
            "trade_size_usd": round(size, 6),
            "start_balance_usd": round(float(start_balance_usd), 2),
            "fee_bps": float(fee_bps),
            "round_trip_fee_usd": round(round_trip_fee, 6),
        },
        "summary": {
            "filled_trades": filled,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / decided * 100, 1) if decided else None,
            "end_balance_usd": round(balance, 6),
            "net_pnl_usd": round(net_pnl, 6),
            "net_return_pct": round(net_pnl / start_balance_usd * 100, 4) if start_balance_usd else None,
            "gross_pnl_usd": round(gross_pnl, 6),
            "fees_paid_usd": round(fees_paid, 6),
            "max_drawdown_usd": round(max_drawdown_usd, 6),
            # The share of gross P&L eaten by fees — the number that exposes why a
            # $1 trade is a bad live idea: at tiny notional, fees dominate.
            "fees_as_pct_of_gross": (round(fees_paid / abs(gross_pnl) * 100, 1)
                                     if gross_pnl else None),
        },
        "equity_curve": curve,
        "ledger": ledger,
        "live_readiness": {
            "hyperliquid_min_order_usd": HYPERLIQUID_MIN_ORDER_USD,
            "size_meets_live_minimum": size >= HYPERLIQUID_MIN_ORDER_USD,
            "note": ("simulation only — no order was placed. A live Hyperliquid "
                     f"order must be >= ${HYPERLIQUID_MIN_ORDER_USD:.0f}; a smaller "
                     "paper size models a trade the exchange would reject."),
        },
        "caveats": [
            "Reporting only — this places no orders and moves no funds.",
            "Funding is not modelled; perps pay/earn it every 8h, so paper P&L is "
            "slightly optimistic on trades held long enough to be charged.",
            "Fills are assumed at the signal's tracked entry/exit — real slippage "
            "on entry and stop would make live results modestly worse.",
        ],
    }
