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

__all__ = ["build_snapshot", "SNAPSHOT_INDICATOR_KEYS", "redact"]

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
    }


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
        "volatility_regime": vol_regime.get("regime") or vol_regime.get("level"),

        # Volume
        "volume_signal": vol_sig.get("signal"),
        "volume_ratio": _num(vol_sig.get("ratio")),
        "volume_current": _num(vol_sig.get("current")),
        "volume_average": _num(vol_sig.get("average")),
        "spot_cvd_trend": spot_cvd.get("trend"),
        "futures_cvd_trend": fut_cvd.get("trend"),

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

        # The decision itself
        "signal_score": _num(signal.get("score")),
        "signal_strength": _num(signal.get("strength")),
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
