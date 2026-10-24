"""
15m entries for the live HL signals: limit fills on a pullback / at the 15m
EMA 21, or a 15m confirmation, waiting 1-2h, else skip or market.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import combined_compare as cm                                          # noqa: E402
import entry_15m_compare as e15                                        # noqa: E402
import rr_compare as rr                                                # noqa: E402
from test_cadence_compare import market                                # noqa: E402
from test_indicator_study import _always_screen                        # noqa: E402

M = e15.M15
H = e15.HOUR_MS


def bar(i, o, c, h=None, lo=None):
    return {"timestamp": i * M, "open": o, "close": c, "high": h or max(o, c),
            "low": lo or min(o, c), "volume": 1.0}


LONG = {"symbol": "ETH", "direction": "LONG", "slot_ms": 4 * M, "sl": 90.0, "entry": 100.0}


def test_market_is_the_next_open():
    b = [bar(i, 100, 100) for i in range(10)]
    candles, costs = e15.entry(LONG, b, "market", None, 0, True)
    assert candles[0]["timestamp"] == 4 * M and costs == cm.LIMIT_TPS


def test_pullback_fills_at_the_limit_with_the_maker_fee():
    b = [bar(i, 100, 100) for i in range(4)] + [bar(4, 100, 99), bar(5, 99, 97, lo=97),
                                                bar(6, 97, 98)]
    candles, costs = e15.entry(LONG, b, "pullback", 0.25, 1, False)   # limit 100 - 2.5 = 97.5
    assert candles[0]["timestamp"] == 5 * M and candles[0]["open"] == 97.5
    assert costs["entry"] == cm.MAKER_BPS


def test_a_gap_through_the_limit_fills_at_the_open():
    b = [bar(i, 100, 100) for i in range(4)] + [bar(4, 100, 99), bar(5, 96, 96.5)]
    candles, _ = e15.entry(LONG, b, "pullback", 0.25, 1, False)
    assert candles[0]["open"] == 96


def test_no_fill_skips_or_goes_to_market():
    b = [bar(i, 100, 101) for i in range(20)]
    assert e15.entry(LONG, b, "pullback", 0.5, 1, False) is None
    candles, costs = e15.entry(LONG, b, "pullback", 0.5, 1, True)
    assert candles[0]["timestamp"] == 4 * M + H and costs == cm.LIMIT_TPS


def test_short_pullback_is_up():
    short = {**LONG, "direction": "SHORT", "sl": 110.0}
    b = [bar(i, 100, 100) for i in range(4)] + [bar(4, 100, 103, h=103)]
    candles, _ = e15.entry(short, b, "pullback", 0.25, 1, False)       # limit 102.5
    assert candles[0]["open"] == 102.5


def test_confirmation_waits_for_a_close_its_way():
    b = [bar(i, 100, 100) for i in range(4)] + [bar(4, 100, 99), bar(5, 99, 100), bar(6, 100, 100)]
    candles, costs = e15.entry(LONG, b, "confirm", None, 1, False)
    assert candles[0]["timestamp"] == 6 * M and costs == cm.LIMIT_TPS


def test_ema_limit_on_the_pullback_side():
    b = [bar(i, 90 + i * 0.1, 90 + i * 0.1) for i in range(40)] + [bar(40, 94, 95)]
    r = {**LONG, "slot_ms": 40 * M, "sl": 80.0}
    assert e15.entry(r, b, "ema", None, 2, False) is None           # EMA ~92.9, never touched
    b2 = b + [bar(41, 95, 92, lo=92)]
    candles, costs = e15.entry(r, b2, "ema", None, 2, False)
    assert candles[0]["timestamp"] == 41 * M and 92 < candles[0]["open"] < 94
    assert costs["entry"] == cm.MAKER_BPS


def test_ema_on_the_wrong_side_enters_at_market():
    b = [bar(i, 100 - i * 0.1, 100 - i * 0.1) for i in range(40)] + [bar(40, 94, 94)]
    r = {**LONG, "slot_ms": 40 * M, "sl": 80.0}                       # EMA above price
    candles, costs = e15.entry(r, b, "ema", None, 2, False)
    assert candles[0]["timestamp"] == 40 * M and costs == cm.LIMIT_TPS


def test_book_counts_missed(monkeypatch):
    rec = {"symbol": "ETH", "direction": "LONG", "slot_ms": 0, "strength": 75, "entry": 100,
           "sl": 95, "tp_targets": [110, 120], "rank": 1}
    b = rr.book([rec], {}, atr_add=0, entry_for=lambda r: None)
    assert b["counts"]["missed"] == 1 and b["trades"] == []


def test_compare_reports_every_variant(monkeypatch):
    _always_screen(monkeypatch)
    mk = market()
    m15 = {s: [{"timestamp": int(c["timestamp"]) + k * M, "open": c["open"], "high": c["high"],
                "low": c["low"], "close": c["close"], "volume": 1.0}
               for c in mk[s]["1H"] for k in range(4)] for s in mk}
    res = e15.compare(mk, m15, core=list(mk), days=6)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in e15.VARIANTS]
    assert e15.render_telegram(res).startswith("🎯")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "entry-15m) MODULE=entry_15m_compare" in wf
