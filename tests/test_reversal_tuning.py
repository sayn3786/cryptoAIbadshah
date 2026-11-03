"""The reversal tuning switches (off by default) and the runs that compare them."""
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis as ca                                           # noqa: E402
import signals as sg                                                   # noqa: E402

D = 86_400_000


def walk(n, seed, drift, vol=0.035):
    random.seed(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(drift, vol)
        out.append({"timestamp": i * D, "open": o, "high": max(o, p) * 1.01,
                    "low": min(o, p) * 0.99, "close": p, "volume": 1000 * random.uniform(.5, 2)})
    return out


def test_off_by_default():
    assert sg.TUNING == frozenset() or os.getenv("SIGNAL_TUNING")
    with sg.tuning("A", "Z"):
        assert sg.TUNING == frozenset({"A"})
    assert sg.TUNING == frozenset()


def test_trend_context_matches_the_audit():
    up = [{"close": 100.0}] * 30 + [{"close": 116.0}]
    down = [{"close": 100.0}] * 30 + [{"close": 84.0}]
    flat = [{"close": 100.0}] * 31
    assert sg._price_trend(up) == "up" and sg._price_trend(down) == "down"
    assert sg._price_trend(flat) is None and sg._price_trend(up[:10]) is None


def test_factors_only_where_the_audit_pointed():
    with sg.tuning("A", "B", "C", "D"):
        assert sg._tune_factor("rsi_slope", 6, "down") == 1.5
        assert sg._tune_factor("rsi_slope", -6, "down") == 1.0
        assert sg._tune_factor("rsi_slope", 6, "up") == 1.0
        assert sg._tune_factor("rsi_level", -12, "up") == 0.0
        assert sg._tune_factor("rsi_level", 12, "down") == 1.0          # oversold kept
        assert sg._tune_factor("trendlines", 5, "down") == 0.5
        assert sg._tune_factor("ema200_retest", -12, "down") == 1.0
        assert sg._tune_factor("cvd", -14, None) == 0.5 and sg._tune_factor("fvg", 13, "up") == 0.5
    assert sg._tune_factor("cvd", -14, None) == 1.0                    # off


def _analyses():
    out = []
    for seed in range(40):
        bars = walk(260, seed, [-0.006, 0.0, 0.006][seed % 3])
        out.append(ca.build_candle_analysis(bars, "1D", "ETH"))
    return out


def test_each_switch_moves_only_its_sections():
    for a in _analyses():
        base = sg.generate_signal(dict(a))
        for flag, sections in (("A", {"rsi_slope", "combo_momentum_against_trend"}),
                               ("B", {"rsi_level", "reversal_radar"}),
                               ("C", {"trendlines", "ema200_retest", "combo_flow_trend"}),
                               ("D", {"cvd", "fvg"})):
            with sg.tuning(flag):
                t = sg.generate_signal(dict(a))
            changed = {k for k in set(base["score_breakdown"]) | set(t["score_breakdown"])
                       if abs(base["score_breakdown"].get(k, 0) - t["score_breakdown"].get(k, 0))
                       > 1e-9}
            # A section's change can move the later caps / combos / multiplier that
            # read the running totals; the switch itself only touches its own.
            downstream = {"group_caps", "confluence", "confluence_multiplier"} | \
                {k for k in changed if k.startswith("combo_")}
            assert changed - sections - downstream == set(), (flag, changed)


def test_b_removes_the_two_radar_checks():
    for a in _analyses():
        with sg.tuning("B"):
            rr = sg.generate_signal(dict(a))["reversal_radar"]
        labels = {x["label"] for x in rr.get("signals") or []}
        assert not labels & {"RSI overbought", "Distribution volume"}


def test_the_studies_run_tuning_sets():
    import reversal_audit as ra
    daily = {f"C{k}": walk(400, k, 0.0, 0.04) for k in range(3)}
    res = ra.compare_tunings(daily, ["-", "A"], log=lambda *_: None)
    assert [v["tuning"] for v in res["variants"]] == ["-", "A"]
    assert "tuning A" in ra.render_tunings(res) and "today's engine" in ra.render_tunings(res)
    import close_picks_compare as cpc
    assert cpc.tuning_label("-") == "today's engine" and cpc.tuning_label("AB") == "tuning AB"


def test_workflow_runs_both():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert '--tunings "-,A,B,C,D,ABC"' in wf and "close_picks_compare --telegram --days 540 --tunings" in wf
