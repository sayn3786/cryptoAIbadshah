"""2W / 3W candles on FIXED calendar periods (Nov 2026): pairing weekly candles
from the start of a rolling download re-paired them every week."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from binance import BinanceClient                                    # noqa: E402

W = 7 * 86_400_000
MON0 = 4 * 86_400_000


def _weeks(first, n):
    return [{"timestamp": MON0 + (first + k) * W, "open": 100 + first + k, "high": 110 + first + k,
             "low": 90 + first + k, "close": 101 + first + k, "volume": 1} for k in range(n)]


def test_same_2w_candles_whatever_the_download_starts():
    a = BinanceClient.aggregate_candles(_weeks(2900, 150), 2)
    b = BinanceClient.aggregate_candles(_weeks(2901, 150), 2)          # one week later
    common = {c["timestamp"] for c in a} & {c["timestamp"] for c in b}
    assert len(common) > 70
    for ts in common:
        ca = next(c for c in a if c["timestamp"] == ts)
        cb = next(c for c in b if c["timestamp"] == ts)
        if ts != max(common):
            assert ca == cb                                            # never re-paired
    assert all((c["timestamp"] - MON0) // W % 2 == 0 for c in a)       # fixed period starts


def test_incomplete_first_period_dropped_last_kept():
    out = BinanceClient.aggregate_candles(_weeks(2901, 5), 2)          # starts mid-period
    assert out[0]["timestamp"] == MON0 + 2902 * W                       # 2901 alone is dropped
    assert out[-1]["timestamp"] == MON0 + 2904 * W and out[-1]["close"] == 101 + 2905


def test_three_weeks_and_non_weekly_untouched():
    out = BinanceClient.aggregate_candles(_weeks(2901, 9), 3)
    assert all((c["timestamp"] - MON0) // W % 3 == 0 for c in out)
    daily = [{"timestamp": k * 86_400_000, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1}
             for k in range(5)]
    assert len(BinanceClient.aggregate_candles(daily, 2)) == 3        # old behaviour for non-weekly
