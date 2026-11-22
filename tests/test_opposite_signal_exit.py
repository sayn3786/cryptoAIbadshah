"""Early exit adopted from exit_rule_test (Nov 22 2026): an open 1D pick is
closed at the daily close when its coin's 1D signal points the other way at 62+."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402

ROWS = [
    {"id": "a", "symbol": "ETH", "direction": "LONG", "timeframe": "1D", "status": "OPEN"},
    {"id": "b", "symbol": "SOL", "direction": "SHORT", "timeframe": "1D", "status": "PARTIAL_TP"},
    {"id": "c", "symbol": "ADA", "direction": "LONG", "timeframe": "1D", "status": "PENDING"},   # not filled
    {"id": "d", "symbol": "XRP", "direction": "LONG", "timeframe": "BW", "status": "OPEN"},      # not 1D
    {"id": "e", "symbol": "DOGE", "direction": "LONG", "timeframe": "1D", "status": "OPEN"},
]
READS = {"ETH": {"direction": "SHORT", "strength": 66, "signal_price": 2000.0},
         "SOL": {"direction": "LONG", "strength": 70, "signal_price": 100.0},
         "ADA": {"direction": "SHORT", "strength": 80, "signal_price": 0.5},
         "XRP": {"direction": "SHORT", "strength": 80, "signal_price": 0.9},
         "DOGE": {"direction": "SHORT", "strength": 55, "signal_price": 0.2}}   # under 62


def test_targets():
    got = [r["id"] for r, _ in appmod._opposite_exit_targets(ROWS, READS)]
    assert got == ["a", "b"]


def test_closes_through_the_store_and_never_raises():
    closed = []

    class Store:
        @staticmethod
        def list_active_signals(environment=None, limit=500):
            return ROWS

        @staticmethod
        def close_signal(sid, price, closed_at=None, reason=None, source_ts=None):
            if sid == "b":
                raise RuntimeError("already closed")
            closed.append((sid, price, reason))
    res = appmod._opposite_signal_exits(READS, 1_795_392_000_000, store=Store, notify=False)
    assert closed == [("a", 2000.0, "OPPOSITE_SIGNAL")] and res == {"closed": 1, "errors": 1}


def test_wired_into_the_daily_close():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    i = src.index("_hlliq_log_close(close_publish.close_key(now))")
    assert "_opposite_signal_exits({sym: reads[sym] for sym in current}, expected)" in src[i:i + 600]
