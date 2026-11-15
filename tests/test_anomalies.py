"""Unusual-candle reads: long same-colour runs and failed breakouts /
breakdowns, from closed candles; context only (never scored)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import anomalies as an                                                 # noqa: E402
import telegram_digest as td                                           # noqa: E402

W = 7 * 86_400_000


def c(i, o, cl, h=None, lo=None):
    return {"timestamp": i * W, "open": o, "close": cl,
            "high": h if h is not None else max(o, cl) + 0.5,
            "low": lo if lo is not None else min(o, cl) - 0.5}


def from_moves(moves, start=100.0):
    out, p = [], start
    for i, m in enumerate(moves):
        out.append(c(i, p, p + m))
        p += m
    return out


def test_seven_red_weeks_with_its_history():
    # an earlier 7-red run, then a rebound, then today's 7-red run
    moves = [1, 1, -1, 1] * 5 + [-1] * 7 + [2, 2, 1, 1] + [-1, 1] * 6 + [-1] * 7
    s = an.streak(from_moves(moves), "1W")
    assert s["length"] == 7 and s["direction"] == "bearish" and s["context"]
    assert s["label"].startswith("7 red weeks in a row — longest since")
    assert s["history_note"].startswith("1 time before; 2 weeks later: 1 up, 0 down, avg +")


def test_short_or_broken_runs_say_nothing():
    assert an.streak(from_moves([-1, 1] * 10 + [-1] * 3), "1W") is None        # 3 < 4
    flat = from_moves([-1] * 6) + [c(6, 94, 94)]                               # a flat close ends it
    assert an.streak(flat, "1W") is None
    first = an.streak(from_moves([1, -1] * 3 + [1] * 9), "1D")
    assert first["direction"] == "bullish" and "longest in the" in first["label"]
    assert first["history_note"] is None


def level_series(back_below=True):
    """A swing high at 110, a close above it, then (optionally) back below."""
    bars = [c(i, 100, 100) for i in range(4)]
    bars += [c(4, 100, 105), c(5, 105, 109, h=110), c(6, 109, 104), c(7, 104, 101)]
    bars += [c(8 + i, 101, 102) for i in range(6)]
    bars += [c(14, 102, 108), c(15, 108, 111.5)]                               # breaks above 110
    bars += [c(16, 111.5, 112)]
    bars += [c(17, 112, 107 if back_below else 113)]                           # back below
    return bars


def test_a_failed_breakout_on_the_latest_close():
    r = an.lost_level(level_series(), "1W")
    assert r and r["direction"] == "bearish" and r["level"] == 110
    assert r["label"] == "Lost 110 again — broke above 2 weeks ago, closed back below (failed breakout)"
    assert an.lost_level(level_series(back_below=False), "1W") is None


def test_a_failed_breakdown_is_mirrored():
    flipped = [{**b, "open": 200 - b["open"], "close": 200 - b["close"],
                "high": 200 - b["low"], "low": 200 - b["high"]} for b in level_series()]
    r = an.lost_level(flipped, "1W")
    assert r and r["direction"] == "bullish" and r["level"] == 90
    assert r["label"].startswith("Reclaimed 90 — broke below 2 weeks ago, closed back above")


def test_they_show_in_the_update_but_never_tip_the_star_or_lean():
    s = {"symbol": "BTC", "timeframe": "1W", "kind": "streak", "context": True,
         "status": "active", "direction": "bullish", "label": "5 green weeks in a row",
         "history_note": "2 times before", "bars_ago": 0, "break_ts": 0}
    l = {**s, "kind": "lost_level", "label": "Reclaimed 80,000"}
    parts, loud = td.build_market_digest_parts([s, l], max_chars=None)
    text = parts[0]
    assert "1W  🟢 5 green weeks in a row · 2 times before" in text
    assert "1W  🟢 Reclaimed 80,000" in text
    assert "⭐" not in text and not loud                                # two weekly "bullish" ≠ star


def test_the_daily_reads_include_them_for_the_four_timeframes():
    pytest.importorskip("flask")
    import app
    moves = [1, -1] * 25 + [-1] * 6
    bars = from_moves(moves)
    kinds = {r["kind"] for r in app._daily_reads_for(bars, "1W")}
    assert "streak" in kinds
    assert not any(r.get("kind") == "streak" for r in app._daily_reads_for(bars, "4H"))


# ── record closes, volume spikes, big candles ──────────────────────────────

def with_vol(bars, vols):
    return [{**b, "volume": v} for b, v in zip(bars, vols)]


def test_lowest_weekly_close_since():
    moves = [1] * 10 + [-0.5] * 5 + [0.2, -0.2] * 15 + [-4]
    bars = from_moves(moves)
    r = an.record_close(bars, "1W")
    assert r and r["direction"] == "bearish" and r["label"].startswith("Lowest weekly close since")
    assert r["span"] >= 26
    up = an.record_close(from_moves([0.1] * 40), "1W")
    assert up["label"] == "Highest weekly close in the 40 weeks of data"
    assert an.record_close(from_moves([1, -1] * 20), "1W") is None


def test_volume_spike_on_a_red_week_with_history():
    moves = [1, -1] * 20 + [-1]
    vols = [100.0] * 41
    vols[10], vols[25], vols[40] = 400.0, 350.0, 380.0       # 10 and 40 red? check colours
    bars = with_vol(from_moves(moves), vols)
    r = an.volume_spike(bars, "1W")
    assert r and r["ratio"] == 3.8 and "on a red week" in r["label"]
    assert r["history_note"].startswith("1 time before")        # 25 is red (odd index), 10 green
    assert an.volume_spike(with_vol(from_moves(moves), [100.0] * 41), "1W") is None
    assert an.volume_spike(from_moves(moves), "1W") is None    # no volume data: nothing


def test_big_red_week_since_and_history():
    moves = [0.5, -0.5] * 20
    bars = from_moves(moves)
    bars[15] = c(15, bars[15]["open"], bars[15]["open"] - 3)   # an earlier big red week
    bars.append(c(40, 100, 96))                                # today's big red week (range 5)
    r = an.big_candle(bars, "1W")
    assert r and r["direction"] == "bearish" and r["ratio"] >= 2.5
    assert r["label"].startswith("Range") and "biggest red week" in r["label"]
    calm = from_moves(moves) + [c(40, 100, 99.5)]
    assert an.big_candle(calm, "1W") is None
