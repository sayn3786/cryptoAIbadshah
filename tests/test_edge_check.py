import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import edge_check as ec                                               # noqa: E402

DAY = 86_400_000


def test_windows_do_not_reach_unsettled_days():
    now = 1_795_392_000_000
    lo, hi = ec.window_ms("recent", now)
    assert hi == now - 20 * DAY and hi - lo == 180 * DAY
    plo, phi = ec.window_ms("prior", now)
    assert phi == now - 110 * DAY and phi - plo == 180 * DAY


def test_grade_signal():
    assert ec.grade_signal({"n": 10, "win_pct": 70, "avg_r": 0.5})["status"] == "too few"
    assert ec.grade_signal({"n": 40, "win_pct": 55, "avg_r": 0.1})["status"] == "working"
    assert ec.grade_signal({"n": 40, "win_pct": 51, "avg_r": 0.02})["status"] == "weakening"
    assert ec.grade_signal({"n": 40, "win_pct": 51, "avg_r": -0.02})["status"] == "gone"


def test_grade_8h_and_lean():
    half = {"stats": {"daily": {"n": 50, "avg_r": 0.05, "avg_loss": -0.9},
                      "8h": {"n": 50, "avg_r": 0.08, "avg_loss": -0.7}}}
    assert ec.grade_8h(half)["status"] == "working"
    half["stats"]["8h"]["avg_loss"] = -1.0
    assert ec.grade_8h(half)["status"] == "weakening"
    half["stats"]["8h"]["avg_r"] = 0.01
    assert ec.grade_8h(half)["status"] == "gone"
    assert ec.grade_lean({"resolved": 40, "win_pct": 56, "z": 2.1, "avg_r": 0.1})["status"] == "working"


def test_grade_size_cap():
    era = {"rows": {"soft 3": {"trades": 100, "ratio": 2.0}}, "ok": {"soft 3": False}, "shuf": {"soft 3": 1.9}}
    assert ec.grade_size_cap(era)["status"] == "weakening"
    era["shuf"]["soft 3"] = 2.5
    assert ec.grade_size_cap(era)["status"] == "gone"
    era["ok"]["soft 3"] = True
    assert ec.grade_size_cap(era)["status"] == "working"


def test_flag_needs_gone_twice_and_render():
    edges = {"A": {"recent": {"status": "gone", "st": {"n": 40, "avg_r": -0.1}}, "prior": {"status": "gone"}},
             "B": {"recent": {"status": "gone", "st": {}}, "prior": {"status": "working"}}}
    assert ec.flagged(edges) == ["A"]
    text = ec.render_telegram({"edges": edges, "coins": ["BTC"], "now": 1_795_392_000_000})
    assert "Flagged for removal" in text and "A" in text and "switches off automatically" in text


def test_memo_signal_calls_once():
    calls = []
    f = ec.memo_signal(lambda w, s, fg: calls.append(1) or {"d": 1})
    w = [{"timestamp": 5}]
    f(w, "BTC", None)
    f(w, "BTC", None)
    assert len(calls) == 1


def test_check_daily_on_tiny_data_runs():
    import random
    rng = random.Random(3)
    rows, p = [], 100.0
    t0 = 1_795_392_000_000 - 700 * DAY
    for d in range(700):
        o = p
        p *= 1 + rng.gauss(0, 0.03)
        rows.append({"timestamp": t0 + d * DAY, "open": o, "high": max(o, p) * 1.01, "low": min(o, p) * 0.99,
                     "close": p, "volume": 1.0})

    def sig(window, sym, fg):
        c = float(window[-1]["close"])
        if int(window[-1]["timestamp"]) // DAY % 9:
            return {}
        return {"direction": "LONG", "strength": 70, "entry": c, "sl": c * 0.95, "tp_targets": [c * 1.05, c * 1.1]}
    out = ec.check_daily({"X": rows}, {}, 1_795_392_000_000, log=lambda m: None, signal_fn=sig)
    assert set(out) == {"1D signal 62+", "1D size cap"}
    assert all(out[k][w]["status"] in ec.ICON for k in out for w in ec.WINDOWS)


def test_grade_lean_filter():
    era = {"no-against": {"stats": {"resolved": 50, "n": 50, "win_pct": 56, "avg_r": 0.10}, "random": 0.05,
                          "ok": True}}
    assert ec.grade_lean_filter(era)["status"] == "working"
    era["no-against"]["ok"] = False
    assert ec.grade_lean_filter(era)["status"] == "weakening"
    era["no-against"]["stats"]["avg_r"] = 0.04
    assert ec.grade_lean_filter(era)["status"] == "gone"
