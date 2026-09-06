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

from datetime import datetime, timezone
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


def _epoch(value) -> Optional[float]:
    """Epoch-ms from an int/float, a datetime, or an ISO-8601 string. None when
    unusable. Deterministic — the same value always maps to the same instant."""
    if value is None or isinstance(value, bool):
        return None
    import math
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def _open_epoch(row) -> Optional[float]:
    """When the position OPENED. Prefer ``entry_filled_at`` — the moment the order
    actually filled — and fall back to ``generated_at`` only for LEGACY rows
    written before entry_filled_at existed (migration 003). None when neither is
    usable, in which case the trade cannot be placed on the timeline."""
    t = _epoch(row.get("entry_filled_at"))
    return t if t is not None else _epoch(row.get("generated_at"))


def _max_concurrent(intervals) -> int:
    """Peak number of trades live at once IGNORING capital, from (open, close)
    pairs. A sweep line; opens before closes at a tie count as overlapping."""
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
    Simulate auto-executing every published signal as a CONCURRENT, margin-reserving
    paper account, processed chronologically.

    ``rows`` are closed-signal dicts (``list_closed_with_snapshots`` /
    ``list_signals`` shape): each needs ``status`` and ``realized_return_pct``;
    the OPEN time is ``entry_filled_at`` (fallback ``generated_at`` for legacy
    rows), the CLOSE time is ``closed_at``, and ``entry_price``/``stop_loss`` give
    the amount at risk.

    Each position puts ``trade_size_usd`` of NOTIONAL on the book at ``leverage``,
    reserving ``notional / leverage`` of capital as margin from OPEN until CLOSE.
    A signal is SKIPPED (``skipped_insufficient_capital``) when the AVAILABLE
    balance — equity minus margin already reserved by open positions — cannot
    cover its margin, so the account never opens a trade it could not fund. Margin
    is released when the position closes, freeing it for later signals. Events are
    ordered by time (opens before closes at a tie, the conservative choice), so a
    genuinely overlapping trade a sequential account could not have entered is
    only opened when the capital is actually free.

    The account balance never goes negative: a loss larger than the remaining
    equity is CAPPED at that equity (the account is ruined), and gross, fee and
    net for that trade are reconciled to the capped figure so
    ``end_balance == start_balance + sum(ledger net P&L)`` holds exactly. On ruin
    the run stops and any still-open positions are liquidated at zero further P&L.

    Simulation only, NOT live-ready: liquidation mechanics and funding are still
    unmodelled. Raises PaperAccountConfigError on a non-finite/zero/negative
    balance, a non-finite/negative size or fee, or leverage below 1.
    """
    start_balance = _require_finite(start_balance_usd, "start_balance_usd", positive=True)
    size = _require_finite(trade_size_usd, "trade_size_usd", positive=True)
    fee_bps_v = _require_finite(fee_bps, "fee_bps", non_negative=True)
    lev = _require_finite(leverage, "leverage", minimum=1.0)

    fee_frac = fee_bps_v / 10_000.0
    round_trip_fee = size * fee_frac * 2.0            # entry + exit, taker both sides
    margin = size / lev

    # ── Build the schedulable positions ──────────────────────────────────────
    positions: List[Dict[str, Any]] = []
    skipped_invalid_timestamp = 0
    for i, r in enumerate(rows or []):
        oc = classify_outcome(r)
        if oc in ("cancelled", "open"):
            # This is a retrospective P&L sim over RESOLVED trades: a cancelled
            # order never filled, and a still-OPEN one has no realised return to
            # simulate. NOTE (documented limitation): a still-open position that
            # HAS filled does occupy margin in reality, but the analytical feed
            # supplies only closed rows, so open-position margin is not reserved
            # here — modelling it needs an as-of time and open-ended holds, out of
            # this report's scope.
            continue
        ret = _f(r.get("realized_return_pct"))
        if ret is None:
            continue                                  # terminal but unpriced
        ot, ct = _open_epoch(r), _epoch(r.get("closed_at"))
        if ot is None or ct is None or ct < ot:
            skipped_invalid_timestamp += 1
            continue                                  # cannot place on the timeline
        positions.append({"seq": i, "row": r, "ret": ret, "open": ot, "close": ct})

    observed_max_concurrent = _max_concurrent([(p["open"], p["close"]) for p in positions])

    # Events: (time, phase, tiebreak, seq). phase 0 = OPEN, 1 = CLOSE, so an open
    # is taken before a close at the same instant (conservative: don't reuse
    # just-freed margin within the same tick), and a zero-duration trade opens
    # before it closes. Among CLOSEs at the SAME timestamp — expected, since the
    # monitor assigns candle timestamps to closes — the biggest GAINS settle
    # first (tiebreak = −raw_net), so a same-candle winner can absorb a same-candle
    # loser before the ruin cap applies. Without this the final balance depended
    # on input row order (a losing close processed first would ruin the account
    # and zero an otherwise-covering winner).
    events = []
    for p in positions:
        p["raw_net"] = size * (p["ret"] / 100.0) - round_trip_fee
        events.append((p["open"], 0, 0.0, p["seq"], p))
        events.append((p["close"], 1, -p["raw_net"], p["seq"], p))
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    equity = start_balance
    reserved = 0.0
    active_risk = 0.0                                  # aggregate stop risk of open positions
    active: Dict[int, Dict[str, Any]] = {}
    peak_equity = start_balance
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0
    peak_exposure = 0.0
    peak_reserved = 0.0
    actual_max_concurrent = 0
    wins = losses = filled = 0
    skipped_insufficient_capital = 0
    gross_pnl = fees_paid = 0.0
    risk_sum = 0.0
    peak_amount_at_risk = 0.0
    account_ruined = False
    ruined_at_trade = None
    net_applied_sum = 0.0                             # UNROUNDED, for exact reconciliation
    ledger: List[Dict[str, Any]] = []
    curve: List[Dict[str, Any]] = []

    def _record_equity(ts):
        nonlocal peak_equity, max_drawdown_usd, max_drawdown_pct
        peak_equity = max(peak_equity, equity)
        dd = peak_equity - equity
        max_drawdown_usd = max(max_drawdown_usd, dd)
        if peak_equity > 0:
            max_drawdown_pct = max(max_drawdown_pct, dd / peak_equity * 100.0)
        curve.append({"closed_at": ts, "balance_usd": round(equity, 6)})

    # Equity, and therefore the drawdown, is recorded ONCE per timestamp (after
    # all closes on that candle settle), never after each individual close —
    # otherwise a same-candle winner then loser prints an artificial peak and
    # drawdown even though the portfolio's timestamp-level equity never moved.
    prev_ts = None
    curve_dirty = False
    curve_ts = None
    for ts, phase, _tiebreak, seq, p in events:
        if prev_ts is not None and ts != prev_ts and curve_dirty:
            _record_equity(curve_ts)                  # flush the previous candle's equity
            curve_dirty = False
        prev_ts = ts
        r = p["row"]
        if phase == 0:                                # OPEN
            # Never admit a trade the free balance cannot FULLY fund: it must cover
            # the margin AND the position's round-trip fee. Checking margin alone
            # admitted trades at the capital boundary whose margin+fees exceeded the
            # balance, contradicting "never opens an unfundable trade". A ruined
            # account has no capital at all. Both cases are counted, never silent.
            if account_ruined or (equity - reserved) + 1e-9 < margin + round_trip_fee:
                skipped_insufficient_capital += 1
                continue
            reserved += margin
            filled += 1
            p["fill_index"] = filled
            active[seq] = p
            # Stop risk AGGREGATED across concurrent open positions (peaked here on
            # open), to stay consistent with the concurrent exposure/margin metrics
            # rather than reporting only the largest single trade's risk.
            rf = _risk_fraction(r)
            p["amt"] = (size * rf) if rf is not None else None
            if p["amt"] is not None:
                risk_sum += p["amt"]
                active_risk += p["amt"]
                peak_amount_at_risk = max(peak_amount_at_risk, active_risk)
            actual_max_concurrent = max(actual_max_concurrent, len(active))
            peak_exposure = max(peak_exposure, len(active) * size)
            peak_reserved = max(peak_reserved, reserved)
        else:                                         # CLOSE
            if seq not in active:
                continue                              # was skipped, so nothing to close
            del active[seq]
            reserved -= margin
            if p.get("amt") is not None:
                active_risk -= p["amt"]               # release this position's stop risk
            if account_ruined:
                # Liquidated WITH the account: margin released, no further P&L, so
                # the reconciliation identity is preserved.
                ledger.append({
                    "symbol": r.get("symbol"), "direction": r.get("direction"),
                    "return_pct": round(p["ret"], 4), "notional_usd": round(size, 6),
                    "amount_at_risk_usd": None,
                    "opened_at": r.get("entry_filled_at") or r.get("generated_at"),
                    "closed_at": r.get("closed_at"),
                    "gross_usd": 0.0, "fee_usd": 0.0, "net_usd": 0.0,
                    "balance_usd": round(equity, 6), "liquidated": True,
                })
                continue
            ret = p["ret"]
            gross = size * (ret / 100.0)
            fee = round_trip_fee
            raw_net = gross - fee
            # Ruin at or below zero: a close that lands equity AT zero wipes the
            # account (it can fund nothing further), and the <= guard also stops a
            # sub-epsilon negative from leaking through and breaking the
            # never-negative invariant.
            if equity + raw_net <= 1e-9:
                net = -equity                         # lose exactly the remaining equity
                gross = net + fee                     # cap gross too, so gross − fee == net
                equity = 0.0
                account_ruined = True
                ruined_at_trade = p["fill_index"]
            else:
                net = raw_net
                equity += net
            net_applied_sum += net
            gross_pnl += gross
            fees_paid += fee
            if net > 0:
                wins += 1
            elif net < 0:
                losses += 1
            amt = p.get("amt")                        # computed at open; risk already tracked there
            ledger.append({
                "symbol": r.get("symbol"), "direction": r.get("direction"),
                "return_pct": round(ret, 4), "notional_usd": round(size, 6),
                "amount_at_risk_usd": round(amt, 6) if amt is not None else None,
                "opened_at": r.get("entry_filled_at") or r.get("generated_at"),
                "closed_at": r.get("closed_at"),
                "gross_usd": round(gross, 6), "fee_usd": round(fee, 6),
                "net_usd": round(net, 6), "balance_usd": round(equity, 6),
                "liquidated": False,
            })
            curve_dirty = True
            curve_ts = r.get("closed_at")

    if curve_dirty:                                   # flush the final candle
        _record_equity(curve_ts)

    # Reconcile on the UNROUNDED running total, not on the sum of 6dp-rounded
    # ledger nets (that sum can drift past the tolerance over many trades and
    # falsely report reconciles=False). equity == start + Σ(applied net) exactly.
    ledger_net_total = round(net_applied_sum, 6)
    net_pnl = round(equity - start_balance, 6)
    decided = wins + losses
    return {
        "strategy_version": strategy_version,
        "config": {
            "trade_size_usd": round(size, 6),
            "start_balance_usd": round(start_balance, 2),
            "fee_bps": float(fee_bps_v),
            "leverage": round(lev, 4),
            "margin_per_trade_usd": round(margin, 6),
            "round_trip_fee_usd": round(round_trip_fee, 6),
        },
        "summary": {
            "filled_trades": filled,
            "skipped_insufficient_capital": skipped_insufficient_capital,
            "skipped_invalid_timestamp": skipped_invalid_timestamp,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / decided * 100, 1) if decided else None,
            "end_balance_usd": round(equity, 6),
            "net_pnl_usd": net_pnl,
            "net_return_pct": round(net_pnl / start_balance * 100, 4),
            "gross_pnl_usd": round(gross_pnl, 6),
            "fees_paid_usd": round(fees_paid, 6),
            # end == start + Σ(applied net); exposed so callers can assert it.
            "ledger_net_total_usd": ledger_net_total,
            "reconciles": abs((start_balance + net_applied_sum) - equity) < 1e-6,
            "max_drawdown_usd": round(max_drawdown_usd, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "account_ruined": account_ruined,
            "ruined_at_trade": ruined_at_trade,
            # Notional is the position value; amount-at-risk is what the stop puts
            # on the line. Reported apart so the two are never conflated.
            "notional_per_trade_usd": round(size, 6),
            "avg_amount_at_risk_usd": round(risk_sum / filled, 6) if filled else None,
            "peak_amount_at_risk_usd": round(peak_amount_at_risk, 6) if filled else None,
            # Concurrent model: exposure and margin scale with positions actually held.
            "peak_exposure_usd": round(peak_exposure, 6),
            "peak_reserved_margin_usd": round(peak_reserved, 6),
            "actual_max_concurrent_positions": actual_max_concurrent,
            "observed_max_concurrent_positions": observed_max_concurrent,
            "fees_as_pct_of_gross": (round(fees_paid / abs(gross_pnl) * 100, 1)
                                     if gross_pnl else None),
        },
        "concurrency_model": "concurrent_margin",
        "equity_curve": curve,
        "ledger": ledger,
        "live_readiness": {
            "hyperliquid_min_order_usd": HYPERLIQUID_MIN_ORDER_USD,
            "size_meets_live_minimum": size >= HYPERLIQUID_MIN_ORDER_USD,
            "concurrency_modelled": True,     # margin-reserving concurrent walk
            "liquidation_modelled": False,
            "funding_modelled": False,
            "live_ready": False,              # never, while liquidation + funding are unmodelled
            "note": ("simulation only — no order was placed. NOT live-ready: "
                     "liquidation mechanics and funding are not modelled, and a "
                     f"live Hyperliquid order must be >= ${HYPERLIQUID_MIN_ORDER_USD:.0f} "
                     "(a smaller paper size models a trade the exchange would reject)."),
        },
        "caveats": [
            "Reporting only — this places no orders and moves no funds.",
            "CONCURRENT margin model: every open position reserves margin until it "
            "closes; a signal is skipped when the free balance cannot fund it.",
            "Funding is not modelled; perps pay/earn it every 8h, so paper P&L is "
            "slightly optimistic on trades held long enough to be charged.",
            "Liquidation mechanics and slippage are not modelled; a loss is capped "
            "at account equity, and fills are assumed at the signal's tracked "
            "entry/exit, so live results would be modestly worse.",
        ],
    }
