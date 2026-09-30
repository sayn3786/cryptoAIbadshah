"""
Reward-to-risk and sizing backtest (the lopsided v54 trades).

The two live trades that prompted it are the fixtures: ICP (fill 3.136, stop
2.997, TP1 3.141, TP2 3.200) and ADA (0.2552 / 0.2425 / 0.2573 / 0.2594) both
planned about 0.25R at the fill. Also checks TP scaling, risk-based sizing and
its clamps, the $ exposure cap, and that a coin HL doesn't list alerts nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import entry_exit_compare as ee                                       # noqa: E402
import ops_alerts                                                     # noqa: E402
import rr_compare as rr                                               # noqa: E402
from test_cadence_compare import H, market, stub                      # noqa: E402,F401
from test_entry_exit_compare import NOFEE, SLOT, bar, rec             # noqa: E402


def test_the_live_icp_and_ada_trades_planned_about_a_quarter_r():
    icp = ee.rr_at_fill(3.136, 2.997, 3.141, 3.200, 0.5, True)
    ada = ee.rr_at_fill(0.2552, 0.2425, 0.2573, 0.2594, 0.5, True)
    assert icp == pytest.approx(0.248, abs=0.005)
    assert ada == pytest.approx(0.248, abs=0.005)


def test_rr_at_fill_edge_cases():
    assert ee.rr_at_fill(100, 97, 103, 106, 0.5, True) == pytest.approx(1.5)
    assert ee.rr_at_fill(100, 103, 97, 94, 0.5, False) == pytest.approx(1.5)
    assert ee.rr_at_fill(104, 97, 103, 106, 0.5, True) == pytest.approx(2 / 7 * 0.5)  # TP1 behind
    assert ee.rr_at_fill(100, 97, 103, None, 0.5, True) == pytest.approx(1.0)
    assert ee.rr_at_fill(100, 100, 103, 106, 0.5, True) == 0.0


def test_min_rr_guard_skips_a_late_fill():
    # stop 97, TPs 102/103, filled at 101.4: (0.6 + 1.6) / 2 / 4.4 = 0.25R
    late = rec(tps=(102.0, 103.0))
    c = [bar(0, 101.4, 101.5, 101.0), bar(1, 101, 103.5, 100.5)]
    t = ee.simulate_hl(late, c, min_rr_at_fill=0.5, **NOFEE)
    assert t["taken"] is False and t["reason"] == "LOW_RR_AT_FILL"
    assert t["rr_at_fill"] == pytest.approx(0.25)
    ok = ee.simulate_hl(late, c, min_rr_at_fill=0.2, **NOFEE)
    assert ok["taken"] and ok["rr_at_fill"] == pytest.approx(0.25)
    assert ee.simulate_hl(late, c, **NOFEE)["taken"]          # no guard: taken


def test_scale_targets_keeps_the_planned_reward_to_risk():
    r = {**rec(), "sl": 94.0}                          # stop widened from 97 to 94
    out = rr.scale_targets(r, 97.0)
    assert out["tp_targets"] == pytest.approx([106.0, 112.0])
    assert rr.scale_targets({**rec()}, 97.0)["tp_targets"] == [103.0, 106.0]  # not widened


@pytest.mark.parametrize("risk_pct, usd", [(4.0, 25.0), (2.0, 50.0), (1.0, 50.0),
                                           (10.0, 20.0), (0, 25.0)])
def test_risk_size_is_a_fixed_loss_per_stop_within_clamps(risk_pct, usd):
    assert rr.risk_size({"risk_pct": risk_pct}) == pytest.approx(usd)


def test_book_enforces_the_dollar_exposure_cap():
    c1 = [bar(i, 100, 100.5, 99.5) for i in range(0, 60)]      # never exits
    m = {s: {"1H": c1, "2H": []} for s in ("A", "B", "C", "D")}
    recs = [{**rec(), "symbol": s, "strength": 80, "rank": i}
            for i, s in enumerate("ABC", 1)]
    recs.append({**rec(), "symbol": "D", "strength": 80, "rank": 1,
                 "slot_ms": SLOT + 4 * H})
    fixed = rr.book(recs, m, atr_add=0.0)
    assert [t["symbol"] for t in fixed["trades"]] == ["A", "B", "C"]   # 3 x $25 + D > $100
    assert fixed["counts"]["capped"] == 1
    big = rr.book(recs, m, atr_add=0.0, size_fn=lambda t: 50.0)
    assert [t["symbol"] for t in big["trades"]] == ["A"]               # $50 + $52.5 > $100


def test_book_reports_dollars_and_skips_low_rr():
    c = [bar(0, 101.4, 101.5, 101.0)] + [bar(i, 101, 101.5, 100.5) for i in range(1, 20)]
    m = {"ETH": {"1H": c, "2H": []}}
    out = rr.book([{**rec(tps=(102.0, 103.0)), "rank": 1}], m, atr_add=0.0, min_rr=0.5)
    assert out["trades"] == [] and out["counts"]["low_rr"] == 1
    c2 = [bar(0, 100, 103.1, 99.5), bar(1, 104, 106.2, 103.5)]
    t, = rr.book([{**rec(), "rank": 1}], {"ETH": {"1H": c2, "2H": []}}, atr_add=0.0)["trades"]
    assert t["usd"] == 25.0 and t["pnl_usd"] == pytest.approx(t["net_pct"] / 100 * 25)


def test_compare_runs_every_row(stub):
    res = rr.compare(market(("BTC", "ETH", "SOL", "LINK")), core=["BTC", "ETH", "SOL", "LINK"],
                     days=5, floor=62)
    labels = [r["label"] for r in res["rows"]]
    assert labels[:6] == [v[0] for v in rr.VARIANTS]
    assert any(r["group"] == "sized" for r in res["rows"])
    text = rr.render_telegram(res)
    assert text.startswith("⚖️") and "RISK-SIZED" in text


def test_a_coin_hl_does_not_list_sends_no_alert():
    out = ops_alerts.notify_execution([{"id": "s", "symbol": "ENJ", "direction": "LONG"}],
                                      {"results": [{"ok": False,
                                                    "reason": "SYMBOL_NOT_ON_HYPERLIQUID"}]})
    assert out == []
