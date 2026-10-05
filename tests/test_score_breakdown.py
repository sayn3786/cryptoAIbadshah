"""
generate_signal's per-section score attribution: report only, and it adds
up to the score it explains.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest                                                          # noqa: E402

import candle_analysis                                                 # noqa: E402
from signals import generate_signal                                    # noqa: E402
from test_cadence_compare import walk                                  # noqa: E402


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_breakdown_sums_to_the_score(seed):
    bars = walk(300, seed=seed)
    sig = generate_signal(candle_analysis.build_candle_analysis(bars, "2H", "ETH"))
    bd = sig["score_breakdown"]
    assert bd and all(v != 0 for v in bd.values())
    assert sum(bd.values()) == pytest.approx(sig["score"], abs=0.05)
    assert "obv_adjustment" in sig and "fib_adjustment" in sig


def test_names_are_the_engine_sections():
    seen = set()
    for seed in range(1, 13):
        bars = walk(300, seed=seed)
        seen |= set(generate_signal(
            candle_analysis.build_candle_analysis(bars, "1H", "SOL"))["score_breakdown"])
    assert seen and seen <= KNOWN


KNOWN = {
    "rsi_level", "rsi_slope", "roc", "candle_consistency", "cvd", "funding",
    "open_interest", "oi_squeeze_fuel", "squeeze_priming", "fvg", "choch",
    "liquidity_grab", "acc_setup", "trend_context", "flags", "reversal_patterns",
    "engulfing", "macd", "trend_ema_supertrend_ichimoku", "ema200_retest",
    "order_book", "netflow", "etf_flows", "macro", "tradfi", "market_regime",
    "gomining", "tao", "long_short_ratio", "fear_greed", "news", "elliott",
    "rsi_divergence", "trendlines", "sr_zones", "bollinger", "vwap", "stoch_rsi",
    "volume", "btc_onchain", "reversal_radar", "group_caps", "confluence",
    "combo_flow_trend", "combo_momentum_trend", "combo_flow_against_trend",
    "combo_momentum_against_trend", "combo_funding_trend", "combo_supertrend_volume",
    "combo_divergence_macd", "combo_bb_squeeze_volume", "combo_hash_ribbon",
    "combo_btc_cycle", "btc_cycle_top", "combo_macro_inflection",
    "combo_etf_reversal", "confluence_multiplier"}
