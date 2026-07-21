"""
Regression tests for funding-interval normalization.

Perps run different funding cadences (8h standard; 4h — e.g. TAO — and 1h
increasingly common). Funding thresholds are 8h-calibrated, so the raw
per-interval rate must be normalized to a per-8h basis before comparison —
otherwise a genuinely extreme 4h rate reads as only half as extreme.

Pure/synthetic; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from binance import (                                                   # noqa: E402
    infer_funding_interval_hours, normalize_funding_8h, FUNDING_STD_HOURS,
)
from signals import _funding_8h, generate_signal                        # noqa: E402

HOUR_MS = 3_600_000
T0 = 1_000_000


# ── interval inference ──────────────────────────────────────────────────────────
def _times(interval_h, n=10, start=T0):
    return [start + i * interval_h * HOUR_MS for i in range(n)]


def test_infer_interval_common_cadences():
    assert infer_funding_interval_hours(_times(8)) == 8
    assert infer_funding_interval_hours(_times(4)) == 4
    assert infer_funding_interval_hours(_times(1)) == 1
    assert infer_funding_interval_hours(_times(2)) == 2


def test_infer_interval_robust_to_a_gap_and_shuffle():
    ts = _times(4, n=10)
    del ts[5]                       # one missing funding event
    ts = ts[::-1]                   # unsorted input
    assert infer_funding_interval_hours(ts) == 4


def test_infer_interval_defaults_to_8_when_insufficient():
    assert infer_funding_interval_hours([]) == FUNDING_STD_HOURS
    assert infer_funding_interval_hours([T0]) == FUNDING_STD_HOURS


def test_normalize_funding_8h():
    assert normalize_funding_8h(0.015, 4) == 0.03      # 4h → doubled
    assert normalize_funding_8h(0.01, 8) == 0.01       # 8h → unchanged
    assert normalize_funding_8h(0.005, 1) == 0.04      # 1h → ×8


# ── signals helper ──────────────────────────────────────────────────────────────
def test_funding_8h_helper_prefers_current_8h():
    assert _funding_8h({"current": 0.015, "current_8h": 0.03,
                        "interval_hours": 4}) == 0.03
    # derive from interval when current_8h absent
    assert _funding_8h({"current": 0.015, "interval_hours": 4}) == 0.03
    # 8h / unknown interval → unchanged
    assert _funding_8h({"current": 0.02}) == 0.02
    # absent data preserves the None ('no data') path
    assert _funding_8h(None) is None
    assert _funding_8h({}) is None


# ── end-to-end scoring parity ────────────────────────────────────────────────────
def _mk_candles(n, up=True, start=100.0):
    out, p = [], start
    for i in range(n):
        cl = p + (0.4 if up else -0.4)
        out.append({"timestamp": T0 + i * HOUR_MS, "open": p,
                    "high": max(p, cl) + 0.3, "low": min(p, cl) - 0.3,
                    "close": cl, "volume": 10.0})
        p = cl
    return out


def _analysis(funding):
    return {
        "symbol": "TAO", "timeframe": "1D", "candles": _mk_candles(60, up=True),
        "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": [1, -1, 1, -1],
        "ema_trend": {"above": [], "below": [], "aligned": "neutral",
                      "ema50": 100, "ema21": 100},
        "supertrend": {"direction": "neutral", "value": 100},
        "macd": {"histogram": 0.0, "cross": "none"},
        "funding_rate": funding,
    }


def test_4h_and_8h_equivalent_funding_score_the_same():
    # A 4h rate of -0.015% is the per-8h-equivalent of an 8h rate of -0.03%.
    four_h = generate_signal(_analysis(
        {"current": -0.015, "current_8h": -0.03, "interval_hours": 4}))
    eight_h = generate_signal(_analysis(
        {"current": -0.03, "current_8h": -0.03, "interval_hours": 8}))
    assert four_h["score"] == eight_h["score"], \
        "equivalent funding must score identically regardless of interval"
    # and both are the 'extremely negative' tier (crosses -0.02 on the 8h basis)
    assert any("extremely negative" in r for r in four_h["bullish_reasons"])
    assert "native -0.0150%/4h" in " ".join(four_h["bullish_reasons"])


def test_4h_extreme_not_under_weighted_vs_raw():
    # The SAME raw number read without normalization would only be 'negative'
    # (-0.015 > -0.02) → +15. Normalized to -0.03/8h it's 'extremely negative'
    # → +30. Prove the 4h coin now gets the stronger, correct score.
    base = generate_signal(_analysis(None))["score"]
    norm_sig = generate_signal(_analysis(
        {"current": -0.015, "current_8h": -0.03, "interval_hours": 4}))
    raw_like = generate_signal(_analysis(
        {"current": -0.015, "current_8h": -0.015, "interval_hours": 8}))["score"]
    # extreme tier (+30) vs negative tier (+15) — the engine's uniform damping
    # scales both, so assert the 2:1 relationship rather than raw magnitudes
    assert (norm_sig["score"] - base) == 2 * (raw_like - base) > 0
    assert any("extremely negative" in r for r in norm_sig["bullish_reasons"])


def test_neutral_default_funding_still_neutral_on_4h():
    # TAO's real state in the screenshot: 0.005% on a 4h cycle → per-8h 0.01%,
    # still below every threshold → zero funding points (unchanged behavior).
    base = generate_signal(_analysis(None))["score"]
    tao = generate_signal(_analysis(
        {"current": 0.005, "current_8h": 0.01, "interval_hours": 4}))["score"]
    assert tao == base, "neutral-default funding contributes no points"


def test_long_short_funding_mirror_symmetry():
    neg = generate_signal(_analysis(
        {"current": -0.015, "current_8h": -0.03, "interval_hours": 4}))["score"]
    pos = generate_signal(_analysis(
        {"current": 0.03, "current_8h": 0.06, "interval_hours": 4}))["score"]
    # -0.03/8h → +30 (extreme neg); +0.06/8h → -30 (extreme high)
    base = generate_signal(_analysis(None))["score"]
    assert (neg - base) == -(pos - base)
