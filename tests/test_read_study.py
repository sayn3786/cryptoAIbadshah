"""
Daily-read event study, and the played-out-before-confirmed rule.

  * a read whose move played out BEFORE it was confirmed (or a forming read
    that already played out) is never listed — it was stale before it could be
    posted;
  * the study builds Monday-aligned weeks from daily candles, counts each read
    once (the day it first appears as active), signs forward returns in the
    read's direction, compares them with an ordinary day, and finds the day a
    coin's reads reach a confluence count.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import read_study as rs                                                # noqa: E402

D = rs.DAY_MS
MON = 1_704_067_200_000          # 2024-01-01 00:00 UTC, a Monday


# ── played out before it was confirmed ───────────────────────────────────────

@pytest.fixture
def app_mod():
    pytest.importorskip("flask")
    import app
    return app


@pytest.mark.parametrize("event_age, played, show, status", [
    (0, 0, True, "played_out"),         # played out on the confirming candle: listed
    (2, 1, True, "played_out"),
    (0, 1, False, "played_out_early"),  # played out a candle BEFORE confirmation
    (1, 3, False, "played_out_early"),
    (1, None, True, "active"),
])
def test_played_out_before_confirmation_is_not_listed(app_mod, event_age, played, show, status):
    assert app_mod._keep(event_age, played) == (show, status)


def test_a_forming_read_that_already_played_out_is_not_listed(app_mod):
    assert app_mod._div_status(2, 0, None, confirmed=False) == (False, "played_out_early")
    assert app_mod._div_status(2, None, None, confirmed=False) == (True, "active")


# ── candles ──────────────────────────────────────────────────────────────────

def _day(i, close=100.0):
    return {"timestamp": MON + i * D, "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1.0}


def test_weeks_are_monday_aligned_and_complete():
    assert rs.week_start(MON + 3 * D + 5) == MON
    assert rs.week_start(MON - 1) == MON - 7 * D
    daily = [_day(i, 100 + i) for i in range(-2, 16)]     # Sat, Sun, 2 full weeks, 2 days
    weeks = rs.weekly_from_daily(daily)
    assert [w["timestamp"] for w in weeks] == [MON, MON + 7 * D]
    assert weeks[0]["open"] == 100 and weeks[0]["close"] == 106 and weeks[0]["high"] == 107


def test_clean_daily_dedupes_and_drops_the_forming_day():
    rows = [_day(1), _day(0), _day(0), _day(2)]
    assert [c["timestamp"] for c in rs.clean_daily(rows, MON + 2 * D + 5)] == [MON, MON + D]


def test_forward_return():
    daily = [_day(i, c) for i, c in enumerate([100, 110, 99])]
    assert rs.forward(daily, 0, 1) == pytest.approx(10.0)
    assert rs.forward(daily, 0, 2) == pytest.approx(-1.0)
    assert rs.forward(daily, 1, 5) is None


# ── events ───────────────────────────────────────────────────────────────────

def test_each_read_counts_once_on_its_first_active_day():
    daily = [_day(i) for i in range(30)]
    flip = {"kind": "indicator_flip", "type": "macd", "label": "MACD bullish cross",
            "direction": "bullish", "break_ts": 1, "status": "active"}
    late = {"kind": "rsi_swing", "label": "RSI Overbought Top", "direction": "bearish",
            "break_ts": 2, "status": "played_out"}

    def reads(closed, tf):
        n = len(closed)
        if tf == "1D" and 12 <= n <= 14:
            return [flip]                       # listed on three days
        if tf == "1D" and n == 15:
            return [late]                       # first seen already played out
        return []

    sc = rs.scan_coin(daily, reads, warmup_days=10)
    assert [(e["label"], e["day"]) for e in sc["events"]] == [("MACD bullish cross", 11)]


def test_confluence_fires_on_the_crossing_day_only():
    b = lambda tf: {"direction": "bullish", "status": "active", "tf": tf}  # noqa: E731
    lists = [(0, [b("1D")]), (1, [b("1D"), b("1W")]), (2, [b("1D"), b("1W")]),
             (3, [b("1W"), b("1W"), b("1D")]), (4, [])]
    got = [(e["label"], e["day"]) for e in rs.confluence_events(lists)]
    assert got == [("2+ bullish reads", 1), ("3+ bullish reads", 3),
                   ("2+ bullish weekly reads", 3)]


# ── statistics ───────────────────────────────────────────────────────────────

def test_edge_is_measured_against_an_ordinary_day():
    base = {(d, h): {"mean": 0.5 if d == "bullish" else -0.5, "hit": 50.0}
            for d in ("bullish", "bearish") for h in rs.HORIZONS}
    evs = [{"tf": "1W", "label": "SuperTrend flipped bullish", "direction": "bullish",
            "fwd": {h: x for h in rs.HORIZONS}} for x in [3.0, 1.0, 2.0, -1.0] * 6]
    [row] = rs.summarize(evs, base)
    assert row["n_7"] == 24 and row["mean_7"] == pytest.approx(1.25)
    assert row["edge_7"] == pytest.approx(0.75) and row["hit_7"] == pytest.approx(75.0)
    assert row["t"] > 0


def test_significance_needs_enough_events_and_a_clear_t():
    base = {(d, h): {"mean": 0.0, "hit": 50.0}
            for d in ("bullish", "bearish") for h in rs.HORIZONS}
    few = [{"tf": "1D", "label": "x", "direction": "bullish",
            "fwd": {h: 5.0 + (i % 2) for h in rs.HORIZONS}} for i in range(10)]
    many = [{**e, "label": "y"} for e in few * 3]
    rows = {r["label"]: r for r in rs.summarize(few + many, base)}
    assert rows["x"]["significant"] is False                 # n < 20
    assert rows["y"]["significant"] is True


def test_study_and_report_end_to_end():
    daily = {"ETH": [_day(i, 100 + (i % 7)) for i in range(200)]}

    def reads(closed, tf):
        n = len(closed)
        if tf == "1D" and n % 5 == 0:
            return [{"kind": "indicator_flip", "type": "st", "direction": "bullish",
                     "label": "SuperTrend flipped bullish", "break_ts": n, "status": "active"}]
        return []

    res = rs.study(daily, reads, warmup_days=40)
    assert res["coins"] == 1 and res["events"] > 20
    text = rs.render_telegram(res)
    assert text.startswith("🔬 Daily-read event study") and "SuperTrend flipped bullish" in text
