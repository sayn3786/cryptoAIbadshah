"""
v58: the live-only positioning sections weigh less — funding 30/15 -> 16/8,
order book 18/10 (+8 walls) -> 6/3 (+4), long/short 14/8 -> 6/3, and the
funding + trend combo 15 -> 8.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis                                                 # noqa: E402
from signals import generate_signal                                    # noqa: E402
from test_cadence_compare import walk                                  # noqa: E402


def bd(**extra):
    a = candle_analysis.build_candle_analysis(walk(300, seed=3), "2H", "ETH")
    a.update(extra)
    return generate_signal(a)["score_breakdown"]


@pytest.mark.parametrize("fr, pts", [(-0.03, 16), (-0.01, 8), (0.05, -16), (0.02, -8),
                                     (0.01, 0)])
def test_funding(fr, pts):
    got = bd(funding_rate={"current": fr, "current_8h": fr, "interval_hours": 8})
    assert got.get("funding", 0) == pts


@pytest.mark.parametrize("imb, pts", [("strong_bid", 6), ("bid_heavy", 3),
                                      ("strong_ask", -6), ("ask_heavy", -3)])
def test_order_book(imb, pts):
    assert bd(order_book={"imbalance": imb, "bid_ask_ratio": 2.0}).get("order_book", 0) == pts


def test_order_book_walls():
    got = bd(order_book={"imbalance": "balanced",
                         "biggest_bid": {"significance": "high", "distance_pct": -1.0},
                         "biggest_ask": {"significance": "high", "distance_pct": 5.0}})
    assert got.get("order_book", 0) == 4


@pytest.mark.parametrize("ratio, pts", [(0.5, 6), (0.75, 3), (3.0, -6), (2.0, -3), (1.0, 0)])
def test_long_short(ratio, pts):
    got = bd(long_short={"ratio": ratio, "long_pct": 50, "short_pct": 50})
    assert got.get("long_short_ratio", 0) == pts


def test_the_funding_trend_combo_is_8():
    import inspect
    import signals
    src = inspect.getsource(signals.generate_signal)
    assert "pts = 8   # v58: was 15" in src
