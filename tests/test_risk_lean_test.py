import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import risk_lean_test as rl                                          # noqa: E402

D = rl.DAY_MS


def test_risk_lean_lags_the_dollar_and_needs_both():
    dollar = {k * D: 100.0 - (k * 0.1 if k > 40 else 0) for k in range(120)}     # falling after day 40
    nasdaq = {k * D: 100.0 + k for k in range(120)}                               # above its 50d average
    lean = rl.risk_lean([100 * D], dollar, nasdaq)
    assert lean.get(100 * D) == "LONG"
    flat_nq = {k * D: 100.0 for k in range(120)}                                  # not above its average
    assert rl.risk_lean([100 * D], dollar, flat_nq) == {}
    # the dollar move only in the last 7 days (not yet published): no lean
    late = {k * D: 100.0 - (2.0 if k > 95 else 0) for k in range(120)}
    assert rl.risk_lean([100 * D], late, nasdaq) == {}


def test_breadth_lean():
    closes = {"BTC": {0: 100.0, 30 * D: 100.0}}
    for k in range(10):
        closes[f"A{k}"] = {0: 100.0, 30 * D: 110.0 if k < 8 else 90.0}
    assert rl.breadth_lean(closes) == {30 * D: "LONG"}
    for k in range(10):
        closes[f"A{k}"][30 * D] = 90.0 if k < 8 else 110.0
    assert rl.breadth_lean(closes) == {30 * D: "SHORT"}


def test_ribbons_cross_after_capitulation_lagged():
    hr = {}
    for k in range(200):
        hr[k * D] = 100.0 if k < 100 else (80.0 if k < 130 else 120.0)
    lean = rl.ribbons_lean(hr)
    assert len(lean) == 1 and all(v == "LONG" for v in lean.values())
    (d,) = lean
    assert d > 130 * D


def test_breadth_not_traded_on_btc_and_parsers():
    daily = [{"timestamp": k * D, "open": 100, "high": 103, "low": 98, "close": 100} for k in range(60)]
    leans = {"breadth": {30 * D: "LONG"}, "risk": {31 * D: "SHORT"}}
    assert [e["lean"] for e in rl.scan(daily, "BTC", leans)] == ["risk"]
    assert [e["lean"] for e in rl.scan(daily, "ETH", leans)] == ["breadth", "risk"]
    fred = rl.fetch_fred("X", get=lambda sid: "DATE,X\n1970-01-02,1.5\n1970-01-03,.\n")
    assert fred == {D: 1.5}
    hr = rl.fetch_hashrate(get=lambda: {"values": [{"x": 86400, "y": 5.0}]})
    assert hr == {D: 5.0}
