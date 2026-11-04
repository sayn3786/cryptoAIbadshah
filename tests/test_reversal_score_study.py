"""The combined reversal score (reversal_score_study)."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_score_study as rss                                     # noqa: E402
import signals                                                         # noqa: E402

D = rss.DAY_MS


def ev(side, feat, real, t=0):
    return {"side": side, "feat": feat, "real": real, "t": t}


def test_score_counts_the_sides_own_tells():
    e = ev(1, {"volume": 9, "rsi_slope": 6, "radar:RSI oversold": 1.0, "bollinger": 20,
               "engulfing": 0}, True)
    assert rss.score(e) == 3                        # bollinger is a TOP tell
    assert rss.score(ev(-1, {"bollinger": 20, "vwap": 12, "volume": 9}, True)) == 2


def test_the_radar_tells_exist_in_the_engine():
    src = open(signals.__file__).read()
    for k in rss.BOTTOM_TELLS + rss.TOP_TELLS:
        if k.startswith("radar:"):
            assert f'"{k[6:]}"' in src, k


def test_analyse_finds_a_real_signal_and_requires_both_halves():
    random.seed(3)
    events = []
    for i in range(600):
        hi = random.random() < 0.4
        feat = {k: 1.0 for k in rss.BOTTOM_TELLS[:4]} if hi else {"volume": 1.0}
        real = random.random() < (0.75 if hi else 0.45)
        events.append(ev(1, feat, real, t=i * D))
    s = rss.analyse(events)["bottom"]
    t4 = next(t for t in s["thresholds"] if t["k"] == 4)
    assert t4["n"] > 150 and t4["edge"] > 8 and t4["z"] > 2.5 and t4["both_halves"]
    assert t4["passes"]
    # the same edge only in the first half: no pass
    late = [dict(e, real=(random.random() < 0.45)) if e["t"] >= 300 * D else e for e in events]
    t4b = next(t for t in rss.analyse(late)["bottom"]["thresholds"] if t["k"] == 4)
    assert not t4b["passes"]


def test_random_walk_with_the_real_engine_passes_nothing():
    def walk(n, seed, start):
        random.seed(seed)
        p, out = start, []
        for i in range(n):
            o = p
            p *= 1 + random.gauss(0, 0.04)
            out.append({"timestamp": i * D, "open": o, "high": max(o, p) * 1.01,
                        "low": min(o, p) * 0.99, "close": p,
                        "volume": 1000.0 * random.uniform(0.5, 2)})
        return out
    w = rss.run_window({f"C{k}": walk(500, k, 100.0 * (k + 1)) for k in range(5)},
                       log=lambda *_: None)
    for side in ("bottom", "top"):
        assert not any(t["passes"] for t in w["analysis"][side]["thresholds"])
    text = rss.render_telegram(w, w)
    assert "Combined reversal score" in text and "UNSEEN" in text and "Verdict" in text


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m reversal_score_study --telegram --days 1500 --unseen-days 1000" in wf
