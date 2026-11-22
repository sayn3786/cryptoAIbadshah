"""Money leans live on ALT 1D signals at +/-5 (owner, Nov 22 2026)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import signals                                                       # noqa: E402

_CACHE = {}


def _base():
    if "a" not in _CACHE:
        import candle_analysis
        px, cs = 100.0, []
        for k in range(240):
            o = px
            px *= 0.99 if k % 5 else 1.012
            cs.append({"timestamp": k * 86_400_000, "open": o, "high": max(o, px) * 1.01,
                       "low": min(o, px) * 0.99, "close": px, "volume": 1000 + k})
        _CACHE["a"] = candle_analysis.build_candle_analysis(cs, "1D", "SOL")
    return dict(_CACHE["a"])


def test_each_lean_moves_strength_5():
    plain = signals.generate_signal(_base())
    d = plain["direction"]
    assert d in ("LONG", "SHORT") and 10 <= plain["strength"] <= 90
    opp = "LONG" if d == "SHORT" else "SHORT"
    both = signals.generate_signal({**_base(), "money_leans": {"premium": d, "stable": d}})
    one_against = signals.generate_signal({**_base(), "money_leans": {"premium": opp, "stable": None}})
    assert both["strength"] == min(100, plain["strength"] + 10)
    assert one_against["strength"] == max(0, plain["strength"] - 5)
    reasons = both["bullish_reasons"] if d == "LONG" else both["bearish_reasons"]
    assert any("Coinbase premium leans" in r for r in reasons)


def test_live_reading_needs_freshness(monkeypatch):
    now = int(time.time() * 1000)
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: {"day": now - 86_400_000,
                                                               "premium": "SHORT", "stable": None})
    assert appmod._money_leans_live() == {"premium": "SHORT", "stable": None}
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: {"day": now - 5 * 86_400_000,
                                                               "premium": "SHORT"})
    assert appmod._money_leans_live() == {}


def test_wired_for_alt_1d_only():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    assert 'if timeframe == "1D" and not business and symbol != "BTC":' in src
