"""
The weekly trend lean (1W EMA 50 / Ichimoku / MACD flips across coins) as an
HL filter on the live book.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import weekly_trend_compare as wt                                      # noqa: E402
from test_cadence_compare import market, stub                          # noqa: E402,F401

D = wt.DAY_MS


def test_relation():
    assert wt.relation("LONG", "bearish") == "against"
    assert wt.relation("SHORT", "bearish") == "with"
    assert wt.relation("LONG", "bullish") == "with"
    assert wt.relation("SHORT", "bullish") == "against"
    assert wt.relation("LONG", "mixed") is None


def c(sym, day, direction, strength=70.0, hour=4):
    return {"symbol": sym, "slot_ms": day * D + hour * 3_600_000, "direction": direction,
            "strength": strength, "avg_tf_strength": strength - 2}


def test_adjust_by_the_days_lean():
    leans = {0: "bearish", D: "bullish", 2 * D: "mixed"}
    cands = [c("A", 0, "LONG"), c("B", 0, "SHORT"), c("C", 1, "LONG"), c("D", 2, "LONG")]
    got = {x["symbol"]: x["strength"] for x in wt.adjust(cands, leans, -10.0, 5.0)}
    assert got == {"A": 60.0, "B": 75.0, "C": 75.0, "D": 70.0}
    skip = wt.adjust(cands, leans, wt.SKIP, 0.0)
    assert skip[0]["strength"] < 0 and skip[0]["avg_tf_strength"] < 0


def test_daily_leans_from_the_reads_at_that_close():
    bear = {"tf": "1W", "direction": "bearish", "kind": "indicator_flip", "type": "ema50",
            "status": "active"}
    tl = {s: {"closes": [D], "reads": [[bear]]} for s in ("A", "B", "C")}
    tl["Z"] = {"closes": [5 * D], "reads": [[]]}               # no history yet on day 1
    got = wt.daily_leans(tl, [0, D])
    assert got[0] == "mixed"                                   # before the first close
    assert got[D] == "bearish"


def test_compare_reports_every_variant(stub):
    m = market()
    daily = {s: [{"timestamp": i * D, "open": 1, "high": 1, "low": 1, "close": 1,
                  "volume": 1} for i in range(10)] for s in m}
    res = wt.compare(m, daily, lambda closed, tf: [], core=list(m), days=5)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in wt.VARIANTS]
    assert wt.render_telegram(res).startswith("🧭")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "weekly-trend) MODULE=weekly_trend_compare" in wf
