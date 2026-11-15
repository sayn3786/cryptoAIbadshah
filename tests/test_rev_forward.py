"""The 1D reversal-pattern forward paper book (rev_forward) and its daily job."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import rev_forward as rf                                               # noqa: E402

D = rf.DAY_MS


def day(i, o, c, h=None, lo=None):
    return {"timestamp": i * D, "open": o, "close": c, "high": h if h is not None else max(o, c),
            "low": lo if lo is not None else min(o, c), "volume": 1}


def candles(n=60):
    return [day(k, 100, 100, h=102, lo=98) for k in range(n)]            # ATR 4


PAT = {"type": "double_top", "label": "Double Top", "direction": "bearish", "confirmed": True,
       "status": "confirmed", "break_ts": 59 * D}


def test_only_a_reversal_pattern_confirmed_on_the_last_day():
    assert rf.confirmed_today([PAT], 59 * D) is PAT
    assert rf.confirmed_today([{**PAT, "break_ts": 58 * D}], 59 * D) is None
    assert rf.confirmed_today([{**PAT, "type": "falling_wedge"}], 59 * D) is None
    assert rf.confirmed_today([{**PAT, "status": "failed"}], 59 * D) is None


def test_trade_levels_are_one_to_one_at_1_5_atr():
    t = rf.open_trade("TAO", PAT, candles())
    assert t["direction"] == "SHORT" and t["entry"] == 100.0
    assert t["sl"] == 106.0 and t["target"] == 94.0 and t["day_ms"] == 59 * D


def test_settle_stop_first_target_timeout_and_opposite():
    t = rf.open_trade("TAO", PAT, candles())
    hit = rf.settle(t, candles() + [day(60, 100, 95, h=101, lo=93.5)])
    assert hit["exit_reason"] == "target" and hit["pnl_usd"] > 0
    assert hit["opposite_pnl_usd"] < 0                                  # the long stops out
    both = rf.settle(t, candles() + [day(60, 100, 100, h=107, lo=93)])
    assert both["exit_reason"] == "stop"
    assert rf.settle(t, candles() + [day(60, 100, 101)]) is None        # still open
    flat = candles() + [day(60 + k, 100, 99.5, h=101, lo=99) for k in range(rf.HOLD_DAYS)]
    to = rf.settle(t, flat)
    assert to["exit_reason"] == "timeout" and 0 < to["r"] < 1


def test_one_open_per_coin_and_a_20_day_cooldown():
    book = [{"symbol": "TAO", "state": "closed", "day_ms": 50 * D, "id": "TAO:50"}]
    assert not rf.can_open(book, "TAO", 60 * D)                         # 10 days later
    assert rf.can_open(book, "TAO", 70 * D)
    assert not rf.can_open([{"symbol": "TAO", "state": "open", "day_ms": 0}], "TAO", 99 * D)
    assert rf.can_open(book, "ETH", 51 * D)


def test_verdict_needs_100_trades_a_profit_and_53pct():
    win = {"state": "closed", "pnl_usd": 4.9, "exit_reason": "target", "direction": "LONG"}
    loss = {"state": "closed", "pnl_usd": -5.1, "exit_reason": "stop", "direction": "SHORT"}
    assert "in progress" in rf.summary([win] * 10)["verdict"]
    assert rf.summary([win] * 55 + [loss] * 45)["verdict"].startswith("PASS")
    assert rf.summary([win] * 52 + [loss] * 48)["verdict"].startswith("FAIL")


def test_daily_job_opens_and_settles(monkeypatch):
    import app as app_mod
    store = {}
    monkeypatch.setattr(app_mod, "_rev_fwd_load", lambda: list(store.get("b", [])))
    monkeypatch.setattr(app_mod, "_rev_fwd_save", lambda b: store.__setitem__("b", list(b)))
    monkeypatch.setattr(rf, "UNIVERSE", frozenset({"TAO", "ETH"}))
    import kv
    monkeypatch.setattr(kv, "claim", lambda *a, **k: True)
    monkeypatch.setattr(kv, "release", lambda *a, **k: None)
    res = app_mod._rev_fwd_daily(fetch_fn=lambda s: candles(), notify=False,
                                 detect_fn=lambda c: [PAT])
    assert res["ran"] and res["opened"] == 2 and len(store["b"]) == 2
    nxt = candles() + [day(60, 100, 95, h=101, lo=93.5)]
    res = app_mod._rev_fwd_daily(fetch_fn=lambda s: nxt, notify=False,
                                 detect_fn=lambda c: [{**PAT, "break_ts": 60 * D}])
    assert res["closed"] == 2 and res["opened"] == 0                   # cooldown: no new ones
    assert all(t["state"] == "closed" for t in store["b"])


def test_scheduled_and_shown():
    import app as app_mod
    assert "/api/cron/rev-forward" in app_mod._SCHEDULER_PATHS
    from _worker_schedule import worker_schedule
    assert worker_schedule().get("rev-forward") == [(0, 40)]
    root = os.path.join(os.path.dirname(__file__), "..")
    js = open(os.path.join(root, "dashboard", "js", "scalp.js")).read()
    assert "function renderRevForward()" in js and "S.data.rev_forward" in js
    assert 'id="revFwd"' in open(os.path.join(root, "dashboard", "scalp.html")).read()
