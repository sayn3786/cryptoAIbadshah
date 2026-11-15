"""Regime switch: trade only WITH the market (BTC vs its 200-day average)?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import regime_switch_test as rs                                        # noqa: E402

D = rs.DAY_MS


def day(i, c):
    return {"timestamp": i * D, "open": c, "close": c, "high": c + 3, "low": c - 3, "volume": 1}


def test_regime_is_btc_close_vs_its_200_day_average_that_day():
    up = [day(k, 100 + k) for k in range(250)]
    reg = rs.btc_regime(up)
    assert 198 * D not in reg and reg[199 * D] == "bull"                 # needs 200 closes
    down = [day(k, 400 - k) for k in range(250)]
    assert rs.btc_regime(down)[249 * D] == "bear"


def test_with_and_against_the_regime_and_the_cooldown():
    daily = [day(k, 100 + (k % 3)) for k in range(400)]
    regime = {k * D: ("bull" if k < 330 else "bear") for k in range(400)}

    def sig(window, sym, fg):
        i = int(window[-1]["timestamp"]) // D
        return {"direction": "LONG" if i in (300, 305, 340) else "SHORT" if i == 310 else None,
                "strength": 70}
    evs = rs.scan(daily, "X", {}, regime, signal_fn=sig)
    got = [(e["ts"] // D, e["direction"], e["regime"], e["with_regime"]) for e in evs]
    assert got == [(300, "LONG", "bull", True), (310, "SHORT", "bull", False),
                   (340, "LONG", "bear", False)]                        # 305: cooldown
    against = evs[1]
    assert against["regime_trade"] is not against["trade"]               # the regime side, LONG


def _ev(ts, with_regime, win, d="LONG", reg="bull"):
    t = {"result": "target" if win else "stop", "r": 0.95 if win else -1.05}
    return {"ts": ts, "with_regime": with_regime, "direction": d, "regime": reg,
            "trade": t, "regime_trade": t, "symbol": "X"}


def test_era_rule():
    good = [_ev(k, True, k % 3 != 0) for k in range(150)] + [_ev(k, False, k % 2 == 0) for k in range(60)]
    e = rs.era(good)
    assert e["passes"] and e["with"]["resolved"] == 150
    few = [_ev(k, True, k % 3 != 0) for k in range(60)]
    assert not rs.era(few)["passes"]
    close = [_ev(k, True, k % 3 != 0) for k in range(150)] + [_ev(k, False, k % 3 != 0) for k in range(60)]
    assert not rs.era(close)["checks"]["avg R ≥ 0.1 above against-regime"]


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", regime-switch" in wf and "python -m regime_switch_test --telegram" in wf
