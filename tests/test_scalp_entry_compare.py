"""The entry-timing study: pullback limits and fading stretched moves."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_entry_compare as se                                       # noqa: E402
from test_sweep_fvg_compare import b                                   # noqa: E402

M = se.M15


def _c(d="LONG", s=80.0):
    # a LONG signal at 104 with the stop at 102 and the target at 107
    return {"direction": d, "strength": s, "entry": 104.0, "sl": 102.0, "tp_targets": [107.0]}


BARS = [b(0, 100, 100.5, 99.5, 100), b(1, 103, 104.5, 102.8, 104)]
EMA = [None, 103.0]
ATR = [None, 1.0]


def test_pullback_limits():
    assert se.plan(_c(), "market", None, BARS, 1, EMA, ATR)["kind"] == "market"
    ema = se.plan(_c(), "ema", None, BARS, 1, EMA, ATR)
    assert ema == {"kind": "limit", "direction": "LONG", "limit": 103.0, "sl": 102.0, "target": 107.0}
    back = se.plan(_c(), "back", 0.5, BARS, 1, EMA, ATR)
    assert back["limit"] == 103.0                         # 0.5 x (104 - 102) back
    assert se.plan(_c(), "ema", None, BARS, 1, [None, 101.0], ATR) is None   # past the stop


def test_fades():
    # 104 is 1.0 ATR above the EMA 20: not stretched at 1.5
    assert se.plan(_c(s=90), "fade_stretch", None, BARS, 1, EMA, ATR) is None
    f = se.plan(_c(s=90), "fade_stretch", None, BARS, 1, [None, 102.4], ATR)
    assert f["direction"] == "SHORT" and f["sl"] == 105.0 and f["target"] == 102.4
    assert se.plan(_c(s=80), "fade_stretch", None, BARS, 1, [None, 102.4], ATR) is None  # < 85
    a = se.plan(_c(), "fade_all", None, BARS, 1, EMA, ATR)
    assert a["direction"] == "SHORT" and a["sl"] == 105.0 and a["target"] == 102.5


def test_limit_fill_and_stop_first():
    o = {"direction": "LONG", "limit": 103.0, "sl": 102.0}
    bars = BARS + [b(2, 104, 104.2, 103.5, 104), b(3, 104, 104, 102.9, 103.2)]
    assert se.fill(o, bars, 1) == 3
    stopped = BARS + [b(2, 104, 104.2, 101.9, 102)]          # through the stop: limit
    assert se.fill({**o, "limit": 101.5}, stopped, 1) is None  # never filled first
    assert se.fill(o, BARS + [b(2 + k, 104, 104.5, 103.6, 104) for k in range(6)], 1) is None


def test_atr_series():
    bars = [b(i, 100, 101, 99, 100) for i in range(20)]
    atr = se.atr_series(bars, 14)
    assert atr[12] is None and atr[13] == 2.0


def test_compare_and_render():
    random.seed(9)
    bars, p = [], 100.0
    for i in range(se.sc.WINDOW + 4 * se.sc.H1_WINDOW + 300):
        o = p
        p *= 1 + random.gauss(0.0003, 0.004)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
    end = int(bars[-1]["timestamp"]) + M
    read = lambda w, tf, s: {"direction": "LONG", "strength": 90, "entry": w[-1]["close"],  # noqa
                             "sl": w[-1]["close"] * 0.99, "tp_targets": [w[-1]["close"] * 1.01]}
    res = se.compare({"ETH": bars}, start_ms=end - 2 * 24 * 3_600_000, end_ms=end,
                     read_fn=read, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in se.VARIANTS]
    assert se.render_telegram(res).startswith("🎯")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-entry) MODULE=scalp_entry_compare" in wf
