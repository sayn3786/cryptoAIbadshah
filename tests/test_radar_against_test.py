"""Pre-registered: should a trade be skipped when the Reversal Radar fights it?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import radar_against_test as rt                                        # noqa: E402
from test_htf_vs_random import H, fake, long_wins_sim, market           # noqa: E402,F401


def _replay(rev_for):
    """ETH and BTC candidates every 4h; SHORTs carry an opposing radar."""
    def replay(mk, **kw):
        assert (kw["interval_hours"], kw["primary_tf"]) == (4, "2H")
        out = []
        for k in range(300, 790, 4):
            d = "SHORT" if k % 8 == 0 else "LONG"
            out.append({"symbol": "ETH", "slot_ms": k * H, "direction": d, "strength": 70,
                        "reversal_against": rev_for(d)})
        return {"candidates": out}
    return replay


def test_losing_radar_against_trades_support_the_skip_rule(fake):
    res = rt.compare(market(800), days=20, log=lambda *_: None, seeds=10,
                     replay=_replay(lambda d: "elevated" if d == "SHORT" else None),
                     sim=long_wins_sim)
    by = {r["label"]: r for r in res["rows"]}
    assert by[rt.WITH]["pnl_usd"] < 0 and by[rt.WITHOUT]["pnl_usd"] > by[rt.ALL]["pnl_usd"]
    v = res["verdict"]["checks"]
    assert v["radar-against trades lose money"] and v["skipping them makes more $"]
    assert res["levels"]["elevated"] == by[rt.WITH]["signals"]


def test_building_or_low_radar_does_not_count_as_against(fake):
    res = rt.compare(market(800), days=20, log=lambda *_: None, seeds=10,
                     replay=_replay(lambda d: "building"), sim=long_wins_sim)
    by = {r["label"]: r for r in res["rows"]}
    assert by[rt.WITH]["signals"] == 0 and not res["verdict"]["passes"]


def test_below_the_floor_is_left_out():
    m = market(800)
    rep = {"candidates": [{"symbol": "ETH", "slot_ms": 400 * H, "direction": "LONG",
                           "strength": 61.9, "reversal_against": "high"}]}
    assert rt.candidates(rep, m, 0, 10_000 * H) == []


def test_fixed_rule_render_and_workflow():
    assert (rt.FLOOR, rt.PERIODS, rt.MIN_AGAINST, rt.AGAINST) == (62.0, (0, 180), 30, ("elevated", "high"))
    res = {"window": {"start": "2026-08-01", "end": "2026-11-01", "days": 90.0}, "coins": ["BTC"],
           "levels": {"none": 5, "elevated": 2},
           "rows": [{"label": rt.ALL, "signals": 0, "trades": 0}],
           "verdict": {"checks": {"30+ radar-against trades": False}, "passes": False}}
    text = rt.render_telegram(res)
    assert "NOT SUPPORTED ❌" in text and "BOTH end_days_ago 0 and 180" in text
    assert "elevated 2" in text
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", radar-against" in wf and "radar-against) MODULE=radar_against_test" in wf
