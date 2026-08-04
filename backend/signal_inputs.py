"""
Every input `generate_signal` reads, classified by whether history can supply it.

A backtest is only as honest as its inventory of what it could not replay. This
is that inventory, written down and testable, rather than a paragraph in a
docstring that goes stale the first time someone adds an indicator.

Five classes:

``CANDLE_DERIVED``
    Computed from OHLCV alone. Deterministic, replayable exactly, and produced
    by `candle_analysis.build_candle_analysis`. A key here that the builder does
    not produce is a parity hole, and a test fails on it.

``EXTERNAL_HISTORICAL``
    Real inputs with a real history that this project does not store — funding,
    open interest, sentiment, macro. Replayable in principle, given timestamped
    snapshots; absent in `price_only`, so their scoring blocks stay dormant.

``LIVE_ONLY``
    Only meaningful at the instant it is read. An order book is the resting
    orders right now; there is no historical order book to fetch, and there
    never will be.

``STATIC_CONFIG``
    Identity, not measurement. Symbol and timeframe.

``EXTERNAL_POINT_IN_TIME``
    External and slot-sensitive: market cap tiers the ATR caps, stop widths,
    target distances and leverage, so substituting today's figure into a slot
    from last March prices the trade against a company size the market did not
    have. Called out separately from EXTERNAL_HISTORICAL because it changes
    entry, stop and targets rather than only the score.

The list is checked against the source: a test greps `analysis.get(...)` out of
signals.py and fails when a key appears that nobody classified. That is the
point — a future production feature cannot be added without someone deciding,
on the record, whether the backtest can replay it.
"""
from __future__ import annotations

__all__ = [
    "CANDLE_DERIVED", "EXTERNAL_HISTORICAL", "LIVE_ONLY", "STATIC_CONFIG",
    "EXTERNAL_POINT_IN_TIME", "ALL_INPUTS", "NESTED_KEYS", "classify",
]

CANDLE_DERIVED = frozenset({
    "candles", "candle_dirs",
    "rsi", "rsi_slope", "price_roc", "rsi_divergence",
    "macd", "ema_trend", "supertrend", "ichimoku", "bollinger", "stoch_rsi",
    "vwap", "vol_signal", "vol_regime",
    "spot_cvd", "cvd_divergence",
    "fvgs", "engulfing", "elliott_wave", "choch", "liq_grab", "acc_setup",
    "trendline", "sr_zones", "flags",
    "reversal_patterns", "triangle_patterns",
    "equal_levels", "bos_streak", "liquidity_pools",
    "deep_swing_highs", "deep_swing_lows",
})

EXTERNAL_HISTORICAL = frozenset({
    "funding_rate", "open_interest", "futures_cvd", "long_short",
    "fear_greed", "news", "macro", "markets", "regime", "event_risk",
    "etf_flows", "options_expiry", "btc_mining", "whale_sells",
    "gomining_tokenomics", "tao_ecosystem",
})

LIVE_ONLY = frozenset({
    "order_book",
})

EXTERNAL_POINT_IN_TIME = frozenset({
    "market_cap",
})

STATIC_CONFIG = frozenset({
    "symbol", "timeframe",
})

ALL_INPUTS = (CANDLE_DERIVED | EXTERNAL_HISTORICAL | LIVE_ONLY
              | EXTERNAL_POINT_IN_TIME | STATIC_CONFIG)

# Sub-keys read off indicator dicts, not off the analysis dict. They appear in a
# naive grep of signals.py and are not inputs in their own right.
NESTED_KEYS = frozenset({
    "above", "below", "ema50", "ema200", "ema7_cross", "price_vs_vwap",
    "short_trend", "slope", "value", "vwap_cross", "zone",
})


def classify(key: str) -> str:
    """Which class an input belongs to, or 'UNCLASSIFIED'."""
    for name, members in (("CANDLE_DERIVED", CANDLE_DERIVED),
                          ("EXTERNAL_HISTORICAL", EXTERNAL_HISTORICAL),
                          ("LIVE_ONLY", LIVE_ONLY),
                          ("EXTERNAL_POINT_IN_TIME", EXTERNAL_POINT_IN_TIME),
                          ("STATIC_CONFIG", STATIC_CONFIG)):
        if key in members:
            return name
    return "UNCLASSIFIED"
