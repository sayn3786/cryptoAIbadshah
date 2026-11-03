"""Oversold-bottom markers that price comes back to: the event study."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import rsi_retest_study as rr                                          # noqa: E402

D = rr.DAY_MS


def bar(i, o, h, lo, c):
    return {"timestamp": i * D, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def tao_like(after):
    """A slide to an oversold low at 220 (day 40, wick; close 239 the next
    days), a drift back to ~221 with RSI lower, then `after` closes."""
    bars, p = [], 280.0
    for i in range(40):                                       # a choppy slide: RSI ~30s
        o = p
        p += -4.0 if i % 2 == 0 else 2.0
        bars.append(bar(i, o, max(o, p) + 0.5, min(o, p) - 0.5, p))
    bars.append(bar(40, p, p + 0.5, 220.12, 239))             # the wick to 220, close 239
    prev = 239.0
    for i, c in zip(range(41, 46), (237, 234, 230, 226, 221)):
        bars.append(bar(i, prev, prev + 0.5, c - 0.5, c))
        prev = c
    for k, c in enumerate(after):
        bars.append(bar(46 + k, prev, max(prev, c) + 1, min(prev, c) - 1, c))
        prev = c
    return bars


def test_marker_is_the_dashboards_and_known_three_days_later():
    bars = tao_like([221] * 40)
    rsi = rr.calculate_rsi_series([b["close"] for b in bars])
    m = [x for x in rr.markers(bars, rsi) if x["i"] == 40]
    assert m and m[0]["low"] == 220.12 and m[0]["known"] == 43 and m[0]["rsi"] <= 40


def test_the_tao_pattern_is_a_weaker_retest_with_its_outcome():
    up = tao_like([225, 232, 240, 246, 250] + [250] * 40)
    ev = rr.coin_events("TAO", up)["events"]
    e = next(x for x in ev if x["low"] == 220.12)
    assert e["t"] == 45 * D and e["kind"] == "weaker" and e["rsi"] < e["bottom_rsi"]
    assert e["r3"] > 0 and e["race"] == 1 and e["broke"] is False
    down = tao_like([214, 205, 196] + [196] * 40)
    e2 = next(x for x in rr.coin_events("TAO", down)["events"] if x["low"] == 220.12)
    assert e2["broke"] is True and e2["race"] == -1


def test_a_break_before_the_retest_is_not_a_retest():
    bars = tao_like([221] * 40)
    bars[44] = bar(44, 226, 227, 200, 205)                   # closes 7% under the low first
    assert not any(x["low"] == 220.12 for x in rr.coin_events("TAO", bars)["events"])


def test_report_on_random_coins():
    random.seed(4)
    daily = {}
    for k in range(3):
        p, bars = 100.0, []
        for i in range(600):
            o = p
            p *= 1 + random.gauss(0, 0.04)
            bars.append(bar(i, o, max(o, p) * 1.02, min(o, p) * 0.98, p))
        daily[f"C{k}"] = bars
    daily["TAO"] = tao_like([225, 232, 240] + [240] * 40)
    res = rr.compare(daily, log=lambda *_: None)
    assert sum(g["all"]["n"] for g in res["groups"]) > 10 and res["base"]["n"] > 1000
    text = rr.render_telegram(res)
    assert "RSI LOWER than at the bottom" in text and "TAO's recent retests" in text


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m rsi_retest_study --telegram --days 1500" in wf
