"""The pre-registered confirmation of the 4-hourly signal at >= 62."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import htf_confirm as hc                                               # noqa: E402
from test_htf_vs_random import H, fake, long_wins_sim, market           # noqa: E402,F401


def test_as_long_mirrors_a_short():
    s = {"direction": "SHORT", "entry": 100.0, "sl": 101.5, "target": 98.5}
    l = hc.as_long(s)
    assert l["direction"] == "LONG" and l["sl"] == 98.5 and l["target"] == 101.5
    assert hc.as_long({"direction": "LONG", "entry": 1, "sl": 0.9, "target": 1.1})["sl"] == 0.9


def _replay(directions):
    def replay(mk, **kw):
        assert (kw["interval_hours"], kw["primary_tf"]) == (4, "2H")
        return {"candidates": [{"symbol": "ETH", "slot_ms": k * H, "direction": directions(k),
                                "strength": 60 + (k % 10)} for k in range(300, 790, 4)]}
    return replay


def test_a_signal_that_only_rides_longs_fails_the_always_long_check(fake):
    res = hc.compare(market(800), days=20, log=lambda *_: None, seeds=10,
                     replay=_replay(lambda k: "LONG"), sim=long_wins_sim)
    v = res["verdict"]
    assert res["long_share_pct"] == 100
    assert v["checks"]["beats random"] and not v["checks"]["beats always-long"]
    assert not v["passes"]
    rows = {r["label"]: r for r in res["rows"]}
    assert rows[hc.TEST]["signals"] == sum(1 for k in range(320, 790, 4) if 60 + k % 10 >= 62)  # window starts at 320h


def test_render_and_the_fixed_rule():
    assert (hc.FLOOR, hc.PERIODS, hc.MIN_PF, hc.SIGNAL[2], hc.SIGNAL[3]) == (62.0, (900, 1000), 1.15, "4H", 48)
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "htf-confirm) MODULE=htf_confirm" in wf
    res = {"window": {"start": "2024-01-01", "end": "2024-03-01", "days": 60.0}, "coins": ["BTC"],
           "long_share_pct": 55, "rows": [{"label": hc.TEST, "signals": 0, "trades": 0}],
           "verdict": {"checks": {"profit after costs": True, "beats always-long": False}, "passes": False}}
    text = hc.render_telegram(res)
    assert "✅ profit after costs · ❌ beats always-long" in text and "FAIL ❌" in text
    assert "BOTH end_days_ago 900 and 1000" in text and "LONG: 55%" in text
