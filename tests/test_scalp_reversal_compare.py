"""The fresh reversal-reads study: bottoms / tops, Fib, divergence, S/R."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_reversal_compare as rc                                    # noqa: E402
from test_sweep_fvg_compare import b                                   # noqa: E402

M = rc.M15
BARS = [b(i, 100, 101, 99, 100) for i in range(20)]
LAST = BARS[-1]["timestamp"]


def test_fresh_reads_for_a_long():
    a = {"rsi_markers": [{"kind": "oversold_bottom", "bars_ago": 2}],
         "rsi_divergence": {"type": "bullish", "points": {"curr": {"timestamp": LAST - 4 * M}}},
         "fib": {"in_entry_zone": True, "bias": "long"},
         "sr_zones": {"support": {"dist_pct": -0.2}, "resistance": {"dist_pct": 3.0}}}
    got = rc.fresh_reads(a, BARS)
    assert got == {"LONG": {"bottom", "divergence", "fib", "support"}, "SHORT": set()}


def test_stale_reads_do_not_count():
    a = {"rsi_markers": [{"kind": "overbought_top", "bars_ago": 9}],
         "rsi_divergence": {"type": "bearish", "points": {"curr": {"timestamp": LAST - 12 * M}}},
         "fib": {"in_entry_zone": False, "bias": "short"},
         "sr_zones": {"resistance": {"dist_pct": 1.5}}}
    assert rc.fresh_reads(a, BARS) == {"LONG": set(), "SHORT": set()}
    forming = {"rsi_divergence": {"type": "bearish", "forming": True}}
    assert rc.fresh_reads(forming, BARS)["SHORT"] == {"divergence"}


def test_fixed_exits_at_11x():
    ex = rc.fixed_exits(100.0, "LONG")
    assert abs(ex["sl"] - (100 - 100 * 0.10 / 11)) < 1e-9
    assert abs(ex["target"] - (100 + 100 * 0.15 / 11)) < 1e-9


def test_compare_and_render():
    random.seed(9)
    bars, p = [], 100.0
    for i in range(rc.sc.WINDOW + 4 * rc.sc.H1_WINDOW + 300):
        o = p
        p *= 1 + random.gauss(0.0002, 0.004)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
    end = int(bars[-1]["timestamp"]) + M

    def build(win, tf, sym):
        c = float(win[-1]["close"])
        return {"_c": c, "rsi_markers": [{"kind": "oversold_bottom", "bars_ago": 1}],
                "fib": {"in_entry_zone": True, "bias": "long"}}

    import signals
    orig = signals.generate_signal
    signals.generate_signal = lambda a: {"direction": "LONG", "strength": 70, "entry": a["_c"],
                                         "sl": a["_c"] * 0.99, "tp_targets": [a["_c"] * 1.01]}
    try:
        res = rc.compare({"ETH": bars}, start_ms=end - 2 * 24 * 3_600_000, end_ms=end,
                         build=build, log=lambda m: None)
    finally:
        signals.generate_signal = orig
    assert [r["label"] for r in res["rows"]] == [v[0] for v in rc.VARIANTS]
    rows = {r["label"]: r for r in res["rows"]}
    assert rows["+10 per fresh read the trade's way"]["trades"] >= rows["live (fixed exits)"]["trades"]
    assert rows["2+ fresh reads as their own setup"]["trades"] > 0
    assert rc.render_telegram(res).startswith("🔁")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-reversal) MODULE=scalp_reversal_compare" in wf
