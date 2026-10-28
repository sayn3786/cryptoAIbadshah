"""The 15m SMA 200 study: a bounce bonus, a side filter, the bounce as a setup."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_sma_compare as smc                                        # noqa: E402
from test_sweep_fvg_compare import b                                   # noqa: E402

M = smc.M15


def test_bounce_reads_a_tap_and_a_close_back():
    sma = [100.0] * 5
    up = [b(0, 101, 101.5, 100.6, 101), b(1, 101, 101.2, 100.05, 100.8)]   # wick to 100.05
    assert smc.bounce(up, sma, 1) == "LONG"
    down = [b(0, 99, 99.4, 98.5, 99), b(1, 99, 99.95, 98.8, 99.2)]         # wick to 99.95
    assert smc.bounce(down, sma, 1) == "SHORT"
    away = [b(0, 103, 103.5, 102.5, 103), b(1, 103, 103.4, 102.6, 103.1)]  # nowhere near
    assert smc.bounce(away, sma, 1) is None
    assert smc.bounce(up, [None] * 5, 1) is None


def test_bounce_setup_stops_past_the_wick():
    sma = [None] * 3 + [100.0] * 3
    bars = [b(i, 101, 101.5, 100.6, 101) for i in range(4)] + [b(4, 101, 101.2, 100.05, 100.8)]
    [s] = smc.bounce_setups("ETH", bars, sma, start_ms=0)
    assert s["direction"] == "LONG" and s["entry"] == 100.8
    assert abs(s["sl"] - 100.05 * (1 - smc.STOP_BUFFER)) < 1e-9
    risk = 100.8 - s["sl"]
    assert abs(s["tp_targets"][0] - (100.8 + 1.5 * risk)) < 1e-9


def test_compare_and_render():
    random.seed(9)
    bars, p = [], 100.0
    for i in range(smc.sc.WINDOW + 4 * smc.sc.H1_WINDOW + 400):
        o = p
        p *= 1 + random.gauss(0.0003, 0.004)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
    end = int(bars[-1]["timestamp"]) + M
    read = lambda w, tf, s: {"direction": "LONG", "strength": 80, "entry": w[-1]["close"],  # noqa
                             "sl": w[-1]["close"] * 0.99, "tp_targets": [w[-1]["close"] * 1.01]}
    res = smc.compare({"ETH": bars}, start_ms=end - 2 * 24 * 3_600_000, end_ms=end,
                      read_fn=read, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in smc.VARIANTS]
    assert smc.render_telegram(res).startswith("〽️")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-sma) MODULE=scalp_sma_compare" in wf
