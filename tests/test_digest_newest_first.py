"""
8 AM update: coins with a read on the LATEST closed 1D / 1W candle come
first (a fresh 1W ahead of a fresh 1D), then by how recent their newest live
read is; latest-close reads are marked 🆕.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import telegram_digest as td  # noqa: E402


def flip(sym, tf, ago, d="bullish"):
    return {"symbol": sym, "timeframe": tf, "kind": "indicator_flip", "type": "macd",
            "direction": d, "event": "flip", "label": "MACD cross", "status": "active",
            "bars_ago": ago, "break_ts": 0}


def order(text):
    names = ["OLD", "D1NEW", "W1NEW", "MID", "SPENT"]
    return sorted((n for n in names if f"\n{n}" in text or f"⭐ {n}" in text),
                  key=lambda n: text.index(n))


def test_newest_first_and_marked():
    alerts = [flip("OLD", "1D", 4), flip("OLD", "1W", 3), flip("OLD", "1W", 4),   # heavy, old
              flip("MID", "1D", 2),
              flip("D1NEW", "1D", 0),
              flip("W1NEW", "1W", 0),
              {**flip("SPENT", "1D", 0), "status": "played_out", "played_ago": 0}]
    text, _ = td.build_market_digest(alerts)
    assert order(text) == ["W1NEW", "D1NEW", "MID", "OLD", "SPENT"]
    assert "🆕 1W" in text and "🆕 1D" in text
    assert "🆕 = fired on the latest closed candle" in text
    spent = text[text.index("SPENT"):]
    assert "🆕" not in spent.split("\n")[1]                  # played out: no mark


def test_no_legend_without_a_fresh_read():
    text, _ = td.build_market_digest([flip("OLD", "1D", 3)])
    assert "🆕" not in text


def test_freshness_key():
    assert td._freshness([flip("A", "1D", 2), flip("A", "1W", 2)]) == (2, 0)
    assert td._freshness([flip("A", "1D", 0)]) == (0, 1)
    assert td._freshness([]) == (99, 1)
