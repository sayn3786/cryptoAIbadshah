import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_exit_5m_compare as s5                                   # noqa: E402

M5 = s5.M5


def _b(k, o, h, l, c):
    return {"timestamp": k * M5, "open": o, "high": h, "low": l, "close": c}


def test_three_against_while_losing():
    bars = [_b(0, 100, 100.2, 99.9, 100.1), _b(1, 100.1, 100.15, 99.8, 99.9),
            _b(2, 99.9, 99.95, 99.7, 99.8), _b(3, 99.8, 99.85, 99.6, 99.7), _b(4, 99.7, 99.8, 99.5, 99.6)]
    r = s5.walk("LONG", 100.0, 99.0, 101.5, bars, 0, 10 * M5, "three5m")
    assert r["reason"] == "early" and r["exit"] == 99.7               # 3 reds, closes below entry
    assert s5.walk("LONG", 100.0, 99.0, 101.5, bars, 0, 10 * M5, "none")["reason"] == "close"


def test_three_against_but_still_in_profit_holds():
    bars = [_b(0, 101, 101.2, 100.8, 100.9), _b(1, 100.9, 101, 100.6, 100.7), _b(2, 100.7, 100.8, 100.4, 100.5)]
    assert s5.walk("LONG", 100.0, 99.0, 102.0, bars, 0, 10 * M5, "three5m")["reason"] == "close"


def test_opposite_5m_signal_and_stop_first():
    bars = [_b(0, 100, 100.1, 99.9, 100), _b(1, 100, 100.1, 98.9, 99)]
    sig = lambda k: {"direction": "SHORT", "strength": 70} if k == 0 else None   # noqa: E731
    assert s5.walk("LONG", 100.0, 99.0, 102.0, bars, 0, 10 * M5, "opp5m", sig)["reason"] == "early"
    assert s5.walk("LONG", 100.0, 99.0, 102.0, bars, 0, 10 * M5, "none")["reason"] == "stop"


def test_pnl_and_verdict():
    p = s5.pnl("LONG", 100.0, 99.0, {"exit": 99.0, "reason": "stop"})
    assert -11.4 < p < -11.2                                          # $10 risk + 13 bps on $1,000
    rows = {"none": {"trades": 60, "pf": 1.1, "avg_loss": -9.0},
            "three5m": {"trades": 60, "pf": 1.2, "avg_loss": -7.0},
            "opp5m": {"trades": 60, "pf": 1.3, "avg_loss": -9.5}}
    assert s5.verdict(rows) == {"three5m": True, "opp5m": False}
