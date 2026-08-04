"""
Walk-forward replay of the strategy that actually publishes.

The old backtest answered a question nobody asked. It took one symbol on one
timeframe, ran `generate_signal`, and entered at the next bar's open — no 1H/2H
agreement, no BTC adjustment, no R/R gate, no expired-setup check, no ranking
across the universe, no top-three, no limit order, no 24-hour cancellation and
no scale-out. Then its expectancy was read as evidence about the published
strategy. Nine of the rules that decide what gets traded were missing, and the
one it did model — the entry — it modelled wrong, in the direction that
flatters: a market fill at the next open always happens, while the limit order
production actually places often never fills at all.

This module replays the real thing:

  * every historical 4H publication slot, in order;
  * only candles closed AT OR BEFORE that slot — never the forming bar;
  * the real `generate_signal` on 1H and 2H, and 4H for the HTF read;
  * the real gates, ranking and correlation-aware top-three, via `rec_policy`,
    the same functions production calls;
  * the real entry: a resting limit order that fills only if price comes back
    to it, and is cancelled unfilled after 24 hours;
  * the real exits: `signal_monitor.evaluate`, one candle at a time, exactly as
    the hourly monitor sees them — 50/30/20 scale-out, stop to breakeven after
    the first partial, stop-wins on same-bar ambiguity, expiry at 72 hours.

Nothing here reimplements a rule. Where a decision exists in production code,
this calls it. That is the whole design: a backtest that restates the rules is
measuring its own restatement, and the restatement drifts.

WHAT IT STILL CANNOT TELL YOU
-----------------------------
Two things, and both are reported in every result rather than buried here.

`price_only` mode replays the OHLCV-derived groups (trend, momentum, pattern)
and nothing else. Funding, open interest, futures CVD, long/short ratio,
sentiment, macro, on-chain and options have no historical series here, so those
scoring blocks stay dormant and `generate_signal` degrades gracefully. The
result measures the price/structure edge. It CANNOT validate the external
inputs, and it must never be quoted as if it had.

`historical_full` mode accepts timestamped external snapshots and uses only
observations available at or before each slot. It reports coverage per feature
family, because a mode that silently substitutes today's funding rate for last
March's is worse than one that admits it has none: it produces a confident
number built on information the strategy could not have had.

Tests passing means the implementation is correct. It does not mean the
strategy is profitable, and no output of this module should be read that way.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import rec_policy
import signal_monitor
from backtest import build_price_analysis
from signal_store import ladder_shares, weighted_return
from signals import generate_signal

__all__ = [
    "PARITY_MODES", "TF_MS", "PUBLICATION_INTERVAL_MS",
    "DEFAULT_FEE_BPS", "DEFAULT_SLIPPAGE_BPS",
    "closed_slice", "publication_slots", "replay",
]

# Bar length in epoch milliseconds. A candle whose open timestamp is `ts` is
# CLOSED at `ts + TF_MS[tf]`, and not one millisecond before.
TF_MS = {
    "1H": 3_600_000,
    "2H": 7_200_000,
    "4H": 14_400_000,
}

PUBLICATION_INTERVAL_MS = 4 * 3_600_000

PARITY_MODES = ("price_only", "historical_full")

# Round-trip cost assumptions, in basis points, applied per leg. Defaults are
# deliberately not zero: a strategy that is only profitable at zero cost is not
# profitable. Taker fees on a major venue run 4-7bp a side; 2bp of slippage on
# a limit entry is optimistic and on a stop-market exit is generous.
DEFAULT_FEE_BPS = 6.0
DEFAULT_SLIPPAGE_BPS = 2.0

# The external feature families production scores that have no OHLCV origin.
# Named so a report can say what is missing rather than implying completeness.
EXTERNAL_FEATURE_FAMILIES = (
    "funding", "open_interest", "futures_cvd", "long_short_ratio",
    "fear_greed", "news_sentiment", "onchain", "etf_flows", "macro", "options",
)


# ── Time and slicing ─────────────────────────────────────────────────────────

def closed_slice(candles: Sequence[Dict], timeframe: str, at_ms: int,
                 *, lookback: Optional[int] = None) -> List[Dict]:
    """
    The candles a live engine could have seen at ``at_ms``.

    A candle counts only when it has CLOSED: its open timestamp plus one bar
    length is at or before the instant. The forming bar is excluded, which is
    the single most common way a backtest invents an edge — its high and low
    are not knowable until it closes, and half the indicators would be reading
    the future through them.
    """
    span = TF_MS.get(timeframe)
    if not span:
        return []
    out = [c for c in candles or []
           if c.get("timestamp") is not None and int(c["timestamp"]) + span <= at_ms]
    if lookback:
        out = out[-lookback:]
    return out


def publication_slots(candles_4h: Sequence[Dict], *,
                      start_ms: Optional[int] = None,
                      end_ms: Optional[int] = None) -> List[int]:
    """
    The instants production would have published at: every 4H candle close.

    Derived from the data rather than generated from a calendar, so a gap in
    the history produces no slot instead of a slot with nothing behind it.
    """
    span = TF_MS["4H"]
    out = []
    for c in candles_4h or []:
        ts = c.get("timestamp")
        if ts is None:
            continue
        close = int(ts) + span
        if start_ms is not None and close < start_ms:
            continue
        if end_ms is not None and close > end_ms:
            continue
        out.append(close)
    return sorted(set(out))


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


# ── Building one timeframe's reading, the way production shapes it ───────────

def _tf_reading(symbol: str, timeframe: str, window: List[Dict],
                *, external: Optional[Dict] = None) -> Optional[Dict]:
    """
    One (symbol, timeframe) reading in the shape `rec_policy` consumes.

    Mirrors what `_compute_recommendations` pulls out of `get_analysis`: the
    direction, the strength, the signal itself, the prices and the quality
    flags. `signal_price` and `live_price` are BOTH the last closed price —
    see the parity notes: at the moment of publication production reads a live
    tick, and the only honest historical stand-in is the close it was built on.
    """
    if len(window) < 60:
        return None                      # not enough history to seed indicators
    analysis = build_price_analysis(window, timeframe, symbol)
    if external:
        analysis.update(external)
    sig = generate_signal(analysis)
    last_close = window[-1].get("close")
    return {
        "direction": sig.get("direction", "NEUTRAL"),
        "strength": sig.get("strength", 0) or 0,
        "sig": sig,
        "rsi": analysis.get("rsi"),
        "current_price": sig.get("current_price") or last_close,
        # Replay has no tick data. The last closed price is what the ladder was
        # built on and the best available stand-in for the live read, so the
        # divergence gate and the TP-behind check both measure against it.
        "live_price": last_close,
        "signal_price": last_close,
        # Every candle fed here is closed, complete and from one source, so the
        # data-quality gate has nothing to fail on. This is a parity gap in the
        # optimistic direction and is reported as one: production drops
        # candidates for stale or misaligned data that replay never sees.
        "data_quality": "good",
        "tradeable": True,
        "reversal_radar": sig.get("reversal_radar") or {},
    }


# ── The paper position: production's state machine, in memory ────────────────

class _PaperPosition:
    """
    One published recommendation, walked forward.

    Holds exactly the fields `signal_monitor.evaluate` reads off a stored row,
    and applies the actions it returns the way `signal_store` would. It is a
    store, not a second rulebook — every decision comes from `evaluate`.
    """

    def __init__(self, rec: Dict, slot_ms: int):
        self.rec = rec
        self.symbol = rec["symbol"]
        self.direction = rec["direction"]
        self.entry = float(rec["entry"])
        self.stop = float(rec["sl"])
        self.targets = [{"target_number": i, "target_price": float(p),
                         "hit_at": None}
                        for i, p in enumerate(rec["tp_targets"], start=1)]
        self.shares = ladder_shares(len(self.targets))
        self.row = {
            "id": rec["id"],
            "symbol": self.symbol,
            "timeframe": "2H",
            "direction": self.direction,
            "entry_price": self.entry,
            "stop_loss": self.stop,
            "current_stop_loss": self.stop,
            "status": "PENDING",
            "candle_close_time": slot_ms,
            "generated_at": slot_ms,
            "entry_filled_at": None,
            "entry_fill_price": None,
        }
        self.exits: List[Tuple[Decimal, float]] = []
        self.final_price: Optional[float] = None
        self.filled_at: Optional[int] = None
        self.closed_at: Optional[int] = None
        self.outcome: Optional[str] = None
        self.events: List[str] = []
        self.stop_moved_to_breakeven = False

    # -- helpers ---------------------------------------------------------
    @property
    def terminal(self) -> bool:
        return self.row["status"] not in ("PENDING", "OPEN", "PARTIAL_TP")

    def _share_for(self, n: int) -> Decimal:
        if self.shares and 1 <= n <= len(self.shares):
            return self.shares[n - 1]
        # No published plan for this ladder length: an even split, which is
        # what signal_store documents as the default. Never a guess.
        return Decimal(1) / Decimal(len(self.targets) or 1)

    def apply(self, action: Dict) -> None:
        kind = action["kind"]
        at = action.get("at")
        at_ms = int(at.timestamp() * 1000) if isinstance(at, datetime) else at
        self.events.append(kind)
        if kind == "ENTRY_FILLED":
            self.row["status"] = "OPEN"
            self.row["entry_filled_at"] = at_ms
            self.row["entry_fill_price"] = float(action["price"])
            self.filled_at = at_ms
        elif kind == "TARGET_HIT":
            n = int(action["target_number"])
            for t in self.targets:
                if t["target_number"] == n:
                    t["hit_at"] = at_ms
            self.exits.append((self._share_for(n), float(action["price"])))
            if all(t["hit_at"] for t in self.targets):
                self.row["status"] = "TARGET_HIT"
                self.final_price = float(action["price"])
                self.closed_at, self.outcome = at_ms, "tp%d" % len(self.targets)
            else:
                self.row["status"] = "PARTIAL_TP"
        elif kind == "STOP_MOVED":
            self.row["current_stop_loss"] = float(action["price"])
            if abs(float(action["price"]) - self.entry) < 1e-12:
                self.stop_moved_to_breakeven = True
        elif kind == "STOP_LOSS_HIT":
            self.row["status"] = "STOP_LOSS_HIT"
            self.final_price = float(action["price"])
            self.closed_at = at_ms
            hits = sum(1 for t in self.targets if t["hit_at"])
            self.outcome = f"tp{hits}_then_be" if self.stop_moved_to_breakeven \
                else ("sl" if not hits else f"tp{hits}_then_sl")
        elif kind == "EXPIRED":
            self.row["status"] = "EXPIRED"
            self.final_price = (float(action["price"])
                                if action.get("price") is not None else None)
            self.closed_at, self.outcome = at_ms, "expired"
        elif kind == "CANCELLED":
            self.row["status"] = "CANCELLED"
            self.closed_at, self.outcome = at_ms, "cancelled"


def _walk_position(pos: _PaperPosition, candles: Sequence[Dict], *,
                   fill_window_hours: int, max_age_hours: int) -> None:
    """
    Feed the position one candle at a time, exactly as the hourly monitor sees
    them.

    Calling `evaluate` once with every future candle would be look-ahead of a
    subtle kind: the 24-hour cancellation is decided against a clock, so a
    single call with `now` set to the end of the dataset would let an order that
    price returned to on hour 30 still fill — when production would have
    withdrawn it at hour 24 and never taken the trade. Stepping the clock with
    the candles is what makes the cancellation real.

    Each step sees ONE candle, and `candle_close_time` advances with it, so a
    candle is judged once. Re-feeding the whole prefix each step would let a
    stop that has since moved to breakeven be triggered by a bar that closed
    before the move — the trade would be stopped out by history it had already
    survived. `generated_at` stays at the publication slot, because the fill
    window and the expiry are both measured from when the order was placed.
    """
    for c in candles:
        ts = int(c["timestamp"])
        pos.row["candle_close_time"] = ts
        now = datetime.fromtimestamp((ts + TF_MS["2H"]) / 1000.0, tz=timezone.utc)
        actions = signal_monitor.evaluate(
            pos.row, pos.targets, [c], now=now,
            max_age_hours=max_age_hours, fill_window_hours=fill_window_hours)
        for a in actions:
            pos.apply(a)
        if pos.terminal:
            return


# ── Costs ────────────────────────────────────────────────────────────────────

def _cost_pct(pos: _PaperPosition, fee_bps: float, slippage_bps: float) -> float:
    """
    Execution cost of the round trip, in percent of notional.

    One leg in, and one leg out for every fraction actually closed. Charging a
    flat round trip would over-charge a position that expired half open and
    under-charge nothing — so it is counted per leg, against the fraction each
    leg closed.
    """
    per_leg = (fee_bps + slippage_bps) / 100.0
    if pos.filled_at is None:
        return 0.0                       # never filled: nothing was transacted
    closed = sum(float(f) for f, _ in pos.exits)
    if pos.final_price is not None:
        closed = 1.0
    return per_leg * (1.0 + min(closed, 1.0))


# ── The replay ───────────────────────────────────────────────────────────────

def replay(market: Dict[str, Dict[str, List[Dict]]], *,
           symbols: Optional[Sequence[str]] = None,
           btc_symbol: str = "BTC",
           correlations: Optional[Dict[str, float]] = None,
           parity_mode: str = "price_only",
           external: Optional[Dict[str, List[Dict]]] = None,
           onchain_score: float = 50.0,
           fee_bps: float = DEFAULT_FEE_BPS,
           slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
           lookback: int = 240,
           warmup_bars: int = 60,
           fill_window_hours: int = signal_monitor.DEFAULT_FILL_WINDOW_HOURS,
           max_age_hours: int = signal_monitor.DEFAULT_MAX_AGE_HOURS,
           max_slots: Optional[int] = None,
           keep_trades: bool = True) -> Dict[str, Any]:
    """
    Replay the publication strategy over `market` and report what it did.

    ``market`` is ``{symbol: {"1H": [...], "2H": [...], "4H": [...]}}``, each a
    list of closed candles oldest-first with timestamp (epoch ms), open, high,
    low, close, volume.

    Deterministic: no wall clock is read anywhere in this function or anything
    it calls. The same market produces the same report, which is what makes a
    regression in the strategy visible as a diff.
    """
    if parity_mode not in PARITY_MODES:
        raise ValueError(f"parity_mode must be one of {PARITY_MODES}")

    correlations = correlations or {}
    symbols = [s for s in (symbols or market.keys())]
    tradable = [s for s in symbols if s != btc_symbol]

    slots = publication_slots(market.get(btc_symbol, {}).get("4H")
                              or next((m.get("4H") for m in market.values()
                                       if m.get("4H")), []))
    if max_slots:
        slots = slots[-max_slots:]

    published: List[Dict] = []
    rejections: Dict[str, int] = {r: 0 for r in rec_policy.REJECTION_REASONS}
    candidates_seen = 0
    slots_evaluated = 0
    coverage = _ExternalCoverage(parity_mode)

    for slot in slots:
        # ── BTC first: every candidate in this slot is measured against it ──
        btc_win = closed_slice(market.get(btc_symbol, {}).get("2H") or [],
                               "2H", slot, lookback=lookback)
        btc_read = _tf_reading(btc_symbol, "2H", btc_win) if btc_win else None
        influence = rec_policy.btc_influence(
            (btc_read or {}).get("direction", "NEUTRAL"),
            (btc_read or {}).get("strength", 0),
            onchain_score=onchain_score)

        slot_candidates: List[Dict] = []
        for sym in tradable:
            tfs = market.get(sym) or {}
            ext = coverage.features_for(sym, slot, external)
            reads = {}
            for tf in ("1H", "2H", "4H"):
                win = closed_slice(tfs.get(tf) or [], tf, slot, lookback=lookback)
                reads[tf] = (_tf_reading(sym, tf, win, external=ext)
                             if len(win) >= warmup_bars else None)
            h1, h2, h4 = reads["1H"], reads["2H"], reads["4H"]

            screen = rec_policy.screen_candidate(
                h1, h2, h4, corr_factor=correlations.get(sym, 1.0),
                influence=influence)
            if not screen["ok"]:
                reason = screen.get("reason")
                if reason:
                    rejections[reason] = rejections.get(reason, 0) + 1
                continue

            candidates_seen += 1
            sig = screen["sig"]
            cand = {
                "symbol": sym,
                "direction": screen["direction"],
                "strength": screen["strength"],
                "h1_strength": round(h1["strength"], 1),
                "h2_strength": round(h2["strength"], 1),
                "avg_tf_strength": screen["avg_tf_strength"],
                "btc_corr": correlations.get(sym, 1.0),
                "btc_adj": screen["btc_adj"],
                "rr_ratio": screen["rr_ratio"],
                "htf_4h_dir": screen["htf_4h_dir"],
                "reversal_against": _reversal_against(screen["direction"], h2, h4),
                "h2_exhausted": sig.get("exhaustion_flag", False),
                "h2_reversal_count": sig.get("reversal_count", 0),
                "data_quality": "good",
                "entry": sig.get("entry"),
                "sl": sig.get("sl"),
                "tp_targets": list(sig.get("tp_targets") or []),
                "leverage": sig.get("leverage"),
            }
            q, qf = rec_policy.rec_quality(cand, cand["htf_4h_dir"])
            cand["quality_score"], cand["quality_factors"] = q, qf
            slot_candidates.append(cand)

        slots_evaluated += 1
        ranked = rec_policy.rank_candidates(slot_candidates)
        for rank, cand in enumerate(rec_policy.select_publishable(ranked), start=1):
            if not (cand["entry"] and cand["sl"] and cand["tp_targets"]):
                continue
            rec = dict(cand)
            rec["id"] = f"{cand['symbol']}-{slot}"
            rec["slot_ms"] = slot
            rec["slot"] = _iso(slot)
            rec["rank"] = rank
            published.append(rec)

    # ── Execution: the published set, walked forward ────────────────────────
    trades: List[Dict] = []
    for rec in published:
        forward = [c for c in (market.get(rec["symbol"], {}).get("2H") or [])
                   if int(c["timestamp"]) >= rec["slot_ms"]]
        pos = _PaperPosition(rec, rec["slot_ms"])
        _walk_position(pos, forward, fill_window_hours=fill_window_hours,
                       max_age_hours=max_age_hours)
        trades.append(_settle(pos, rec, fee_bps=fee_bps, slippage_bps=slippage_bps))

    report = {
        "parity": _parity_block(parity_mode, market, symbols, slots,
                                slots_evaluated, coverage, fee_bps, slippage_bps,
                                fill_window_hours, max_age_hours),
        "population": _population(candidates_seen, published, trades, rejections),
        "metrics": aggregate(trades),
    }
    if keep_trades:
        report["trades"] = trades
    return report


def _reversal_against(direction: str, h2: Dict, h4: Optional[Dict]) -> Optional[str]:
    """Strongest reversal-radar level fighting the trade, across 2H and 4H."""
    rank = {"low": 0, "building": 1, "elevated": 2, "high": 3}
    worst = None
    for src in ((h2 or {}).get("reversal_radar") or {}, (h4 or {}).get("reversal_radar") or {}):
        mode, lvl = src.get("mode"), src.get("level")
        opposes = (direction == "LONG" and mode == "top") or \
                  (direction == "SHORT" and mode == "bottom")
        if opposes and lvl in rank and (worst is None or rank[lvl] > rank[worst]):
            worst = lvl
    return worst


def _settle(pos: _PaperPosition, rec: Dict, *, fee_bps: float,
            slippage_bps: float) -> Dict:
    """Turn a walked position into a trade record, in percent and in R."""
    entry = pos.row.get("entry_fill_price") or pos.entry
    risk_pct = abs(entry - pos.stop) / entry * 100.0 if entry else 0.0
    gross = weighted_return(pos.direction, entry, pos.exits, pos.final_price)
    gross_pct = float(gross) if gross is not None else None
    cost = _cost_pct(pos, fee_bps, slippage_bps)
    net_pct = round(gross_pct - cost, 8) if gross_pct is not None else None
    r = round(net_pct / risk_pct, 4) if (net_pct is not None and risk_pct > 0) else None

    status = pos.row["status"]
    return {
        "symbol": pos.symbol,
        "slot": rec["slot"],
        "slot_ms": rec["slot_ms"],
        "rank": rec["rank"],
        "direction": pos.direction,
        "strength": rec["strength"],
        "avg_tf_strength": rec["avg_tf_strength"],
        "quality_score": rec["quality_score"],
        "rr_ratio": rec["rr_ratio"],
        "entry": pos.entry,
        "entry_fill": pos.row.get("entry_fill_price"),
        "stop": pos.stop,
        "final_stop": pos.row["current_stop_loss"],
        "targets": [t["target_price"] for t in pos.targets],
        "targets_hit": [t["target_number"] for t in pos.targets if t["hit_at"]],
        "moved_to_breakeven": pos.stop_moved_to_breakeven,
        "status": status,
        "filled": pos.filled_at is not None,
        "outcome": pos.outcome or ("working" if status in ("PENDING", "OPEN",
                                                           "PARTIAL_TP") else status),
        "risk_pct": round(risk_pct, 8),
        "gross_return_pct": gross_pct,
        "cost_pct": round(cost, 8),
        "return_pct": net_pct,
        "r": r,
        "filled_at": pos.filled_at,
        "closed_at": pos.closed_at,
        "hold_hours": (round((pos.closed_at - pos.filled_at) / 3_600_000.0, 2)
                       if pos.filled_at and pos.closed_at else None),
        "events": pos.events,
    }


# ── External-data coverage ───────────────────────────────────────────────────

class _ExternalCoverage:
    """
    Tracks which external feature families were actually available, per slot.

    Exists so a `historical_full` report can state its own incompleteness. A
    replay that quietly falls back to neutral values for a missing family, and
    then reports full parity, produces a number built on information the
    strategy never had — which is worse than reporting nothing, because it is
    confident.
    """

    def __init__(self, parity_mode: str):
        self.mode = parity_mode
        self.available: Dict[str, int] = {f: 0 for f in EXTERNAL_FEATURE_FAMILIES}
        self.requested = 0
        self.rejected_future = 0

    def features_for(self, symbol: str, slot_ms: int,
                     external: Optional[Dict[str, List[Dict]]]) -> Optional[Dict]:
        if self.mode != "historical_full" or not external:
            return None
        self.requested += 1
        snaps = external.get(symbol) or []
        usable = []
        for s in snaps:
            at = s.get("available_at")
            if at is None:
                continue
            if int(at) > slot_ms:
                # A future-dated observation is not a data problem to work
                # around; it is the exact thing that makes a backtest lie.
                self.rejected_future += 1
                continue
            usable.append(s)
        if not usable:
            return None
        latest = max(usable, key=lambda s: int(s["available_at"]))
        feats = {k: v for k, v in latest.items() if k != "available_at"}
        for family in feats:
            if family in self.available:
                self.available[family] += 1
        return feats

    def report(self) -> Dict:
        if self.mode != "historical_full":
            return {
                "mode": self.mode,
                "families_replayed": [],
                "families_omitted": list(EXTERNAL_FEATURE_FAMILIES),
                "note": ("price_only: no external observation was used. This "
                         "result measures the price/structure edge and cannot "
                         "validate any external-data contribution."),
            }
        return {
            "mode": self.mode,
            "symbol_slots_requested": self.requested,
            "future_dated_rejected": self.rejected_future,
            "coverage": {f: (round(self.available[f] / self.requested, 4)
                             if self.requested else 0.0)
                         for f in EXTERNAL_FEATURE_FAMILIES},
            "families_omitted": [f for f in EXTERNAL_FEATURE_FAMILIES
                                 if not self.available[f]],
        }


def _parity_block(parity_mode, market, symbols, slots, slots_evaluated,
                  coverage, fee_bps, slippage_bps, fill_window_hours,
                  max_age_hours) -> Dict:
    candle_cov = {}
    for sym in symbols:
        tfs = market.get(sym) or {}
        candle_cov[sym] = {tf: len(tfs.get(tf) or []) for tf in ("1H", "2H", "4H")}
    return {
        "parity_mode": parity_mode,
        "replayed_gates": list(rec_policy.REJECTION_REASONS) + [
            "RANKING_avg_1h_2h_strength", "TIEBREAK_quality_score",
            "CORRELATION_DIVERSIFICATION", f"TOP_{rec_policy.PUBLISH_TOP_N}"],
        "gate_constants": {
            "min_adjusted_strength": rec_policy.MIN_ADJUSTED_STRENGTH,
            "min_rr": rec_policy.MIN_RR,
            "max_signal_live_divergence": rec_policy.MAX_SIGNAL_LIVE_DIVERGENCE,
            "high_corr": rec_policy.HIGH_CORR,
            "publish_top_n": rec_policy.PUBLISH_TOP_N,
        },
        "external_data": coverage.report(),
        "candle_coverage": candle_cov,
        "symbols": list(symbols),
        "publication_slots_available": len(slots),
        "publication_slots_evaluated": slots_evaluated,
        "first_slot": _iso(slots[0]) if slots else None,
        "last_slot": _iso(slots[-1]) if slots else None,
        "execution": {
            "entry": "resting limit at the published entry; fills only when a "
                     "later candle trades through it",
            "fill_window_hours": fill_window_hours,
            "max_age_hours": max_age_hours,
            "scale_out": "TP1 50%, TP2 30%, TP3 20% (signal_store.SCALE_OUT_SHARES)",
            "breakeven": "stop moves to entry after the first partial target",
            "intrabar_ambiguity": "OHLC does not reveal ordering. When one "
                                  "candle touches both a target and the stop "
                                  "after entry, the STOP is recorded.",
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "cost_model": "(fee + slippage) charged on the entry leg and on "
                          "every fraction closed",
        },
        "known_non_parity": [
            "No live tick: signal_price and live_price are both the last closed "
            "price, so the divergence gate and the TP1-behind-live gate are "
            "measured against the close rather than an intra-slot tick.",
            "data_quality is always good: replay feeds complete closed candles "
            "from one source, so candidates production drops for stale or "
            "misaligned data are still published here.",
            "Options-expiry pressure and the on-chain multiplier are constants, "
            "not historical series.",
        ],
    }


# ── Populations and metrics ──────────────────────────────────────────────────

def _population(candidates: int, published: List[Dict], trades: List[Dict],
                rejections: Dict[str, int]) -> Dict:
    """
    The populations, kept apart on purpose.

    An unfilled order is not a zero-return trade. Folding cancellations into the
    win rate measures a population nobody could have traded, and it is the
    single largest distortion available to a backtest of a limit-entry strategy:
    the orders that never fill are disproportionately the ones price ran away
    from, which is to say the ones that would have won.
    """
    filled = [t for t in trades if t["filled"]]
    return {
        "candidates_generated": candidates,
        "recommendations_published": len(published),
        "orders_filled": len(filled),
        "orders_cancelled_unfilled": sum(1 for t in trades
                                         if t["status"] == "CANCELLED"),
        "trades_completed": sum(1 for t in filled
                                if t["status"] in ("TARGET_HIT", "STOP_LOSS_HIT")),
        "trades_expired": sum(1 for t in filled if t["status"] == "EXPIRED"),
        "open_at_dataset_end": sum(1 for t in trades
                                   if t["status"] in ("PENDING", "OPEN", "PARTIAL_TP")),
        "rejections": dict(rejections),
    }


def aggregate(trades: Sequence[Dict]) -> Dict:
    """
    Headline metrics over the FILLED, CLOSED population only.

    Working and cancelled orders are excluded from every average, rate and
    total here — they are counted in the population block instead. A
    cancellation is not a scratch: it is an order that never became a position.
    """
    closed = [t for t in trades
              if t["filled"] and t["status"] in ("TARGET_HIT", "STOP_LOSS_HIT",
                                                 "EXPIRED")
              and t["r"] is not None]
    published = len(trades)
    filled = sum(1 for t in trades if t["filled"])
    base = {
        "trades": len(closed),
        "fill_rate_pct": round(filled / published * 100, 1) if published else None,
        "cancellation_rate_pct": round(
            sum(1 for t in trades if t["status"] == "CANCELLED") / published * 100, 1)
        if published else None,
    }
    if not closed:
        base["note"] = "no filled and closed trades in this window"
        return base

    rs = [t["r"] for t in closed]
    pcts = [t["return_pct"] for t in closed if t["return_pct"] is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    eq = peak = max_dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)

    holds = [t["hold_hours"] for t in closed if t["hold_hours"] is not None]
    by_symbol: Dict[str, Dict] = {}
    for t in closed:
        b = by_symbol.setdefault(t["symbol"], {"trades": 0, "total_R": 0.0})
        b["trades"] += 1
        b["total_R"] = round(b["total_R"] + t["r"], 4)
    by_slot: Dict[str, Dict] = {}
    for t in closed:
        hour = datetime.fromtimestamp(t["slot_ms"] / 1000.0,
                                      tz=timezone.utc).strftime("%H:00Z")
        b = by_slot.setdefault(hour, {"trades": 0, "total_R": 0.0})
        b["trades"] += 1
        b["total_R"] = round(b["total_R"] + t["r"], 4)

    base.update({
        "expectancy_R": round(sum(rs) / len(rs), 4),
        "total_R": round(sum(rs), 4),
        "avg_return_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "total_return_pct": round(sum(pcts), 4) if pcts else None,
        "win_rate_pct": round(len(wins) / len(rs) * 100, 1),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "avg_win_R": round(gross_win / len(wins), 4) if wins else 0.0,
        "avg_loss_R": round(-gross_loss / len(losses), 4) if losses else 0.0,
        "max_drawdown_R": round(max_dd, 4),
        "max_consecutive_losses": worst,
        "avg_hold_hours": round(sum(holds) / len(holds), 2) if holds else None,
        "tp1_hit_rate_pct": _hit_rate(closed, 1),
        "tp2_hit_rate_pct": _hit_rate(closed, 2),
        "tp3_hit_rate_pct": _hit_rate(closed, 3),
        "tp1_then_breakeven": sum(1 for t in closed
                                  if t["targets_hit"] == [1] and t["moved_to_breakeven"]
                                  and t["status"] == "STOP_LOSS_HIT"),
        "tp2_then_breakeven": sum(1 for t in closed
                                  if t["targets_hit"] == [1, 2] and t["moved_to_breakeven"]
                                  and t["status"] == "STOP_LOSS_HIT"),
        "stop_loss_count": sum(1 for t in closed if t["status"] == "STOP_LOSS_HIT"),
        "expired_count": sum(1 for t in closed if t["status"] == "EXPIRED"),
        "full_target_count": sum(1 for t in closed if t["status"] == "TARGET_HIT"),
        "long": sum(1 for t in closed if t["direction"] == "LONG"),
        "short": sum(1 for t in closed if t["direction"] == "SHORT"),
        "long_total_R": round(sum(t["r"] for t in closed
                                  if t["direction"] == "LONG"), 4),
        "short_total_R": round(sum(t["r"] for t in closed
                                   if t["direction"] == "SHORT"), 4),
        "by_symbol": by_symbol,
        "by_publication_slot": by_slot,
    })
    return base


def _hit_rate(closed: Sequence[Dict], n: int) -> float:
    if not closed:
        return 0.0
    return round(sum(1 for t in closed if n in t["targets_hit"]) / len(closed) * 100, 1)
