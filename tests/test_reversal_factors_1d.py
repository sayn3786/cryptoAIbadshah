"""The other 1D reversal factors: trade the signal or the opposite?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_factors_1d as rf                                       # noqa: E402

D = rf.DAY_MS


def _daily(n=400):
    return [{"timestamp": k * D, "open": 100 + (k % 5), "close": 100 + ((k + 1) % 5),
             "high": 106, "low": 94, "volume": 1} for k in range(n)]


def test_each_factor_fires_only_on_its_day():
    st = {}
    a = {"rsi_divergence": {"type": "bearish", "points": {"curr": {"timestamp": 5}}},
         "choch": {"signal": "bullish", "candles_ago": 0},
         "liq_grab": {"signal": "bearish", "candles_ago": 2},           # not today
         "supertrend": {"flipped_bars_ago": 0, "direction": "bullish"},
         "engulfing": [{"candles_ago": 1, "direction": "bearish"}]}
    got = dict(rf.events_from(a, st))
    assert got == {"RSI divergence": "SHORT", "CHoCH": "LONG", "SuperTrend flip": "LONG",
                   "engulfing": "SHORT"}
    assert "RSI divergence" not in dict(rf.events_from(a, st))      # same divergence: once
    a["rsi_divergence"] = {"type": "hidden_bullish", "points": {}}
    assert "RSI divergence" not in dict(rf.events_from(a, st))      # hidden = continuation


def test_cooldown_one_trade_per_factor_per_coin():
    fires = {300, 305, 330}

    def analysis(window, sym):
        i = int(window[-1]["timestamp"]) // D
        return {"choch": {"signal": "bearish", "candles_ago": 0}} if i in fires else {}
    evs = rf.scan(_daily(), "X", analysis_fn=analysis, pattern_fn=lambda w, tf: [])
    assert [e["ts"] // D for e in evs] == [300, 330]              # 305 is inside the cooldown
    assert all(e["direction"] == "SHORT" for e in evs)


def test_patterns_are_rechecked():
    def pats(window, tf):
        ts = int(window[-1]["timestamp"])
        return [{"_kind": "reversal", "direction": "bullish", "confirmed": True, "break_ts": ts}] \
            if ts == 320 * D else []
    evs = rf.scan(_daily(), "X", analysis_fn=lambda w, s: {}, pattern_fn=pats)
    assert [(e["family"], e["direction"]) for e in evs] == [("reversal patterns (re-check)", "LONG")]


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", reversal-factors" in wf and "python -m reversal_factors_1d --telegram" in wf
