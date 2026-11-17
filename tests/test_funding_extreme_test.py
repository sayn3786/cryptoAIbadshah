"""Do funding extremes mark tops and bottoms?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import funding_extreme_test as fx                                      # noqa: E402

D = fx.DAY_MS
H = 3_600_000


def test_fetch_pages_and_daily_sums():
    pages = [[{"time": str(k * H), "fundingRate": "0.0001"} for k in range(500)],
             [{"time": str((500 + k) * H), "fundingRate": "0.0002"} for k in range(10)]]
    got = fx.fetch_funding("BTC", 0, 600 * H, post=lambda body: pages.pop(0), pause=0)
    assert len(got) == 510
    daily = fx.daily_funding(got)
    assert abs(daily[0] - 24 * 0.0001) < 1e-12 and 21 * D not in daily   # last day: 6 hours only


def test_extremes_use_only_the_past():
    fund = {k * D: (0.0001 if k % 2 else -0.0001) for k in range(120)}
    fund[100 * D] = 0.01                                   # a spike
    ext = fx.extremes(fund)
    assert 100 * D in ext and ext[100 * D] > 2 and 50 * D not in ext


def _daily(n=200):
    return [{"timestamp": k * D, "open": 100, "close": 100 + (k % 3), "high": 104, "low": 96,
             "volume": 1} for k in range(n)]


def test_contrarian_trade_on_the_first_extreme_day_with_cooldown():
    fund = {k * D: (0.0001 if k % 2 else -0.0001) for k in range(200)}
    for k in (100, 101, 110):
        fund[k * D] = 0.01                                 # crowded long, 101 continues, 110 in cooldown
    fund[150 * D] = -0.01                                  # crowded short
    evs = fx.scan(_daily(), fund, "X")
    got = [(e["ts"] // D, e["side"]) for e in evs]
    assert got == [(100, "crowded_long"), (150, "crowded_short")]
    assert evs[0]["with"]["result"] in ("target", "stop", "open")


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", funding-extreme" in wf and "python -m funding_extreme_test --telegram" in wf
