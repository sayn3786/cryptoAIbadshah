"""Nov 10 audit: what the dashboard says now matches what the code does.

Score fixes: mock / wrong-window OI is not scored; a mock liquidation
estimate never leans the max-pain bias; the OBV brake reads the real CVD
divergence (no double-count); an invalidated RSI reversal marker docks
nothing. Display fixes are checked in the dashboard source.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signals                                                         # noqa: E402

HOUR = 3_600_000


def _candles(n=60, up=True):
    out, p = [], 100.0
    for i in range(n):
        cl = p + (0.4 if up else -0.4)
        out.append({"timestamp": i * HOUR, "open": p, "high": max(p, cl) + 0.3,
                    "low": min(p, cl) - 0.3, "close": cl, "volume": 10.0})
        p = cl
    return out


def _analysis(**kw):
    a = {"symbol": "TAO", "timeframe": "1D", "candles": _candles(),
         "rsi": 50, "rsi_slope": 0, "price_roc": 0.1, "candle_dirs": [1, -1, 1, -1],
         "ema_trend": {"above": [], "below": [], "aligned": "neutral", "ema50": 100, "ema21": 100},
         "supertrend": {"direction": "neutral", "value": 100},
         "macd": {"histogram": 0.0, "cross": "none"}}
    a.update(kw)
    return a


def _reasons(sig):
    return " ".join(sig["bullish_reasons"] + sig["bearish_reasons"])


# ── 1. OI: only a real ~5-candle change is scored ───────────────────────────

def test_unscored_oi_earns_no_points():
    scored = signals.generate_signal(_analysis(open_interest={
        "change_pct": 17.0, "thr_strong": 6.0, "thr_quad": 3.0, "scored": True}))
    assert "new longs opening" in _reasons(scored)
    mock = signals.generate_signal(_analysis(open_interest={
        "change_pct": 17.0, "thr_strong": 6.0, "thr_quad": 3.0, "scored": False}))
    assert "OI" not in _reasons(mock)
    base = signals.generate_signal(_analysis())
    assert mock["score"] == base["score"]                      # as if there were no OI


def test_the_app_marks_which_oi_it_can_score():
    pytest.importorskip("flask")
    import app
    okx = {"source": "okx", "window_bars": 5, "change_pct": 3}
    app._mark_oi_scored(okx)
    assert okx["scored"] is True
    for src in ("mock", "coinglass", "binance"):
        o = {"source": src, "change_pct": 17}
        app._mark_oi_scored(o)
        assert o["scored"] is False and o["not_scored"]
    m = {"source": "mock"}
    app._mark_oi_scored(m)
    assert m["not_scored"].startswith("estimate only")
    assert app._liq_for_bias({"source": "mock", "longs_liquidated": 9e9}) == {}
    real = {"source": "binance", "longs_liquidated": 1.0}
    assert app._liq_for_bias(real) is real
    # a mock liquidation estimate leans nothing on its own
    assert app._liquidation_bias(app._liq_for_bias(
        {"source": "mock", "longs_liquidated": 0, "shorts_liquidated": 1e9}), {})["direction"] == "balanced"


# ── 3. OBV fires only when CVD stayed silent ────────────────────────────────

def _obv_reason_present(sig):
    return any("OBV" in r for r in sig["bullish_reasons"] + sig["bearish_reasons"])


def test_obv_reads_the_real_cvd_divergence():
    long_kw = dict(supertrend={"direction": "bullish", "value": 90},
                   macd={"histogram": 1.0, "cross": "bullish"},
                   ema_trend={"above": [7, 21, 50, 200], "below": [], "aligned": "bullish",
                              "trend": "bullish", "ema50": 95, "ema21": 98},
                   obv={"divergence": "bearish"})
    silent = signals.generate_signal(_analysis(
        **long_kw, cvd_divergence={"type": "neutral", "signal": "neutral"}))
    assert silent["direction"] == "LONG"
    assert any("OBV diverges against the long" in r for r in silent["bearish_reasons"])
    spoke = signals.generate_signal(_analysis(
        **long_kw, cvd_divergence={"type": "absorption_up", "signal": "bearish"}))
    assert not any("OBV" in r for r in spoke["bearish_reasons"])     # CVD spoke: no double-count


# ── 4. an invalidated RSI reversal marker docks nothing ─────────────────────

def test_an_invalidated_reversal_marker_is_void():
    fresh = [{"kind": "overbought_top", "bars_ago": 3, "status": "active", "timestamp": 1}]
    void = [{"kind": "overbought_top", "bars_ago": 3, "status": "invalidated", "timestamp": 1}]
    assert signals.rsi_reversal_delta("LONG", fresh) < 0
    assert signals.rsi_reversal_delta("LONG", void) == 0


# ── display fixes (5-8, netflow) ────────────────────────────────────────────

def test_the_cards_say_what_the_score_does():
    js = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "js", "dashboard.js")).read()
    assert "r8 > 0.015 ? 'Longs paying" in js and "r8 < -0.005 ? 'Shorts paying" in js   # 5
    assert "bull_surge:" in js and "bear_collapse:" in js                                 # 6
    assert "const bas = bb.breakout_after_squeeze;" in js                                  # 7
    assert "Number(ema.price) > Number(r.v)" in js                                         # 8
    assert "Not scored: ${oi.not_scored" in js                                             # 1 card
    assert "const bigT = sym === 'BTC' ? 1000 : 10000" in js                               # netflow
