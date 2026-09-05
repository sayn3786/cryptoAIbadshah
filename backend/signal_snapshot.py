"""
Builds the decision-time snapshot stored with a published signal.

Two rules drive this module, and both are enforced by construction rather than
by reviewer discipline:

1. **Allow-list, never deny-list.** We copy named fields out of the analysis
   dict. A future analysis key holding an API key, an auth header or a raw
   provider payload cannot leak in, because nothing is copied unless it is
   named here.

2. **Bounded size.** Free-tier storage is finite and this row is written once
   per published signal. Values are scalars and small dicts; candle arrays,
   full series and raw responses are never stored — only counts and
   timestamps that describe them.

The snapshot answers "what did the strategy see when it decided?" so a losing
signal can be explained later. It is analysis input, never live strategy input.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["build_snapshot", "build_card", "CARD_KEYS",
           "SNAPSHOT_INDICATOR_KEYS", "redact"]

# Anything whose key looks like a credential is dropped even if it somehow
# reaches a nested dict we do copy. Belt and braces on top of the allow-list.
_SECRET_HINTS = ("key", "token", "secret", "password", "passwd", "pwd",
                 "authorization", "auth", "credential", "dsn", "connection_string",
                 "database_url", "cookie", "session")

# Scalar indicator fields lifted straight off the analysis dict.
SNAPSHOT_INDICATOR_KEYS = (
    "rsi", "rsi_slope", "price_roc", "signal_price", "live_price",
)

_MAX_STR = 500
_MAX_LIST = 25


def _is_secretish(key: str) -> bool:
    k = str(key).lower()
    return any(h in k for h in _SECRET_HINTS)


def redact(value: Any, _depth: int = 0) -> Any:
    """
    Shrink a value to something safe and small enough to store.

    Drops credential-looking keys, truncates long strings and long lists, and
    refuses to recurse deeply — a nested payload is a sign we are about to
    store something we should not.
    """
    if _depth > 3:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _is_secretish(k):
                continue
            out[str(k)] = redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = list(value)[:_MAX_LIST]
        return [redact(v, _depth + 1) for v in items]
    return str(value)[:_MAX_STR]


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


_MAX_PATTERNS = 5


def _kinds(items: Any) -> list:
    """
    Bounded [{type, status}] for a pattern list.

    Counts alone cannot answer "was the trade taken against a confirmed
    reversal?" — that needs the type and where it was in its lifecycle. Capped
    because a snapshot must stay small; five is more than any symbol shows.
    """
    out = []
    for item in (items if isinstance(items, list) else [])[:_MAX_PATTERNS]:
        if not isinstance(item, dict):
            continue
        out.append({"type": item.get("type") or item.get("kind"),
                    "status": item.get("status")})
    return out


def _divergence_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    RSI divergence AND where it was in its life.

    A divergence is not a fact, it is a fact with an age: the same reading
    scores differently at one bar old and at nine, and `freshness` is the
    multiplier the strategy actually applied. Recording the type without the
    lifecycle would make every divergence look equally strong in hindsight,
    which is precisely the question a postmortem needs to separate.

    All-None when there was no divergence. Absence is recorded as NULL rather
    than as a zero-strength divergence, which would be a different claim.
    """
    d = analysis.get("rsi_divergence")
    if not isinstance(d, dict):
        d = {}
    forming = d.get("forming")
    return {
        "rsi_divergence_type": d.get("type"),
        "rsi_divergence_strength": _num(d.get("strength")),
        "rsi_divergence_status": d.get("status"),
        "rsi_divergence_age_candles": d.get("age_candles"),
        "rsi_divergence_fresh_bars": d.get("fresh_bars"),
        "rsi_divergence_freshness": _num(d.get("freshness")),
        # Provisional second pivot — the divergence was not confirmed yet.
        "rsi_divergence_forming": None if forming is None else bool(forming),
        # A forming divergence whose predicted turn ALREADY happened (price
        # reclaimed + RSI crossed the midline) before the pivot mechanically
        # confirmed — a spent, resolved read rather than a fresh setup.
        "rsi_divergence_played_out": bool(d.get("played_out")),
    }


def _rsi_reversal_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    The most recent RSI swing-reversal marker, recorded so a postmortem can ask
    whether a reversal firing AGAINST the trade preceded a stop.

    ``rsi_markers`` is a list of {timestamp, kind, rsi, price} where kind is
    ``oversold_bottom`` (a bullish reversal) or ``overbought_top`` (bearish). We
    keep only the latest kind, its timestamp, and the count — enough for the
    against-the-trade read, small enough for a once-per-signal row. All-None
    when there were no markers; absence is NULL, not a neutral reading.
    """
    markers = analysis.get("rsi_markers")
    if not isinstance(markers, list) or not markers:
        return {"rsi_reversal_latest": None, "rsi_reversal_latest_ts": None,
                "rsi_reversal_count": 0}
    latest = max(markers, key=lambda m: (m or {}).get("timestamp") or 0)
    return {"rsi_reversal_latest": latest.get("kind"),
            "rsi_reversal_latest_ts": latest.get("timestamp"),
            "rsi_reversal_count": len(markers)}


def _fib_bmsb_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fibonacci golden-pocket alignment (v50 scores it as a brake) and the weekly
    Bull-Market-Support-Band status (still read-only — None on the 2H publish
    path). Recorded so a review can score whether trading against the fib zone
    bias, or below the band, preceded stops. All-None when absent.
    """
    f = analysis.get("fib")
    f = f if isinstance(f, dict) else {}
    b = analysis.get("bmsb")
    b = b if isinstance(b, dict) else {}
    in_zone = bool(f.get("in_golden_pocket") or f.get("in_entry_zone"))
    return {
        "fib_bias": f.get("bias"),                 # long (discount) | short (premium) | None
        "fib_status": f.get("status"),
        "fib_in_zone": in_zone if f else None,     # None when no fib at all
        "fib_in_golden_pocket": bool(f.get("in_golden_pocket")) if f else None,
        "bmsb_status": b.get("status"),            # weekly-only; None on 2H
        "bmsb_close_status": b.get("close_status"),
    }


def _pattern_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Flag / breakout measurements — the things a postmortem asks about."""
    flags = analysis.get("flags") or []
    first = flags[0] if isinstance(flags, list) and flags else {}
    if not isinstance(first, dict):
        first = {}
    return {
        "flag_count": len(flags) if isinstance(flags, list) else 0,
        "flag_type": first.get("type"),
        "flag_status": first.get("status"),
        "flag_break_level": _num(first.get("break_level")),
        "flag_pole_pct": _num(first.get("pole_pct")),
        "flag_bars": first.get("bars"),
        "breakout_volume": redact(first.get("breakout_volume")),
        "retest_state": first.get("retest_state"),
        "reversal_pattern_count": len(analysis.get("reversal_patterns") or []),
        "triangle_pattern_count": len(analysis.get("triangle_patterns") or []),
        "flag_diagnostics": redact(analysis.get("flag_diagnostics")),
        # Only flags[0] was recorded above. A symbol commonly carries several
        # patterns and the one that mattered is often not the first, so keep
        # the whole (bounded) set with its lifecycle state.
        "flags_seen": _kinds(flags),
        "reversal_patterns_seen": _kinds(analysis.get("reversal_patterns")),
        "triangle_patterns_seen": _kinds(analysis.get("triangle_patterns")),
    }


# ── The published card ──────────────────────────────────────────────────────
# What the dashboard rendered for this signal, stored so the card can be served
# back from the database instead of from a cache of a recomputed set. Every key
# here is one the renderer actually reads (dashboard.js _buildRecCard) — same
# allow-list discipline as the snapshot: nothing is copied unless it is named.
#
# Deliberately NOT stored: entry / sl / tp_targets / direction / symbol /
# timeframe. Those are real columns on `signals` and `signal_targets`, and a
# second copy could drift from the record of the decision. The reader fills them
# in from the row.
CARD_KEYS = (
    # Conviction, and how the two timeframes that had to agree scored it.
    # avg_tf_strength is the RANKING key, so the card shows what put this
    # trade in the top three rather than only the 2H number.
    "strength", "display_strength", "h1_strength", "h2_strength",
    "avg_tf_strength", "aligned_tfs",
    # Risk framing
    "rr_ratio", "sl_pct", "tp_pcts", "leverage",
    "vol_tier", "vol_tier_label",
    # BTC context
    "btc_consensus", "btc_corr", "btc_adj", "btc_aligned", "btc_conflict",
    # Higher-timeframe confluence badge
    "mtf_dirs", "mtf_adj", "mtf_aligned", "mtf_confirm", "mtf_counter",
    # Plain-language why
    "reasons",
    # Presentation
    "view_tf", "detected_at",
)
# Deliberately NOT here either:
#   quality_score       — already stored on market_context; one copy, not two.
#   targets_behind_live — which rungs are spent is a fact about the LIVE price,
#                         true at publication and stale by the time the slot is
#                         read back. Storing it would freeze a moving number.


def build_card(rec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    The renderable part of a published recommendation, bounded and redacted.

    Stored alongside the signal so ``/api/recommendations`` can serve what was
    actually published rather than a cached recomputation. Small by
    construction: scalars, short lists and one flat dict of timeframe
    directions. Keys absent from ``rec`` are simply absent here — a card that
    lies about what the strategy reported is worse than a sparse one.
    """
    rec = rec or {}
    return redact({k: rec[k] for k in CARD_KEYS if rec.get(k) is not None})


def build_snapshot(analysis: Dict[str, Any],
                   signal: Dict[str, Any],
                   *,
                   extra_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Return the four JSONB payloads plus the candle count for one signal.

    ``{"indicator_values", "market_context", "source_timestamps",
       "input_candle_count", "data_quality_flags"}``

    Reads only from the analysis/signal dicts the app already builds — this
    performs no network calls and no recomputation, so it cannot change what
    the strategy decided.
    """
    analysis = analysis or {}
    signal = signal or {}

    macd = analysis.get("macd") or {}
    ema_trend = analysis.get("ema_trend") or {}
    supertrend = analysis.get("supertrend") or {}
    bollinger = analysis.get("bollinger") or {}
    stoch = analysis.get("stoch_rsi") or {}
    vol_sig = analysis.get("vol_signal") or {}
    trendline = analysis.get("trendline") or {}
    sr_zones = analysis.get("sr_zones") or {}
    regime = analysis.get("regime") or {}
    vol_regime = analysis.get("vol_regime") or {}
    funding = analysis.get("funding_rate") or {}
    oi = analysis.get("open_interest") or {}
    spot_cvd = analysis.get("spot_cvd") or {}
    fut_cvd = analysis.get("futures_cvd") or {}
    candles = analysis.get("candles") or []

    indicator_values: Dict[str, Any] = {
        # RSI (period is the app-wide default used by calculate_rsi)
        "rsi": _num(analysis.get("rsi")),
        "rsi_period": 14,
        "rsi_slope": _num(analysis.get("rsi_slope")),
        "stoch_rsi_k": _num(stoch.get("k")),
        "stoch_rsi_d": _num(stoch.get("d")),

        # MACD
        "macd": _num(macd.get("macd")),
        "macd_signal_line": _num(macd.get("signal_line")),
        "macd_histogram": _num(macd.get("histogram")),
        "macd_cross": macd.get("cross"),
        "macd_trend": macd.get("trend"),

        # Moving averages
        "ema50": _num(ema_trend.get("ema50")),
        "ema200": _num(ema_trend.get("ema200")),
        "ema_above": redact(ema_trend.get("above")),
        "ema_below": redact(ema_trend.get("below")),

        # Volatility. NOTE: this project has no ATR indicator; it sizes stops
        # from a market-cap volatility tier instead, so that is what is
        # recorded. Storing a fabricated ATR would make the snapshot lie.
        "volatility_tier": signal.get("vol_tier"),
        "volatility_tier_label": signal.get("vol_tier_label"),
        "bollinger_upper": _num(bollinger.get("upper")),
        "bollinger_middle": _num(bollinger.get("middle")),
        "bollinger_lower": _num(bollinger.get("lower")),
        "bollinger_bandwidth": _num(bollinger.get("bandwidth")),
        # vol_regime() labels the tape under "zone" (extreme/elevated/normal/
        # calm); "regime"/"level" are kept as fallbacks for any other shape. The
        # postmortem's violent_volatility_tape flag read all-unknown until this
        # pointed at the key the analysis actually populates.
        "volatility_regime": (vol_regime.get("zone") or vol_regime.get("regime")
                              or vol_regime.get("level")),

        # Volume
        "volume_signal": vol_sig.get("signal"),
        "volume_ratio": _num(vol_sig.get("ratio")),
        "volume_current": _num(vol_sig.get("current")),
        "volume_average": _num(vol_sig.get("average")),
        "spot_cvd_trend": spot_cvd.get("trend"),
        "futures_cvd_trend": fut_cvd.get("trend"),
        # OBV is reporting-only in scoring (it would double-count volume against
        # CVD), but its trend and price-divergence are recorded so a postmortem
        # can learn whether an OBV divergence AGAINST the trade preceded a stop.
        "obv_trend": (analysis.get("obv") or {}).get("trend"),
        "obv_divergence": (analysis.get("obv") or {}).get("divergence"),

        # Fibonacci golden-pocket alignment (v50 brake) + weekly BMSB status.
        **_fib_bmsb_summary(analysis),

        # Trend classification
        "supertrend_direction": supertrend.get("direction"),
        "supertrend_value": _num(supertrend.get("value")),
        "regime": regime.get("regime") or regime.get("state"),

        # Support / resistance
        "support_zone": redact((sr_zones.get("support") or {})),
        "resistance_zone": redact((sr_zones.get("resistance") or {})),
        "trendline_local": redact(trendline.get("local")),
        "trendline_macro": redact(trendline.get("macro")),

        # Patterns / breakout confirmation
        **_pattern_summary(analysis),

        # Divergence, with its age — see _divergence_summary.
        **_divergence_summary(analysis),

        # Latest RSI swing-reversal marker — see _rsi_reversal_summary.
        **_rsi_reversal_summary(analysis),

        # Market-structure confluence. These are the whole point of a postmortem
        # on a losing trade: they record whether the strategy KNEW about a
        # stop-run risk, a chase, or stale structure at decision time — and how
        # much it discounted the trade for it. Without them the snapshot would
        # show the strength but not why it was cut.
        "structure_adjustment": _num(signal.get("structure_adjustment")),
        "structure_factors": redact(signal.get("structure_factors")),
        # Liquidation max-pain squeeze nudge (v46). The signed delta applied and
        # the side it leaned, so a postmortem can ask whether trades taken
        # AGAINST the squeeze (liquidation_adjustment < 0) lost more often.
        "liquidation_adjustment": _num(signal.get("liquidation_adjustment")),
        "liquidation_bias_dir": signal.get("liquidation_bias_dir"),
        # Whether the stop had to be moved clear of a pool, or could not be.
        # A trade that lost with stop_liquidity.blocked set was flagged as
        # sitting in a sweep zone before it was ever taken.
        "stop_liquidity": redact(signal.get("stop_liquidity")),
        # What TP2 was trading to — a liquidity pool or a zone/line.
        "tp_anchor": redact(signal.get("tp_anchor")),

        # The decision itself
        "signal_score": _num(signal.get("score")),
        "signal_strength": _num(signal.get("strength")),
        # Since v44 the published strength is the AVERAGE of the 1H and 2H
        # readings, and an average hides its own disagreement: 70/30 and 50/50
        # both publish as 50. The spread is the feature — a signal strong on 1H
        # and weak on 2H is a different animal from one strong on both, and
        # only one of those looks like chasing.
        "h1_strength": _num(signal.get("h1_strength")),
        "h2_strength": _num(signal.get("h2_strength")),
        "tf_strength_spread": (
            None if signal.get("h1_strength") is None or signal.get("h2_strength") is None
            else round(_num(signal.get("h1_strength")) - _num(signal.get("h2_strength")), 4)
        ),
        "signal_tier": signal.get("tier"),
        "risk_reward_ratio": _num(signal.get("rr_ratio")),
        "stop_loss_pct": _num(signal.get("sl_pct")),
        "take_profit_pcts": redact(signal.get("tp_pcts")),
        "suggested_leverage": _num(signal.get("leverage")),
        "bullish_reasons": redact(signal.get("bullish_reasons")),
        "bearish_reasons": redact(signal.get("bearish_reasons")),
    }

    market_context: Dict[str, Any] = {
        "symbol": analysis.get("symbol"),
        "timeframe": analysis.get("timeframe"),
        "candle_interval_seconds": None,          # filled by the caller
        "data_source": analysis.get("data_source"),
        "demo_mode": bool(analysis.get("demo_mode")),
        "futures_available": bool(analysis.get("futures_available")),
        "market_cap": _num(analysis.get("market_cap")),
        "funding_rate": _num(funding.get("rate") if isinstance(funding, dict) else None),
        "open_interest": _num(oi.get("value") if isinstance(oi, dict) else None),
        "fear_greed": redact(analysis.get("fear_greed")),
        "btc_correlation": None,                  # filled by the caller
        # What BTC was doing when the alt trade was taken, and what the
        # strategy did about it. An alt stopping out while BTC rolled over is
        # not the same failure as one stopping out with BTC aligned, and
        # without these the snapshot cannot tell them apart — the correlation
        # FACTOR alone says how much BTC should matter, never what it did.
        "btc_consensus": signal.get("btc_consensus"),
        "btc_aligned": (None if signal.get("btc_aligned") is None
                        else bool(signal.get("btc_aligned"))),
        "btc_conflict": (None if signal.get("btc_conflict") is None
                         else bool(signal.get("btc_conflict"))),
        "btc_adjustment": _num(signal.get("btc_adj")),
        "exhaustion_flag": bool(signal.get("exhaustion_flag")),
        "reversal_count": signal.get("reversal_count"),
        "reversal_radar": redact(signal.get("reversal_radar")),
        "options_bias": signal.get("options_bias"),
        "options_in_window": signal.get("options_in_window"),
    }
    if extra_context:
        market_context.update(redact(extra_context))

    source_timestamps: Dict[str, Any] = {
        "analysis_generated_at_ms": analysis.get("generated_at"),
        "last_closed_candle_ms": analysis.get("signal_candle_closed_at"),
        "first_candle_ms": (candles[0] or {}).get("timestamp") if candles else None,
        "last_candle_ms": (candles[-1] or {}).get("timestamp") if candles else None,
        "provider": analysis.get("data_source"),
        "data_age_seconds": analysis.get("data_age_seconds"),
    }

    dq_flags: Dict[str, Any] = {
        "data_quality": analysis.get("data_quality"),
        "data_quality_reasons": redact(analysis.get("data_quality_reasons")),
        "missing_futures": not bool(analysis.get("futures_available")),
        "demo_data": bool(analysis.get("demo_mode")),
        "stale": (analysis.get("data_quality") in ("degraded", "invalid")),
    }

    return {
        "indicator_values": redact(indicator_values),
        "market_context": redact(market_context),
        "source_timestamps": redact(source_timestamps),
        "input_candle_count": len(candles),
        "data_quality_flags": redact(dq_flags),
    }
