"""Pre-registered: does the 4-hourly signal only work in trending markets?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import trend_filter_test as tf                                         # noqa: E402
from test_htf_vs_random import H, bars, fake, long_wins_sim, market     # noqa: E402,F401


def test_adx_reads_only_closed_4h_candles():
    trend = bars(200, 4 * H, step=1.0)                     # a straight rise: strong trend
    flat = bars(200, 4 * H)                                # flat: no trend
    slot = 150 * 4 * H
    a = tf.adx_at(trend, slot)
    assert a is not None and a >= 25 and tf.adx_at(flat, slot) < 20
    win = tf._closed_4h(trend, slot)
    assert all(b["timestamp"] + 4 * H <= slot for b in win) and len(win) == tf.LOOKBACK_4H
    assert tf.adx_at(trend, 10 * 4 * H) is None            # not enough history


def test_filter_splits_and_verdict(fake):
    m = market(800)
    m["ETH"]["4H"] = bars(200, 4 * H, step=1.0)            # ETH trends, so ADX >= 25

    def replay(mk, **kw):
        return {"candidates": [{"symbol": "ETH", "slot_ms": k * H, "direction": "LONG",
                                "strength": 70} for k in range(300, 790, 4)]}
    res = tf.compare(m, days=20, log=lambda *_: None, seeds=10, replay=replay, sim=long_wins_sim)
    by = {r["label"]: r for r in res["rows"]}
    assert by[tf.TREND]["signals"] + by[tf.NOT]["signals"] == by[tf.ALL]["signals"]
    assert res["trend_share_pct"] == 100


def test_verdict_rule():
    rows = [{"label": tf.ALL, "trades": 200, "pnl_usd": 50},
            {"label": tf.TREND, "trades": 80, "pnl_usd": 90, "pf_usd": 1.3},
            {"label": tf.NOT, "trades": 120, "pnl_usd": -40, "pf_usd": 0.9}]
    assert tf.verdict(rows)["passes"]
    rows[1]["pf_usd"] = 1.1                                 # below 1.15
    assert not tf.verdict(rows)["passes"]
    rows[1]["pf_usd"], rows[2]["pf_usd"] = 1.3, 1.2         # gap only 0.10
    assert not tf.verdict(rows)["passes"]


def test_fixed_rule_render_and_workflow():
    assert (tf.ADX_TREND, tf.PERIODS, tf.MIN_TREND, tf.MIN_PF, tf.PF_GAP) == (25.0, (360, 540), 50, 1.15, 0.15)
    res = {"window": {"start": "2025-08-01", "end": "2025-11-01", "days": 90.0}, "coins": ["BTC"],
           "trend_share_pct": 40, "rows": [{"label": tf.ALL, "signals": 0, "trades": 0}],
           "verdict": {"checks": {"50+ trending trades": False}, "passes": False}}
    text = tf.render_telegram(res)
    assert "FILTER FAILS ❌" in text and "360 and 540" in text and "ADX ≥ 25: 40%" in text
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", trend-filter" in wf and "trend-filter) MODULE=trend_filter_test" in wf
