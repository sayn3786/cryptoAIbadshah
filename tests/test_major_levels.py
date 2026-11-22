"""Multi-year major levels and the megaphone shape (major_levels): display only."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import major_levels as ml                                            # noqa: E402

WK = 7 * 86_400_000


def _ts(y, m=1):
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _weekly_btc():
    """EliZ's 2W picture: 2021 tops at 60-61k, 2024 and 2026 lows at 58-60k, a
    70-71k top zone, a 44-45k zone (2021 low, 2022 low, 2024 high)."""
    turns = [10, 60, 45, 61, 44, 48, 16, 45, 38, 70, 59, 71, 60, 110, 58, 80, 59, 85, 55]
    t = _ts(2020, 6)
    path = []
    for a, b in zip(turns, turns[1:]):
        for k in range(8):
            path.append((a + (b - a) * (k + 1) / 8) * 1000)
    return [{"timestamp": t + k * WK, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p}
            for k, p in enumerate(path)]


def test_levels_find_the_60k_flip_and_the_bands_around_it():
    wk = _weekly_btc()
    lv = ml.levels(wk, 56_000)
    assert lv["above"] and lv["below"]
    a = lv["above"][0]
    assert 56_000 < a["bottom"] <= 62_000 and a["touches"] >= 3
    assert a["flip"] and "resistance" in a["flip"] and "support" in a["flip"]
    assert any(43_000 <= b["bottom"] <= 46_000 for b in lv["below"])
    assert any(68_000 <= b["bottom"] <= 72_000 for b in lv["above"])


def test_levels_need_data():
    assert ml.levels([], 1) is None


def _c(k, lo, hi):
    return {"timestamp": k * WK, "open": (lo + hi) / 2, "high": hi, "low": lo, "close": (lo + hi) / 2}


def test_megaphone_highs_rising_lows_falling():
    # swings: 100/90, 104/86, 108/82 - widening
    path = [95, 97, 100, 97, 93, 90, 93, 97, 101, 104, 100, 94, 89, 86, 90, 96, 102, 108, 101, 93, 87, 82,
            86, 90, 92, 91, 90]
    cs = [_c(k, p - 1, p + 1) for k, p in enumerate(path)]
    m = ml.megaphone(cs)
    assert m and m["status"] == "inside" and m["upper_now"] > 109 and m["lower_now"] < 81
    narrowing = [95, 97, 100, 97, 93, 90, 93, 96, 99, 96, 93, 91, 93, 95, 98, 95, 93, 92, 93, 94, 96, 95, 94,
                 93, 94, 95, 94]
    assert ml.megaphone([_c(k, p - 1, p + 1) for k, p in enumerate(narrowing)]) is None


def test_panel_rows():
    from patterns import build_structure_panel
    from test_flag_pattern_correctness import _make_candles
    cs = _make_candles(60, up=False)
    band = {"bottom": 58000.0, "top": 62000.0, "touches": 5, "role": "support and resistance",
            "flip": "was resistance in 2021, support in 2026", "first_year": 2021, "last_year": 2026,
            "dist_pct": 4.5}
    a = {"candles": cs, "symbol": "BTC", "timeframe": "2W", "ema_trend": {}, "supertrend": {},
         "signal": {"direction": "NEUTRAL", "strength": 0},
         "major_levels": {"above": [band], "below": [], "inside": None, "weeks": 400},
         "megaphone": {"upper_now": 90000.0, "lower_now": 52000.0, "status": "inside"}}
    rows = {r["label"]: r for r in build_structure_panel(a)["rows"]}
    assert rows["Major Level Above"]["value"] == "58,000 – 62,000"
    assert "was resistance in 2021, support in 2026" in rows["Major Level Above"]["detail"]
    assert rows["Megaphone"]["value"] == "INSIDE" and "context only" in rows["Megaphone"]["detail"]
