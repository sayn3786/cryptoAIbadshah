"""
Trade-outcome factor study: factors known at entry, WITH vs WITHOUT, in both
halves; a factor is consistent only if it points the same way in both.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import factor_study as fs                                             # noqa: E402
from test_cadence_compare import market, stub                         # noqa: E402,F401

D = fs.DAY_MS


def _flip(tf, typ, d):
    return {"tf": tf, "kind": "indicator_flip", "type": typ, "direction": d, "status": "active"}


def _cand(**kw):
    base = {"direction": "LONG", "strength": 72, "h1_strength": 75, "h2_strength": 71,
            "rr_ratio": 2.2, "btc_adj": 3, "htf_4h_dir": "LONG", "structure_adjustment": 2,
            "fib_bias": None, "fib_in_zone": None, "h2_exhausted": False,
            "reversal_against": None}
    return {**base, **kw}


def test_signal_factors():
    f = fs.factors(_cand(), [])
    assert f["strength >= 69"] and f["1H and 2H both >= 70"] and f["R:R >= 2"]
    assert f["BTC with the trade"] and f["4H trend agrees"] and f["structure backed the trade"]
    assert not f["structure fought the trade"] and not f["against the Fibonacci pocket"]
    g = fs.factors(_cand(strength=60, h1_strength=80, h2_strength=55, btc_adj=-2,
                         htf_4h_dir="SHORT", structure_adjustment=-3, fib_bias="short",
                         fib_in_zone=True, h2_exhausted=True, reversal_against="x"), [])
    assert g["strength < 62"] and g["1H and 2H differ by >= 20"] and g["BTC against the trade"]
    assert g["4H trend opposes"] and g["structure fought the trade"]
    assert g["against the Fibonacci pocket"] and g["2H exhausted"] and g["reversal against"]


def test_read_factors_are_relative_to_the_trade_direction():
    reads = [_flip("1D", "ichimoku", "bullish"), _flip("1D", "supertrend", "bullish"),
             _flip("1W", "ema50", "bullish"), _flip("1W", "supertrend", "bullish"),
             _flip("1D", "ema50", "bullish"),
             {"tf": "1D", "kind": "rsi_swing", "direction": "bullish", "status": "active"}]
    lf = fs.factors(_cand(), reads)
    assert lf["1D Ichimoku + SuperTrend with the trade"] and lf["2+ weekly reads with the trade"]
    assert lf["daily trend + weekly both with the trade"]
    assert lf["1D bottom/top read with the trade (counter-trend)"]
    assert lf["1D EMA 50 cross with + 1W SuperTrend with"]
    sf = fs.factors(_cand(direction="SHORT", htf_4h_dir="SHORT"), reads)
    assert sf["2+ weekly reads against the trade"] and sf["1D trend flip against the trade"]
    assert not sf["1D trend flip with the trade"] and not sf["long"]


def test_washout_and_short_top_factors():
    reads = [_flip("1D", "macd", "bearish"), _flip("1D", "supertrend", "bearish")]
    assert fs.factors(_cand(), reads)["1D MACD + SuperTrend against the trade (washout)"]
    top = [{"tf": "1D", "kind": "rsi_swing", "direction": "bearish", "status": "active"},
           _flip("1W", "ichimoku", "bearish")]
    assert fs.factors(_cand(direction="SHORT"), top)["1D top read + weekly with a short"]
    assert not fs.factors(_cand(direction="SHORT"), top[:1])["1D top read + weekly with a short"]


def test_reads_at_the_last_daily_close():
    tl = {"closes": [10 * D, 11 * D], "reads": [["a"], ["b"]]}
    assert fs.reads_at(tl, 11 * D + 1) == ["b"] and fs.reads_at(tl, 10 * D) == ["a"]
    assert fs.reads_at(tl, 9 * D) == [] and fs.reads_at(None, 1) == []


def _t(net, **flags):
    return {"net_pct": net, "slot_ms": 0, "factors": flags}


def test_compare_factor_with_vs_without():
    trades = [_t(2.0, x=True), _t(1.0, x=True), _t(3.0, x=True),
              _t(-1.0, x=False), _t(0.0, x=False), _t(-2.0, x=False)]
    r = fs.compare_factor(trades, "x")
    assert r["n_with"] == 3 and r["avg_with"] == 2.0 and r["avg_without"] == -1.0
    assert r["diff"] == 3.0 and r["win_with"] == 100.0 and r["t"] > 3
    assert fs.compare_factor(trades[:3], "x")["diff"] is None       # nothing without it


def test_consistent_needs_both_halves_same_sign_and_enough():
    good = ([{**_t(1.0, f=True), "slot_ms": s} for s in (1, 5)] * 40
            + [{**_t(-0.5 + (i % 3) * 0.1, f=False), "slot_ms": s}
               for i, s in enumerate((1, 5) * 40)])
    flip = ([{**_t(1.0, f=True), "slot_ms": 1}] * 40 + [{**_t(-1.0, f=True), "slot_ms": 5}] * 40
            + [{**_t(0.0 + (i % 2) * 0.1, f=False), "slot_ms": s}
               for i, s in enumerate((1, 5) * 40)])
    [g] = fs.study(good, split_ms=3)
    [x] = fs.study(flip, split_ms=3)
    assert g["consistent"] and g["pooled"]["diff"] > 0
    assert not x["consistent"]


def test_run_and_report(stub):
    m = market(("BTC", "ETH", "SOL", "LINK"))
    daily = {s: [{"timestamp": i * D, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
                 for i in range(20_000)] for s in ("ETH", "SOL", "LINK")}
    res = fs.run(m, daily, lambda closed, tf: [])
    assert res["trades"] == res["trades_a"] + res["trades_b"] > 0
    assert {r["factor"] for r in res["rows"]} >= {"strength >= 69", "long"}
    text = fs.render_telegram(res)
    assert text.startswith("🧮") and "NOT CONSISTENT" in text
