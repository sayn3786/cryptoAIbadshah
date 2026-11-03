"""The 1D reversal audit: graded only by what follows the confirmation day."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_audit as ra                                            # noqa: E402

D = ra.DAY_MS


def bar(i, o, h, lo, c, v=1000.0):
    return {"timestamp": i * D, "open": o, "high": h, "low": lo, "close": c, "volume": v}


S = 80                                                     # the swing day


def slide_then(after):
    """A 40-day slide from 200 to ~140, a swing low at day S (130), three
    confirming days above it, then `after` closes."""
    bars = [bar(i, 200, 201, 199, 200 + (i % 2)) for i in range(S - 40)]     # history first
    bars += [bar(S - 40 + i, 200 - 1.5 * i, 201 - 1.5 * i, 199 - 1.5 * i, 200 - 1.5 * i)
             for i in range(40)]
    bars.append(bar(S, 141, 142, 130, 138))
    for i, c in zip((S + 1, S + 2, S + 3), (139, 140, 141)):
        bars.append(bar(i, c - 1, c + 0.5, c - 1.5, c))
    prev = 141.0
    for k, c in enumerate(after):
        bars.append(bar(S + 4 + k, prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c))
        prev = c
    return bars


def test_grade_is_a_race_from_the_decision_close():
    up = slide_then([146, 152, 157] + [157] * 30)
    assert ra.grade(up, S, 1) is True                     # +10% from 141 first
    down = slide_then([135, 128, 126] + [126] * 30)
    assert ra.grade(down, S, 1) is False                  # -10% first
    flat = slide_then([141] * 31)
    assert ra.grade(flat, S, 1) is None


def test_the_swing_is_labelled_by_what_follows_only():
    up = slide_then([146, 152, 157] + [157] * 30)
    t = ra.label_turns(up)
    assert S in t["real_bottom"] and S not in t["false_low"]
    down = slide_then([135, 128, 126] + [126] * 30)
    assert S in ra.label_turns(down)["false_low"]


def test_reads_stop_at_the_decision_day():
    seen = []

    def read(win, sym):
        seen.append(int(win[-1]["timestamp"]) // D)
        return {"direction": "LONG", "strength": 50, "score_breakdown": {"choch": 18},
                "reversal_radar": {"mode": "bottom", "level": "high", "count": 5,
                                   "signals": [{"label": "RSI oversold"}]}}
    bars = slide_then([146, 152, 157] + [157] * 30)
    ev = [e for e in ra.coin_events("X", bars, read) if e["t"] == S * D]
    assert ev and ev[0]["real"] and ev[0]["calls"] == [50, 50, 50, 50]
    assert ev[0]["feat"]["choch"] == 18 and ev[0]["feat"]["radar:RSI oversold"] == 1.0
    assert {S, S + 1, S + 2, S + 3} <= set(seen)
    assert not any(S + 3 < d < S + 10 for d in seen)        # nothing read past the decision day


def _walk(n, seed, start):
    random.seed(seed)
    p, out = start, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0, 0.04)
        out.append(bar(i, o, max(o, p) * (1 + random.uniform(0, 0.02)),
                       min(o, p) * (1 - random.uniform(0, 0.02)), p))
    return out


def test_a_random_walk_shows_no_verdicts():
    """The design check: with no information in the data, no component may
    come out as 'raise' or 'misleads'. (Fake engine: a momentum read, the kind
    that leaked in the earlier drafts.)"""
    def read(win, sym):
        c = [b["close"] for b in win]
        roc = (c[-1] / c[-4] - 1) * 100 if len(c) > 4 else 0
        return {"direction": "LONG" if roc > 0 else "SHORT", "strength": min(99, abs(roc) * 5),
                "score_breakdown": {"roc": roc, "slope": 1 if roc > 0 else -1}}
    daily = {f"C{k}": _walk(700, k, 100.0 * (k + 1)) for k in range(8)}
    res = ra.compare(daily, read_fn=read, log=lambda *_: None)
    for side in ("bottom", "top"):
        v = res["analysis"][side]["verdicts"]
        assert v["underweighted"] == [] and v["misleads"] == [], (side, v)
    text = ra.render_telegram(res)
    assert "Reversal audit" in text and "BOTTOMS" in text and "TOPS" in text


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m reversal_audit --telegram --days 1500" in wf
