import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import macro_lean_test as ml                                         # noqa: E402

D = ml.DAY_MS


def test_premium_lean_uses_a_3_day_mean_and_needs_consecutive_days():
    off = {k * D: 100.0 for k in range(6)}
    cb = {0: 100.2, D: 100.2, 2 * D: 100.2, 3 * D: 99.8, 4 * D: 99.8, 5 * D: 99.8}
    lean = ml.premium_lean(cb, off)
    assert lean[2 * D] == "LONG" and lean[5 * D] == "SHORT" and 3 * D not in lean
    gap = {0: 100.2, D: 100.2, 3 * D: 100.2}
    assert ml.premium_lean(gap, off) == {}


def test_stable_and_fg_leans():
    sup = {0: 100.0, 30 * D: 103.0, 31 * D: 99.0}
    sup[D] = 100.0
    lean = ml.stable_lean(sup)
    assert lean[30 * D] == "LONG" and lean[31 * D] == "SHORT"
    assert ml.fg_lean({0: 15, D: 50, 2 * D: 85}) == {0: "LONG", 2 * D: "SHORT"}


def test_scan_trades_every_coin_on_lean_days_with_cooldown():
    daily = [{"timestamp": k * D, "open": 100, "high": 103, "low": 98, "close": 100} for k in range(80)]
    leans = {"fg": {k * D: "LONG" for k in range(25, 60)}}
    evs = ml.scan(daily, "BTC", leans)
    assert [e["ts"] // D for e in evs] == [25, 45]                  # 20-day cooldown
    res = ml.summarize(evs)
    assert set(res) == set(ml.LEANS) and not res["fg"]["holds"]


def test_parsers():
    cb = ml.fetch_coinbase_daily(0, 3 * D, get=lambda p: [[D // 1000, 1, 2, 1, 1.5, 9]])
    assert cb == {D: 1.5}
    sup = ml.fetch_stable_supply(get=lambda: [{"date": str(D // 1000),
                                               "totalCirculatingUSD": {"peggedUSD": 1.2e11}}])
    assert sup == {D: 1.2e11}
