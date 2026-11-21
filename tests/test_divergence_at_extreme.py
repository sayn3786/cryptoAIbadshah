"""15m scalp: a divergence adds points only at an oversold bottom (bullish) or
overbought top (bearish) - the owner's rule, Nov 2026. Other timeframes unchanged."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_live as sl                                              # noqa: E402
import signals                                                       # noqa: E402


def _div(kind, r_prev, r_curr, **kw):
    return {"type": kind, "strength": 6, "description": kind,
            "points": {"prev": {"timestamp": 0, "price": 1, "rsi": r_prev},
                       "curr": {"timestamp": 1, "price": 1, "rsi": r_curr}}, **kw}


def test_at_extreme():
    assert signals.divergence_at_extreme(_div("bullish", 28, 36))
    assert not signals.divergence_at_extreme(_div("bullish", 45, 52))
    assert signals.divergence_at_extreme(_div("bearish", 72, 63))
    assert not signals.divergence_at_extreme(_div("bearish", 58, 55))
    assert signals.divergence_at_extreme(_div("hidden_bearish", 55, 61))
    assert not signals.divergence_at_extreme({"type": "bullish"})          # no pivots
    assert not signals.divergence_at_extreme({})


def _score(div, flag):
    a = {"rsi_divergence": div}
    if flag:
        a["div_need_extreme"] = True
    sig = signals.generate_signal(a)
    return sig["score_breakdown"].get("rsi_divergence", 0)


def test_score_gated_only_with_the_flag():
    mid = _div("bullish", 45, 52)
    low = _div("bullish", 28, 36)
    assert _score(mid, False) > 0              # other timeframes: unchanged
    assert _score(mid, True) == 0              # 15m: not at an oversold bottom -> no points
    assert _score(low, True) == _score(low, False) > 0


def test_scalp_reversal_reads_gated():
    bars = [{"timestamp": 15 * 60_000 * i, "open": 1, "high": 1, "low": 1, "close": 1} for i in range(10)]
    a_mid = {"rsi_divergence": _div("bullish", 45, 52, forming=True)}
    a_low = {"rsi_divergence": _div("bullish", 30, 37, forming=True)}
    assert "divergence" not in sl.reversal_reads(a_mid, bars)["LONG"]
    assert sl.reversal_reads(a_low, bars)["LONG"]["divergence"] == 1.0


def test_scalp_engine_read_sets_the_flag():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    i = src.index("def _scalp_engine_read")
    assert 'a["div_need_extreme"] = True' in src[i:i + 600]
