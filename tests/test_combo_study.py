"""
Indicator combinations on the daily chart: features per close, events when a
combination becomes true, both-halves edge vs baseline and a placebo.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import combo_study as cs                                               # noqa: E402

D = cs.DAY_MS


def bar(i, o, c, h=None, lo=None):
    return {"timestamp": i * D, "open": o, "close": c, "high": h or max(o, c) * 1.001,
            "low": lo or min(o, c) * 0.999, "volume": 1.0}


def test_candle_features():
    bars = [bar(i, 100, 100) for i in range(16)] + \
        [bar(16, 100, 101), bar(17, 101, 102), bar(18, 102, 104, h=104.1, lo=101.9)]
    f = cs.candle_features(bars)
    assert f["c_run"] == 1 and f["c_break"] == 1 and f["c_range"] == 1
    wick = [bar(i, 100, 100, 101, 99) for i in range(16)] + [bar(16, 100, 100.2, 100.3, 97)]
    assert cs.candle_features(wick)["c_wick"] == 1
    assert cs.candle_features(bars[:5]) == {}


def test_engine_features_skip_small_and_meta_sections():
    sig = {"score_breakdown": {"macd": 10, "vwap": -6, "rsi_level": 3, "combo_flow_trend": 12,
                               "confluence_multiplier": 9},
           "direction": "LONG", "strength": 64}
    assert cs.engine_features(sig) == {"macd": 1, "vwap": -1, "engine": 1}


def test_events_only_when_the_combination_becomes_true():
    rows = [{"t": 0, "f": {"a": 1}}, {"t": 1, "f": {"a": 1, "b": 1}}, {"t": 2, "f": {"a": 1, "b": 1}}]
    got = [(c, d, r["t"]) for c, d, r in cs._events(rows, 2)]
    assert (("a",), 1, 0) in got and (("a",), 1, 1) not in got
    assert (("a", "b"), 1, 1) in got and (("a", "b"), 1, 2) not in got


def _rows(n, link):
    """`x` days are followed by +3% when `link`, else random."""
    random.seed(7)
    out = {}
    for s in ("A", "B", "C"):
        rows = []
        for i in range(n):
            u = random.random()
            on = u < 0.25
            f = {"x": 1, "y": 1} if on else ({"y": 1} if u < 0.5 else {})
            m = 3.0 if (on and link) else random.gauss(0, 2)
            rows.append({"t": i * D, "sym": s, "f": f,
                         "fwd": {1: m / 3, 3: m, 7: m * 1.5}})
        out[s] = rows
    return out


def test_study_finds_a_real_link_and_the_placebo_does_not():
    res = cs.study(_rows(600, True), max_size=2)
    held = {(r["combo"], r["d"]) for r in res["singles"] + res["bull"]}
    assert (("x",), 1) in held and (("x", "y"), 1) in held
    assert res["placebo_held"] < sum(res["held"].values())
    text = cs.render_telegram(res)
    assert text.startswith("🧩") and "x + y" in text


def test_no_link_holds_nothing():
    res = cs.study(_rows(600, False), max_size=2)
    assert not res["bull"] and "Verdict" in cs.render_telegram(res)


def test_coin_rows_scores_each_close_without_the_future():
    daily = [bar(i, 100 + i, 101 + i) for i in range(cs.WINDOW + 5)]
    seen = []

    def score(win, sym):
        seen.append(win[-1]["timestamp"])
        return {"score_breakdown": {"macd": 8}, "direction": "LONG", "strength": 40}
    rows = cs.coin_rows(daily, score, symbol="X")
    # each close is scored on the WINDOW candles ending at it, never later ones
    assert len(rows) == 5 and seen == [r["t"] for r in rows]
    assert rows[0]["t"] == cs.WINDOW * D
    assert rows[-1]["fwd"][1] is None and rows[0]["fwd"][1] is not None


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m combo_study --telegram --days 1500" in wf
