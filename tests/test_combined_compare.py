"""
Combined backtest: best exit + stop, realistic HL costs, strength bands.

Checks per-leg costs (taker on market legs, maker on limit take-profits), that
a widened stop does not loosen HL's stale-entry guard, the strength-band split
and cap, every row running, and the verdict wording.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import combined_compare as cm                                         # noqa: E402
import entry_exit_compare as ee                                       # noqa: E402
import portfolio_backtest as pbt                                      # noqa: E402
from test_cadence_compare import market, stub                         # noqa: E402,F401
from test_entry_exit_compare import NOFEE, bar, rec                   # noqa: E402


def test_costs_are_charged_per_leg_kind():
    c = [bar(0, 100, 103.1, 99.5), bar(1, 104, 106.2, 103.5)]        # TP1 then TP2
    market_tps = ee.simulate_hl(rec(), c, costs=cm.MARKET_TPS)
    limit_tps = ee.simulate_hl(rec(), c, costs=cm.LIMIT_TPS)
    # gross +4.5%; market: 6.5 x (1 + 0.5 + 0.5) = 13 bps; limit: 6.5 + 1.5 = 8 bps
    assert market_tps["net_pct"] == pytest.approx(4.5 - 0.13)
    assert limit_tps["net_pct"] == pytest.approx(4.5 - 0.08)
    stopped = ee.simulate_hl(rec(), [bar(0, 100, 101, 96.5)], costs=cm.LIMIT_TPS)
    assert stopped["net_pct"] == pytest.approx(-3.0 - 0.13)          # stop is a market leg


def test_default_costs_unchanged():
    c = [bar(0, 100, 101, 96.5)]
    assert ee.simulate_hl(rec(), c, fee_bps=6, slippage_bps=2)["net_pct"] == \
        pytest.approx(-3.0 - 0.16)


def test_a_wider_stop_keeps_the_published_stale_guard():
    # published stop 3% → allowed drift 1.5%; a +1.6% open is stale either way
    wide = pbt.widen_stop(rec(), [], mult=2.0)
    c = [bar(0, 101.6, 102, 101)]
    assert ee.simulate_hl(wide, c, **NOFEE)["reason"] == "STALE_ENTRY"


def test_run_book_applies_the_stop_variant_and_the_band_cap():
    c1 = [bar(i, 100, 100.5, 99.5) for i in range(0, 30)]
    c1[3] = bar(3, 100, 100.2, 96.5)                  # below the 97 stop, above 94
    m = {"ETH": {"1H": c1, "2H": []}, "SOL": {"1H": c1, "2H": []}}
    pub = [{**rec(), "strength": 65}, {**rec(), "symbol": "SOL", "strength": 75}]
    tight = ee.run_book(pub, m, skip=lambda r: False, exit_cfg=ee.HL_TODAY,
                        min_strength=62, **NOFEE)
    wide = ee.run_book(pub, m, skip=lambda r: False, exit_cfg=ee.HL_TODAY,
                       min_strength=62, stop_variant={"mult": 2.0}, **NOFEE)
    assert {t["outcome"] for t in tight["trades"]} == {"stop"}
    assert "stop" not in {t["outcome"] for t in wide["trades"]}
    band = ee.run_book(pub, m, skip=lambda r: False, exit_cfg=ee.HL_TODAY,
                       min_strength=62, max_strength=69, **NOFEE)
    assert [t["symbol"] for t in band["trades"]] == ["ETH"]


def test_bands_split_by_strength():
    t = {"outcome": "stop", "full_stop": True, "tp1_hit": False, "r": -1.0, "closed_at": 1}
    b = cm.bands([{**t, "strength": 65, "net_pct": -3.0},
                  {**t, "strength": 72, "net_pct": 2.0, "outcome": "tp2",
                   "full_stop": False, "r": 1.0}], days=1)
    assert b["<69"]["trades"] == 1 and b["<69"]["total_net_pct"] == -3.0
    assert b["69+"]["trades"] == 1 and b["69+"]["total_net_pct"] == 2.0


def test_compare_runs_every_row(stub):
    res = cm.compare(market(), days=5)
    groups = [r["group"] for r in res["rows"]]
    assert groups[:5] == ["main"] * 5 and groups[5:] == ["costs", "policy"]
    assert res["rows"][0]["label"].startswith("today")
    assert all("bands" in r for r in res["rows"])
    text = cm.render_telegram(res)
    assert text.startswith("🧪 Combined backtest") and "LIMIT TAKE-PROFITS" in text


@pytest.mark.parametrize("best, phrase", [
    ({"avg_net_pct": 0.08, "profit_factor": 1.15, "total_net_pct": 40.0}, "worth a v54"),
    ({"avg_net_pct": 0.02, "profit_factor": 1.02, "total_net_pct": 10.0}, "thin"),
    ({"avg_net_pct": -0.01, "profit_factor": 0.98, "total_net_pct": -5.0}, "no edge"),
])
def test_verdict(best, phrase):
    today = {"label": "today", "trades": 10, "avg_net_pct": -0.1, "profit_factor": 0.9,
             "total_net_pct": -50.0}
    res = {"rows": [today, {"label": "x", "trades": 10, **best}]}
    assert phrase in cm.verdict(res)
