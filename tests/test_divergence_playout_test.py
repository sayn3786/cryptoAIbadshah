import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import divergence_playout_test as dp                                 # noqa: E402
import reversal_1d_test as rv                                        # noqa: E402


def _a(kind, piv, rsi, forming=False):
    return {"rsi_divergence": {"type": kind, "forming": forming, "points": {"curr": {"price": piv}}}, "rsi": rsi}


def test_played_out_rule():
    assert dp.played_out(_a("hidden_bearish", 341.23, 43.1), 190.5)          # TAO 2W
    assert not dp.played_out(_a("hidden_bearish", 341.23, 55.0), 190.5)      # RSI not back below 50
    assert not dp.played_out(_a("bearish", 100, 40), 98.0)                    # under 3%
    assert dp.played_out(_a("bullish", 100, 52), 104.0)
    assert not dp.played_out(_a("bullish", 100, 52, forming=True), 104.0)    # forming: already handled
    assert not dp.played_out({}, 1.0)


def test_zeroed_takes_out_only_the_divergence_points():
    sig = {"direction": "SHORT", "strength": 70, "score_breakdown": {"trend": -80, "rsi_divergence": -14}}
    z = dp.zeroed(sig)
    assert z["direction"] == "SHORT" and z["strength"] < 70
    weak = {"direction": "SHORT", "strength": 20, "score_breakdown": {"trend": -25, "rsi_divergence": -14}}
    assert dp.zeroed(weak)["direction"] == "NEUTRAL"


def test_scan_books():
    D = rv.DAY_MS
    daily = [{"timestamp": k * D, "open": 100, "high": 103, "low": 97, "close": 100}
             for k in range(rv.LOOKBACK + 40)]
    sig = {"direction": "SHORT", "strength": 63, "score_breakdown": {"trend": -126, "rsi_divergence": -14}}
    fn = lambda w, s, f: (_a("bearish", 110, 40), sig)                        # noqa: E731
    evs = dp.scan(daily, "X", {}, fn=fn)
    assert {e["book"] for e in evs} == {"current"}                           # zeroed drops under 62
    assert all(e["played_out"] for e in evs)
