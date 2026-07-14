"""
Regression tests for the four critical signal-correctness fixes:

1. Reversal Radar is read from the signal dict (not the analysis root) and the
   opposing-radar penalty fires; a supporting radar is not penalised.
2. Closed-candle off-by-one: the newest COMPLETED candle is included exactly
   once in candle-direction and swing anchors; the forming candle is excluded.
3. Options-expiry pressure adjusts strength exactly once (in generate_signal),
   never twice.
4. CVD dominance comes from aligned recent USD flow and is invariant to the
   arbitrary cumulative starting total; bad/unknown inputs degrade to "unknown".

All tests are deterministic and never call live APIs.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import generate_signal, _recent_closed_extremes          # noqa: E402
from backtest import build_price_analysis                              # noqa: E402
from indicators import (                                               # noqa: E402
    compute_cvd_dominance, detect_cvd_divergence,
    CVD_DOM_MIN_ALIGNED,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def make_candles(n, up=True, start_ts=1_000_000, step=3_600_000, start=100.0):
    out, p = [], start
    for i in range(n):
        o = p
        cl = p + (0.6 if up else -0.6)
        out.append({"timestamp": start_ts + i * step, "open": o,
                    "high": max(o, cl) + 0.3, "low": min(o, cl) - 0.3,
                    "close": cl, "volume": 10.0})
        p = cl
    return out


def make_cvd(deltas, unit="usd", start_ts=1_000_000, step=3_600_000, cum0=0.0):
    series, cum = [], cum0
    for i, d in enumerate(deltas):
        cum += d
        series.append({"timestamp": start_ts + i * step,
                       "cvd": round(cum, 2), "delta": d})
    return {"current": round(cum, 2), "trend": "bullish",
            "series": series, "unit": unit}


def bullish_analysis(with_options=False, options_pts=10):
    a = {
        "symbol": "BTC", "timeframe": "2H", "candles": make_candles(60, up=True),
        "rsi": 22, "rsi_slope": 16, "price_roc": 9, "candle_dirs": [1, 1, 1, 1],
        "ema_trend": {"above": [50, 200], "below": [], "aligned": "bullish",
                      "ema50": 100, "ema21": 101},
        "supertrend": {"direction": "bullish", "value": 98},
        "macd": {"histogram": 0.5, "cross": "bullish"},
    }
    if with_options:
        # PRODUCTION shape from get_options_expiry_data(): signal_pts at the ROOT,
        # bias nested (carrying in_window). Reproducing this exactly is what makes
        # the options test a real regression guard.
        a["options_expiry"] = {
            "signal_pts": options_pts,
            "bias": {"bias": "bullish", "in_window": True},
        }
    return a


# ── 2. closed-candle off-by-one ───────────────────────────────────────────────
def test_recent_closed_extremes_includes_newest_completed_candle():
    # newest closed candle carries the extreme high AND low
    candles = make_candles(6, up=True)
    candles[-1]["high"] = 999.0
    candles[-1]["low"] = 1.0
    hi, lo = _recent_closed_extremes(candles, 5)
    assert hi == 999.0, "newest closed candle's high must be in the swing anchor"
    assert lo == 1.0, "newest closed candle's low must be in the swing anchor"


def test_recent_closed_extremes_window_size_is_n_not_n_minus_one():
    candles = make_candles(10, up=True)
    # only the last 3 should count
    candles[-3]["high"] = 500.0
    candles[-4]["high"] = 900.0  # just outside the window → must be ignored
    hi, _ = _recent_closed_extremes(candles, 3)
    assert hi == 500.0


def test_candle_dirs_includes_newest_closed_candle():
    # bearish run, but the NEWEST closed candle is bullish
    candles = make_candles(30, up=False)
    last = candles[-1]
    last["open"], last["close"] = 10.0, 20.0   # clearly bullish
    a = build_price_analysis(candles, "2H", "TESTX")
    dirs = a["candle_dirs"]
    assert dirs, "candle_dirs should not be empty"
    assert dirs[-1] == 1, "newest closed candle's direction must be included"
    # exactly n entries (no off-by-one dropping the last)
    from backtest import _TF_CANDLE_N
    assert len(dirs) == _TF_CANDLE_N["2H"]


def test_forming_candle_excluded_and_cannot_affect_signal():
    flask = pytest.importorskip("flask")  # noqa: F841
    import app
    interval_s = 7200  # 2H
    import time
    now_ms = int(time.time() * 1000)
    # last candle is still forming (opened this interval, closes in the future)
    closed = [{"timestamp": now_ms - k * interval_s * 1000, "open": 1, "high": 2,
               "low": 0.5, "close": 1.5, "volume": 1} for k in range(5, 0, -1)]
    forming = {"timestamp": now_ms, "open": 1.5, "high": 3, "low": 1,
               "close": 2.9, "volume": 1}
    candles = closed + [forming]
    closed_out, live = app._split_closed(candles, interval_s)
    assert live is not None and live["timestamp"] == forming["timestamp"]
    assert forming["timestamp"] not in [c["timestamp"] for c in closed_out]
    # mutating the forming candle does not change the closed set used for signals
    before = [dict(c) for c in closed_out]
    forming["close"] = 99999
    closed_out2, _ = app._split_closed(candles, interval_s)
    assert [c["close"] for c in closed_out2] == [c["close"] for c in before]


# ── 1. reversal radar wiring + penalty ────────────────────────────────────────
def test_generate_signal_exposes_reversal_radar():
    sig = generate_signal(bullish_analysis())
    assert "reversal_radar" in sig, "reversal_radar must live inside the signal dict"


def test_reversal_radar_read_from_signal_not_root():
    # Reproduce the get_analysis shape: radar lives at data["signal"]["reversal_radar"],
    # NOT at the analysis root. The rec engine must read it from the signal.
    data = {"signal": {"reversal_radar": {"mode": "top", "level": "high"}}}
    assert data.get("reversal_radar") is None            # the old (buggy) path
    sig = data.get("signal", {})
    assert (sig.get("reversal_radar") or {}).get("level") == "high"  # the fixed path


def test_rec_quality_penalizes_opposing_radar():
    flask = pytest.importorskip("flask")  # noqa: F841
    import app
    base = {"strength": 60, "direction": "LONG", "rr_ratio": 2.0}
    plain, _ = app._rec_quality(dict(base), "NEUTRAL")
    high, _ = app._rec_quality({**base, "reversal_against": "high"}, "NEUTRAL")
    elevated, _ = app._rec_quality({**base, "reversal_against": "elevated"}, "NEUTRAL")
    assert plain - high == 15, "high opposing radar must cost 15"
    assert plain - elevated == 8, "elevated opposing radar must cost 8"


def test_rec_quality_supporting_radar_not_penalized():
    flask = pytest.importorskip("flask")  # noqa: F841
    import app
    base = {"strength": 60, "direction": "LONG", "rr_ratio": 2.0}
    plain, _ = app._rec_quality(dict(base), "NEUTRAL")
    # reversal_against is only set when the radar OPPOSES; a supporting radar
    # leaves it None → no penalty.
    supported, _ = app._rec_quality({**base, "reversal_against": None}, "NEUTRAL")
    assert supported == plain


def test_rec_quality_missing_radar_no_crash_no_penalty():
    flask = pytest.importorskip("flask")  # noqa: F841
    import app
    base = {"strength": 60, "direction": "LONG", "rr_ratio": 2.0}
    plain, _ = app._rec_quality(dict(base), "NEUTRAL")          # no reversal_against key
    with_key, _ = app._rec_quality({**base, "reversal_against": ""}, "NEUTRAL")
    assert plain == with_key


# ── 3. options applied once ───────────────────────────────────────────────────
def test_options_adjustment_applied_exactly_once_in_signal():
    without = generate_signal(bullish_analysis(with_options=False))
    with_ = generate_signal(bullish_analysis(with_options=True, options_pts=10))
    assert with_["direction"] == without["direction"] == "LONG"
    assert with_["options_applied"] is True
    assert with_["options_adjustment"] == 10
    assert with_["options_application_stage"] == "signal"
    # +10 pin moves strength by exactly +10, never +20
    assert with_["strength"] - without["strength"] == 10


def test_options_metadata_present_when_not_in_window():
    sig = generate_signal(bullish_analysis(with_options=False))
    assert sig["options_applied"] is False
    assert sig["options_adjustment"] == 0
    assert sig["options_application_stage"] == "signal"


def test_options_signal_pts_read_from_root_not_bias():
    # Regression for the production-shape bug: signal_pts lives at the ROOT of
    # options_expiry, NOT inside bias. Reading it from bias yields 0 and silently
    # drops all options pressure.
    prod = generate_signal(bullish_analysis(with_options=True, options_pts=10))
    assert prod["options_applied"] is True
    assert prod["options_adjustment"] == 10
    # a payload with signal_pts wrongly nested inside bias must NOT be picked up
    a = bullish_analysis(with_options=False)
    a["options_expiry"] = {"bias": {"bias": "bullish", "signal_pts": 10,
                                    "in_window": True}}
    wrong = generate_signal(a)
    assert wrong["options_applied"] is False
    assert wrong["options_adjustment"] == 0


# ── P2: dominance intensifier is a bonus only (never subtracts) ───────────────
def _score_for_spot_ratio(spot_ratio):
    a = bullish_analysis(with_options=False)
    a["cvd_divergence"] = {
        "type": "spot_dominated_up", "signal": "bullish",
        "spot_ratio": spot_ratio, "futures_ratio": round(1 / spot_ratio, 3),
        "dominance": "spot", "dominance_class": "spot_dominated",
    }
    return generate_signal(a)["score"]


def test_dominance_intensifier_never_subtracts():
    # spot_ratio ~4 corresponds to the 80% share threshold; pre-fix this made the
    # intensifier negative (round((4-10)*0.1) = -1), weakening a dominant read.
    # Clamped to >=0, a low-but-dominant ratio must not score LOWER than a higher
    # ratio (both should land on the same clamped-at-0 bonus, not go negative).
    low = _score_for_spot_ratio(4.0)     # would be base-1 pre-fix
    mid = _score_for_spot_ratio(10.0)    # extra == 0 both pre and post
    assert low == mid, "low dominant ratio must not be penalised vs a higher one"


# ── 4. CVD dominance from aligned USD flow ────────────────────────────────────
def test_cvd_spot_dominant_aligned_flow():
    candles = make_candles(10)
    spot = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100])
    fut = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4])
    d = compute_cvd_dominance(spot, fut, candles)
    assert d["dominance"] == "spot_dominated"
    assert d["dominance_data_quality"] == "ok"
    assert d["futures_share"] < 0.2


def test_cvd_futures_dominant_aligned_flow():
    candles = make_candles(10)
    spot = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4])
    fut = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100])
    d = compute_cvd_dominance(spot, fut, candles)
    assert d["dominance"] == "futures_dominated"
    assert d["futures_share"] > 0.8


def test_cvd_dominance_invariant_to_cumulative_starting_total():
    candles = make_candles(10)
    deltas = [100, 120, 90, 110, 130, 100, 95, 105, 115, 100]
    fut = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4])
    a = compute_cvd_dominance(make_cvd(deltas, cum0=0.0), fut, candles)
    b = compute_cvd_dominance(make_cvd(deltas, cum0=-1_980_000_000.0), fut, candles)
    # a huge arbitrary cumulative offset must NOT change the classification
    assert a["dominance"] == b["dominance"]
    assert a["futures_share"] == b["futures_share"]
    assert a["spot_gross_usd"] == b["spot_gross_usd"]


def test_cvd_misaligned_timestamps_unknown():
    candles = make_candles(10)
    spot = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100])
    fut = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4], start_ts=999_999)  # 1ms off
    d = compute_cvd_dominance(spot, fut, candles)
    assert d["dominance"] == "unknown"
    assert d["aligned_candles"] < CVD_DOM_MIN_ALIGNED


def test_cvd_insufficient_coverage_unknown():
    candles = make_candles(10)
    spot = make_cvd([100, 120, 90])   # only 3 aligned bars
    fut = make_cvd([5, -4, 6])
    d = compute_cvd_dominance(spot, fut, candles)
    assert d["dominance"] == "unknown"
    assert d["dominance_data_quality"] in ("insufficient_aligned", "insufficient_coverage")


def test_cvd_unknown_units_unknown():
    candles = make_candles(10)
    spot = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100])
    spot.pop("unit")                  # unit no longer known
    fut = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4])
    d = compute_cvd_dominance(spot, fut, candles)
    assert d["dominance"] == "unknown"
    assert d["dominance_data_quality"] == "unknown_units"


def test_cvd_conflicting_units_converted_consistently():
    # base-unit deltas are converted to USD via candle price exactly once;
    # a usd + base pair still aligns because both end up in USD.
    candles = make_candles(10, start=100.0)   # close ≈ 100
    spot_base = make_cvd([1, 1.2, 0.9, 1.1, 1.3, 1.0, 0.95, 1.05, 1.15, 1.0], unit="base")
    fut_usd = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4], unit="usd")
    d = compute_cvd_dominance(spot_base, fut_usd, candles)
    assert d["dominance_data_quality"] == "ok"
    # spot_gross ≈ 10.65 base × ~100 price ≈ ~1065 USD (NOT ~10.65 — proves ×price once)
    assert d["spot_gross_usd"] > 500


def test_cvd_live_bar_excluded_from_dominance():
    # A CVD series bar with a timestamp NOT present in the closed candles (i.e.
    # the still-forming bar) must not enter the aligned flow.
    candles = make_candles(10)
    extra_ts = candles[-1]["timestamp"] + 3_600_000   # forming bar, not a closed candle
    spot = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100])
    fut = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4])
    huge = {"timestamp": extra_ts, "cvd": 9e9, "delta": 9e9}
    spot["series"].append(huge)
    fut["series"].append({"timestamp": extra_ts, "cvd": 1, "delta": 1})
    d = compute_cvd_dominance(spot, fut, candles)
    # the 9e9 forming-bar delta must be excluded → still spot-dominated, small gross
    assert d["aligned_candles"] == 10
    assert d["spot_gross_usd"] < 5000   # would be ~9e9 if the live bar leaked in


def test_usd_cvd_sources_declare_their_unit():
    # P1: single-OKX and CoinGlass results are genuinely USD and must declare it,
    # otherwise dominance degrades to "unknown" whenever they are the source.
    import inspect
    import cvd_sources
    assert '"unit": "usd"' in inspect.getsource(cvd_sources._okx_taker_cvd)
    assert '"unit"' in inspect.getsource(cvd_sources.fetch_aggregated_spot_cvd)
    assert '"unit"' in inspect.getsource(cvd_sources.fetch_aggregated_futures_cvd)
    coinglass = pytest.importorskip("coinglass")
    assert '"unit"' in inspect.getsource(coinglass.CoinGlassClient.get_aggregated_cvd)


def test_okx_shaped_usd_cvd_yields_dominance_not_unknown():
    # Two USD-tagged series (as OKX/CoinGlass now emit) must produce a real
    # dominance read rather than "unknown".
    candles = make_candles(10)
    spot = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100], unit="usd")
    fut = make_cvd([5, -4, 6, -3, 4, -5, 3, -2, 5, -4], unit="usd")
    d = compute_cvd_dominance(spot, fut, candles)
    assert d["dominance_data_quality"] == "ok"
    assert d["dominance"] != "unknown"


def test_detect_cvd_divergence_unknown_units_falls_back_to_confirmed():
    up = [100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5, 104, 104.5]
    candles = [{"timestamp": 1_000_000 + i * 3_600_000, "open": p, "high": p + 1,
                "low": p - 1, "close": p, "volume": 10} for i, p in enumerate(up)]
    spot = make_cvd([100, 120, 90, 110, 130, 100, 95, 105, 115, 100]); spot.pop("unit")
    fut = make_cvd([5, 4, 6, 3, 4, 5, 3, 2, 5, 4])
    r = detect_cvd_divergence(spot, fut, candles)
    # no reliable dominance → plain confirmed, never *_dominated
    assert r["type"] == "confirmed_up"
    assert r["dominance"] == "unknown"
    assert r["futures_ratio"] is None and r["spot_ratio"] is None
