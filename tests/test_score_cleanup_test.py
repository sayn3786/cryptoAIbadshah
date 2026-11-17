"""Score cleanup: the 1D signal without the factors that tested as no edge."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import score_cleanup_test as sc                                        # noqa: E402

D = sc.DAY_MS


def test_cleaned_removes_the_sections_and_recomputes():
    sig = {"direction": "SHORT", "strength": 91, "score_breakdown": {
        "trend_ema_supertrend_ichimoku": -150.0, "reversal_radar": 8.0, "stoch_rsi": 20.0,
        "rsi_divergence": -30.0, "macd": -20.0}}
    c = sc.cleaned(sig)                                     # score -172 -> -170 (removed -2)
    assert c["direction"] == "SHORT" and c["removed_points"] == -2.0
    assert abs(c["strength"] - (91 + (170 - 172) / 220 * 100)) < 0.11
    weak = {"direction": "LONG", "strength": 20, "score_breakdown": {"rsi_divergence": 40.0, "macd": 10.0}}
    assert sc.cleaned(weak)["direction"] == "NEUTRAL"       # only the removed factor carried it


def test_both_books_with_their_own_cooldowns():
    daily = [{"timestamp": k * D, "open": 100, "close": 100 + (k % 3), "high": 104, "low": 96,
              "volume": 1} for k in range(400)]

    def sig(window, sym, fg):
        i = int(window[-1]["timestamp"]) // D
        if i == 300:   # strong on both
            return {"direction": "LONG", "strength": 80, "score_breakdown": {"macd": 150.0}}
        if i == 330:   # strong only thanks to a removed factor
            return {"direction": "LONG", "strength": 70, "score_breakdown": {"stoch_rsi": 120.0, "macd": 30.0}}
        return {}
    evs = sc.scan(daily, "X", {}, signal_fn=sig)
    got = sorted((e["book"], e["ts"] // D) for e in evs)
    assert got == [("cleaned", 300), ("current", 300), ("current", 330)]


def test_era_rule():
    def ev(book, win):
        return {"book": book, "trade": {"result": "target", "r": 0.95} if win else {"result": "stop", "r": -1.05}}
    good = [ev("current", k % 2 == 0) for k in range(100)] + [ev("cleaned", k % 3 != 0) for k in range(100)]
    assert sc.era(good)["passes"]
    worse = [ev("current", k % 3 != 0) for k in range(100)] + [ev("cleaned", k % 2 == 0) for k in range(100)]
    assert not sc.era(worse)["passes"]


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", score-cleanup" in wf and "python -m score_cleanup_test --telegram" in wf
