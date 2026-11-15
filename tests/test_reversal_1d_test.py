"""1D reversal signals (Reversal Radar, reversal chart patterns): trade the
reversal or the opposite?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_1d_test as rv                                          # noqa: E402

D = rv.DAY_MS


def day(i, o, c, h=None, lo=None):
    return {"timestamp": i * D, "open": o, "close": c, "high": h if h is not None else max(o, c),
            "low": lo if lo is not None else min(o, c), "volume": 1}


def test_walk_is_one_to_one_stop_first_and_costed():
    up = [day(1, 100, 104, h=106)]
    t = rv.walk("LONG", 100.0, 2.0, up)                    # target 103 hit
    assert t["result"] == "target" and abs(t["r"] - (1 - rv.COST * 100 / 3)) < 1e-9
    both = [day(1, 100, 100, h=104, lo=96)]
    assert rv.walk("LONG", 100.0, 2.0, both)["result"] == "stop"
    assert rv.walk("SHORT", 100.0, 2.0, up)["result"] == "stop"
    flat = [day(k, 100, 100.5) for k in range(rv.HOLD_DAYS)]
    o = rv.walk("LONG", 100.0, 2.0, flat)
    assert o["result"] == "open" and 0 < o["r"] < 1
    assert rv.walk("LONG", 100.0, 2.0, flat[:3])["result"] == "pending"


def _daily(n=400):
    return [day(k, 100 + (k % 5), 100 + ((k + 1) % 5), h=106, lo=94) for k in range(n)]


def test_radar_event_only_on_the_day_it_turns_elevated():
    levels = {}

    def radar(window, sym):
        i = int(window[-1]["timestamp"]) // D
        lvl = "elevated" if 300 <= i < 310 else "high" if i == 310 else "low"
        levels[i] = lvl
        return {"level": lvl, "mode": "bottom"}
    evs = rv.scan(_daily(), "X", radar_fn=radar, pattern_fn=lambda w, tf: [])
    assert [e["ts"] // D for e in evs] == [300]             # 301-310 stay elevated/high: no repeat
    e = evs[0]
    assert e["family"] == "radar bottoming" and e["direction"] == "LONG"
    assert e["opp"]["result"] in ("target", "stop", "open")


def test_confirmed_reversal_pattern_on_its_break_day():
    def pats(window, tf):
        ts = int(window[-1]["timestamp"])
        if ts == 350 * D:
            return [{"_kind": "reversal", "type": "double_top", "direction": "bearish",
                     "confirmed": True, "break_ts": ts},
                    {"_kind": "flag", "confirmed": True, "breakout_ts": ts}]
        return []
    evs = rv.scan(_daily(), "X", radar_fn=lambda w, s: {}, pattern_fn=pats)
    assert len(evs) == 1 and evs[0]["detail"] == "double top" and evs[0]["direction"] == "SHORT"


def test_verdicts():
    def st(w, n=100, r=0.1):
        return {"resolved": n, "win_pct": w, "z": (w / 100 * n - n / 2) / (n / 4) ** 0.5, "avg_r": r}
    assert rv.verdict(st(60), st(40, r=-0.2)) == "REVERSAL WINS"
    assert rv.verdict(st(40, r=-0.2), st(60, r=0.2)) == "OPPOSITE WINS"
    assert rv.verdict(st(52), st(48)) == "no edge"
    assert rv.verdict(st(70, n=20), st(30)) == "too few"


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", reversal-1d" in wf and "python -m reversal_1d_test --telegram" in wf
