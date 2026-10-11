"""
The channel's exit plan vs alternatives, as a member would trade each
published signal (limit fill at the entry within 24h, closed after 72h).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import channel_exit_compare as cx                                      # noqa: E402
from test_cadence_compare import market, stub                          # noqa: E402,F401

H = cx.HOUR_MS
REC = {"symbol": "ETH", "direction": "LONG", "slot_ms": 0, "entry": 100.0, "sl": 95.0,
       "tp_targets": [104.0, 108.0, 112.0], "strength": 70.0}


def bars(*hl):
    return [{"timestamp": i * H, "open": (h + l) / 2, "high": h, "low": l,
             "close": (h + l) / 2} for i, (h, l) in enumerate(hl)]


def sim(cands, **kw):
    kw.setdefault("fee_bps", 0)
    kw.setdefault("slippage_bps", 0)
    return cx.simulate(REC, cands, **kw)


def test_no_fill_when_price_never_touches_the_entry():
    assert sim(bars((103, 101), (104, 102)), shares=(1.0,)) is None


def test_today_tp1_then_breakeven():
    t = sim(bars((101, 99), (104.5, 100.5), (101, 99.5)), shares=(0.5, 0.3, 0.2), be="tp1")
    # half at 104 (+4%), rest stopped at entry (0%) -> +2%
    assert t["net_pct"] == pytest.approx(2.0) and t["outcome"] == "be"


def test_stop_first_on_a_candle_touching_both():
    t = sim(bars((101, 99), (105, 94)), shares=(0.5, 0.5), be="tp1")
    assert t["outcome"] == "sl" and t["net_pct"] == pytest.approx(-5.0)


def test_all_targets_and_two_target_plan():
    path = bars((101, 99), (104.5, 101), (108.5, 104), (112.5, 108))
    three = sim(path, shares=(0.5, 0.3, 0.2), be="tp1")
    assert three["outcome"] == "tp3"
    assert three["net_pct"] == pytest.approx(0.5 * 4 + 0.3 * 8 + 0.2 * 12)
    two = sim(path, shares=(0.6, 0.4), be="tp1")
    assert two["outcome"] == "tp2" and two["net_pct"] == pytest.approx(0.6 * 4 + 0.4 * 8)


def test_be_at_1r():
    # risk 5, so 1R = 105: price reaches 105.5 (below TP1 107), stop goes to entry
    rec = {**REC, "tp_targets": [107.0, 110.0]}
    t = cx.simulate(rec, bars((101, 99), (105.5, 101), (101, 99.5)), shares=(0.5, 0.5),
                    be=1.0, fee_bps=0, slippage_bps=0)
    assert t["outcome"] == "be" and t["net_pct"] == pytest.approx(0.0)


def test_timeout_closes_at_market():
    path = bars((101, 99), *[(102, 100)] * 80)
    t = sim(path, shares=(1.0,), max_age_h=10)
    assert t["outcome"] == "timeout" and t["net_pct"] == pytest.approx(1.0)


def test_costs_on_every_leg():
    t = cx.simulate(REC, bars((101, 99), (104.5, 101)), shares=(1.0,), fee_bps=5,
                    slippage_bps=5)
    assert t["net_pct"] == pytest.approx(4.0 - 0.2)


def test_metrics():
    m = cx.metrics([{"net_pct": 2.0, "closed_at": 1}, {"net_pct": -1.0, "closed_at": 2},
                    None, {"net_pct": -1.0, "closed_at": 3}])
    assert m["trades"] == 3 and m["total_pct"] == 0.0 and m["max_dd_pct"] == 2.0
    assert m["pf"] == 1.0 and cx.metrics([None]) == {"trades": 0}


def test_compare_reports_every_plan(stub):
    res = cx.compare(market(), core=["BTC", "ETH", "SOL"], days=5)
    assert [r["label"] for r in res["all"]] == [p[0] for p in cx.PLANS]
    assert len(res["strong"]) == len(cx.PLANS)
    assert cx.render_telegram(res).startswith("🎯")


def test_worst_mae_rows_are_named():
    import signal_analytics as sa
    rows = [{"id": i, "symbol": "X", "mae_pct": m, "status": "SL_HIT",
             "realized_return_pct": -1} for i, m in enumerate([-2.0, -206.0, -4.0, -1.0])]
    rep = sa.excursion_report(rows)
    assert [r["id"] for r in rep["worst_mae_rows"]] == [1, 2, 0]
