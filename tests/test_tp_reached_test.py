import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_1d_test as rv                                        # noqa: E402
import tp_reached_test as tt                                         # noqa: E402

D = rv.DAY_MS


def test_reached():
    s = {"direction": "SHORT", "strength": 70, "tp_targets": [95, 90]}
    assert tt.reached(s, {"high": 101, "low": 94}) == {"tp1": True, "last": False}
    assert tt.reached(s, {"high": 101, "low": 89}) == {"tp1": True, "last": True}
    assert tt.reached({**s, "strength": 50}, {"high": 101, "low": 80}) == {"tp1": False, "last": False}
    assert tt.reached({"direction": "LONG", "strength": 70, "tp_targets": [105]},
                      {"high": 106, "low": 99})["tp1"]


def _daily(n):
    return [{"timestamp": i * D, "open": 100, "high": 101, "low": 99 if i % 2 else 94,
             "close": 100} for i in range(n)]


def test_scan_groups():
    daily = _daily(rv.LOOKBACK + 60)
    sig = {"direction": "SHORT", "strength": 70, "tp_targets": [95, 90]}
    evs = tt.scan(daily, "X", {}, signal_fn=lambda w, s, f: sig)
    groups = {e["group"] for e in evs}
    assert "after TP1" in groups and "other" in groups and "after last TP" not in groups


def test_era_checks():
    def ev(g, win, n):
        return [{"group": g, "x": {"result": "target" if k < win else "stop",
                                   "r": 0.9 if k < win else -1.1}} for k in range(n)]
    e = tt.era(ev("after TP1", 15, 40) + ev("other", 22, 40))
    assert e["passes"]
    e2 = tt.era(ev("after TP1", 21, 40) + ev("other", 22, 40))
    assert not e2["passes"]


def test_weekly_bars_are_complete_monday_weeks():
    mon = 1_795_392_000_000           # 2026-11-23 is a Monday
    days = [{"timestamp": mon - 3 * D + k * D, "open": k, "high": k + 1, "low": k - 1,
             "close": k + 0.5, "volume": 1} for k in range(17)]   # Fri..: partial, 2 full weeks
    w = tt.weekly(days)
    assert [x["timestamp"] for x in w] == [mon, mon + 7 * D]
    assert w[0]["open"] == 3 and w[0]["close"] == 9.5 and w[0]["high"] == 10 and w[0]["low"] == 2


def test_weekly_run_uses_its_own_settings():
    mon = 1_795_392_000_000 - 400 * 7 * D
    days = [{"timestamp": mon + k * D, "open": 100, "high": 101, "low": 94 if (k // 7) % 2 else 99,
             "close": 100, "volume": 1} for k in range(7 * 120)]
    sig = {"direction": "SHORT", "strength": 70, "tp_targets": [95, 90]}
    res = tt.run({"X": days}, {}, log=lambda m: None, tf="1W", signal_fn=lambda w, s, f: sig)
    assert res["tf"] == "1W" and res["min_resolved"] == 20
    assert "1W signal" in tt.render_telegram(res) and "20 weeks" in tt.render_telegram(res)
