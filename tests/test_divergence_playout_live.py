"""Played-out confirmed RSI divergences: zero on 1D (live), the flip variant,
and the follow-up tests' rules."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import divergence_playout2_test as d2                                # noqa: E402
import signals                                                       # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_confirmed_divergence_played_out():
    div = {"type": "hidden_bearish", "points": {"curr": {"price": 341.23}}}
    a = {"rsi": 43.1, "candles": [{"close": 190.5}]}
    assert signals.confirmed_divergence_played_out(div, a)
    assert not signals.confirmed_divergence_played_out(div, {**a, "rsi": 55})
    assert not signals.confirmed_divergence_played_out({"type": "bullish", "points": {"curr": {"price": 100}}},
                                                       {"rsi": 52, "candles": [{"close": 102}]})


def _analysis(close):
    return {"rsi": 40.0, "candles": [{"close": close}],
            "rsi_divergence": {"type": "bearish", "strength": 6, "description": "bear div",
                               "points": {"curr": {"price": 100.0}}}}


def test_zero_and_flip_switches_move_the_divergence_points():
    base = signals.generate_signal(_analysis(90.0))["score_breakdown"].get("rsi_divergence", 0)
    zero = signals.generate_signal({**_analysis(90.0), "div_zero_played_out": True})["score_breakdown"].get(
        "rsi_divergence", 0)
    flip = signals.generate_signal({**_analysis(90.0), "div_flip_played_out": True})["score_breakdown"].get(
        "rsi_divergence", 0)
    assert base < 0 and zero == 0 and flip == -base
    # not played out (only 2% below the pivot): the switch changes nothing
    near = signals.generate_signal({**_analysis(98.0), "div_zero_played_out": True})["score_breakdown"].get(
        "rsi_divergence", 0)
    assert near == base


def test_live_on_1d_and_the_paper_book():
    src = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
    assert 'if timeframe == "1D" and not business:\n        analysis["div_zero_played_out"] = True' in src
    sf = open(os.path.join(ROOT, "backend", "sig1d_forward.py"), encoding="utf-8").read()
    assert 'a["div_zero_played_out"] = True' in sf


def test_part2_rules():
    def ev(book, win, n, era_ts):
        return [{"book": book, "ts": era_ts, "trade": {"result": "target" if k < win else "stop",
                                                        "r": 0.95 if k < win else -1.05}} for k in range(n)]
    evs = ev("current", 50, 100, 0) + ev("zeroed", 52, 100, 0) + ev("flipped", 60, 100, 0)
    e = d2.era(evs, "1D")
    assert e["zero_ok"] and e["flip_ok"]
    e2 = d2.era(ev("current", 50, 100, 0) + ev("zeroed", 52, 100, 0) + ev("flipped", 51, 100, 0), "1W")
    assert e2["zero_ok"] and not e2["flip_ok"]
