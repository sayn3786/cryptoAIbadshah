import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import size_cap as sc                                                  # noqa: E402


def test_factor_matches_the_tested_rule():
    assert [sc.factor(k) for k in range(7)] == [1.0, 1.0, 1.0, 0.75, 0.6, 0.5, 0.43]


def test_batch_counts_earlier_picks():
    got = sc.batch({"SHORT": 2, "LONG": 0}, [("A", "SHORT"), ("B", "SHORT"), ("C", "LONG"), ("D", "SHORT")])
    assert got["A"]["size"] == 1.0 and got["B"]["size"] == 0.75 and got["D"]["size"] == 0.6
    assert got["C"]["size"] == 1.0
    assert sc.label(got["B"], "SHORT") == " · size 0.75× (3 SHORTs open)" and sc.label(got["A"], "SHORT") == ""


def _iso(day, hour=0):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc).isoformat()


def test_tracker_hints_use_what_was_open_then():
    rows = [{"signal_id": i, "symbol": f"C{i}", "timeframe": "1D", "direction": "SHORT",
             "opened_at": _iso(10 + i), "closed_at": None} for i in range(4)]
    rows.append({"signal_id": 9, "symbol": "C9", "timeframe": "1D", "direction": "SHORT", "opened_at": _iso(9),
                 "closed_at": _iso(10, 12)})                  # closed before #1 opened
    rows.append({"signal_id": 8, "symbol": "C0", "timeframe": "1D", "direction": "SHORT",
                 "opened_at": _iso(10, 6), "closed_at": None})    # C0 republished: one coin
    rows.append({"signal_id": 7, "timeframe": "4H", "direction": "SHORT", "opened_at": _iso(9)})
    h = sc.tracker_hints(rows)
    assert h["0"]["open"] == 1 and h["3"]["open"] == 3 and h["3"]["size"] == 0.75 and "7" not in h


def test_paper_tag_and_compare():
    book = [{"state": "open", "direction": "LONG"}] * 3
    t = sc.tag_paper({"direction": "LONG"}, book)
    assert t["size_cap"] == 0.75 and t["open_same_way"] == 3
    done = [{"state": "closed", "pnl_usd": -5.0, "size_cap": 1.0, "closed_ms": 1},
            {"state": "closed", "pnl_usd": -5.0, "size_cap": 0.5, "closed_ms": 2},
            {"state": "closed", "pnl_usd": 5.0, "closed_ms": 3}]          # before the cap: left out
    c = sc.paper_compare(done)
    assert c["trades"] == 2 and c["pnl_full_usd"] == -10.0 and c["pnl_capped_usd"] == -7.5 and c["dd_capped_usd"] == 7.5


def test_close_message_shows_reduced_sizes_only():
    import close_publish as cp
    rec = lambda sym, s: {"symbol": sym, "direction": "SHORT", "strength": s, "entry": 1.0, "sl": 1.1,  # noqa: E731
                          "tp_targets": [0.9], "rr_ratio": 1.0, "signal_id": sym}
    sets = {"1D": [rec("AAA", 80), rec("BBB", 70)]}
    day = datetime(2026, 11, 23, tzinfo=timezone.utc)
    hints = sc.batch({"SHORT": 3}, [("AAA", "SHORT"), ("BBB", "SHORT")])
    text = cp.build_message(sets, day=day, size_hints=hints)
    assert "size 0.75× (3 SHORTs open)" in text and "size 0.6× (4 SHORTs open)" in text and "⚖️" in text
    plain = cp.build_message(sets, day=day, size_hints=sc.batch({}, [("AAA", "SHORT"), ("BBB", "SHORT")]))
    assert "size 1×" not in plain and "⚖️" not in plain             # full sizes: nothing added


def test_close_size_hints_count_active_1d_only():
    import app
    import close_publish as cp

    class Store:
        @staticmethod
        def list_active_signals(**kw):
            return [{"id": 1, "symbol": "A", "timeframe": "1D", "direction": "SHORT", "strategy_name": cp.STRATEGY_NAME},
                    {"id": 2, "symbol": "B", "timeframe": "1D", "direction": "SHORT", "strategy_name": cp.STRATEGY_NAME},
                    {"id": 6, "symbol": "B", "timeframe": "1D", "direction": "SHORT", "strategy_name": cp.STRATEGY_NAME},
                    {"id": 3, "symbol": "C", "timeframe": "1D", "direction": "SHORT", "strategy_name": cp.STRATEGY_NAME},
                    {"id": 4, "timeframe": "1W", "direction": "SHORT"},
                    {"id": 5, "symbol": "AAA", "timeframe": "1D", "direction": "SHORT", "strategy_name": cp.STRATEGY_NAME}]
    sets = {"1D": [{"symbol": "AAA", "direction": "SHORT", "strength": 70, "signal_id": 5}]}
    h = app._close_size_hints(sets, [5], store=Store)
    assert h["AAA"] == {"size": 0.75, "open": 3}     # A, B (republished once) and C; today's own pick not counted
