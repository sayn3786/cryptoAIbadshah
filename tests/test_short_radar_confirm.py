"""Pre-registered confirmation: skip shorts into a bottoming radar? Is the
4-hourly signal still worse than random?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import short_radar_confirm as sc                                       # noqa: E402
from test_htf_vs_random import H, fake, long_wins_sim, market           # noqa: E402,F401


def _replay(k_rev):
    def replay(mk, **kw):
        out = []
        for k in range(300, 790, 4):
            d = "SHORT" if k % 8 == 0 else "LONG"
            out.append({"symbol": "ETH", "slot_ms": k * H, "direction": d, "strength": 70,
                        "reversal_against": k_rev(k, d)})
        return {"candidates": out}
    return replay


def test_only_shorts_with_a_bottoming_radar_are_the_test_group(fake):
    res = sc.compare(market(800), days=20, log=lambda *_: None, seeds=10,
                     replay=_replay(lambda k, d: "high" if (d == "SHORT" and k % 16 == 0)
                                    else ("elevated" if d == "LONG" else None)),
                     sim=long_wins_sim)
    by = {r["label"]: r for r in res["rows"]}
    shorts = by["all SHORTS (context)"]["signals"]
    assert by[sc.TEST]["signals"] + by[sc.OTHER]["signals"] == shorts
    assert abs(by[sc.TEST]["signals"] - shorts / 2) <= 1      # longs with a radar never count
    assert by[sc.SKIP]["signals"] == by[sc.ALL]["signals"] - by[sc.TEST]["signals"]


def test_rule_needs_a_real_pf_gap(fake):
    rows = [{"label": sc.ALL, "trades": 100, "pnl_usd": -50, "random": {"net_p05": -40}},
            {"label": sc.TEST, "trades": 40, "pnl_usd": -30, "pf_usd": 0.60},
            {"label": sc.OTHER, "trades": 30, "pnl_usd": -5, "pf_usd": 0.80},
            {"label": sc.SKIP, "trades": 60, "pnl_usd": -20}]
    v = sc.verdict(rows)
    assert v["rule"] and v["signal_worse_than_random"]
    rows[2]["pf_usd"] = 0.70                                   # gap only 0.10
    assert not sc.verdict(rows)["rule"]
    rows[0]["random"]["net_p05"] = -60                         # -50 is not below the worst 5%
    assert not sc.verdict(rows)["signal_worse_than_random"]


def test_fixed_rule_render_and_workflow():
    assert (sc.PERIODS, sc.MIN_TRADES, sc.PF_GAP) == ((90, 270), 30, 0.15)
    res = {"window": {"start": "2026-05-01", "end": "2026-08-01", "days": 90.0}, "coins": ["BTC"],
           "rows": [{"label": sc.ALL, "signals": 0, "trades": 0}],
           "verdict": {"checks": {"30+ such shorts": False}, "rule": False,
                       "signal_worse_than_random": True}}
    text = sc.render_telegram(res)
    assert "RULE FAILS ❌" in text and "WORSE than random" in text and "90 and 270" in text
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", short-radar" in wf and "short-radar) MODULE=short_radar_confirm" in wf
