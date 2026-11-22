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
    {"id": "e", "symbol": "AVAX", "direction": "LONG", "timeframe": "1D", "status": "OPEN"},
]
READS = {"ETH": {"direction": "SHORT", "strength": 66, "signal_price": 2000.0},
         "SOL": {"direction": "LONG", "strength": 70, "signal_price": 100.0},
         "ADA": {"direction": "SHORT", "strength": 80, "signal_price": 0.5},
         "XRP": {"direction": "SHORT", "strength": 80, "signal_price": 0.9},
         "AVAX": {"direction": "SHORT", "strength": 55, "signal_price": 0.2}}   # under 62


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


def test_the_8h_job_replaces_the_daily_check():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    i = src.index("_hlliq_log_close(close_publish.close_key(now))")
    assert "_opposite_signal_exits(" not in src[i:i + 600]                # no daily-close check now
    assert '"/api/cron/exit-8h"' in src.split("_SCHEDULER_PATHS = ")[1][:900]
    w = open(os.path.join(os.path.dirname(__file__), "..", "cloudflare", "hl-manage-worker", "worker.js"),
             encoding="utf-8").read()
    assert 'path: "/api/cron/exit-8h"' in w and "h % 8 === 0 && m === 12" in w


def test_exit_8h_run_uses_only_the_8h_candle_that_just_closed():
    H8 = appmod.H8_MS
    now = 2_000 * H8 + 12 * 60_000                       # 12 minutes after an 8H close
    closed = []

    class Store:
        @staticmethod
        def list_active_signals(environment=None, limit=500):
            return ROWS

        @staticmethod
        def close_signal(sid, price, closed_at=None, reason=None, source_ts=None):
            closed.append((sid, price, reason))

    def build(sym, tf):
        assert tf == "8H"
        fresh = 2_000 * H8 if sym != "SOL" else 1_999 * H8           # SOL: a stale reading
        d = {"ETH": "SHORT", "SOL": "LONG", "AVAX": "SHORT"}.get(sym, "NEUTRAL")
        return {"signal": {"direction": d, "strength": 70}, "signal_price": 1.5,
                "signal_candle_closed_at": fresh}
    res = appmod._exit_8h_run(now, store=Store, build=build, notify=False)
    assert res["ran"] and closed == [("a", 1.5, "OPPOSITE_SIGNAL_8H"), ("e", 1.5, "OPPOSITE_SIGNAL_8H")]
