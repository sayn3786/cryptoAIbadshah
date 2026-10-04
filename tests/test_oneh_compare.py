"""
1H-only signals, published hourly, vs the live 1H+2H / 4H setup.

primary_tf="1H" scores coins on the 1H chart alone: the 2H chart is never
read, the screen gets the 1H reading as both 1H and 2H, and BTC's own 1H
reading drives the BTC adjustment.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import oneh_compare as oh                                             # noqa: E402
import portfolio_backtest as pbt                                      # noqa: E402
from test_cadence_compare import market, stub                         # noqa: E402,F401


def test_one_hour_mode_reads_only_1h_and_4h_and_screens_1h_as_both(stub, monkeypatch):
    read_tfs, screened = [], []
    real_read = pbt._tf_reading
    real_screen = pbt.rec_policy.screen_candidate

    def reading(sym, tf, win, external=None, market_cap=None):
        read_tfs.append((sym, tf))
        return real_read(sym, tf, win, external, market_cap)

    def screen(h1, h2, h4, corr_factor=1.0, influence=None):
        screened.append(h1 is h2)
        return real_screen(h1, h2, h4, corr_factor=corr_factor, influence=influence)

    monkeypatch.setattr(pbt, "_tf_reading", reading)
    monkeypatch.setattr(pbt.rec_policy, "screen_candidate", screen)
    rep = pbt.replay(market(), interval_hours=1, primary_tf="1H", max_slots=6,
                     execute=False, keep_candidates=True)
    assert rep["execution"]["slots"] == 6
    assert not any(tf == "2H" for _s, tf in read_tfs)          # 2H never read
    assert ("BTC", "1H") in read_tfs                           # BTC on 1H
    assert screened and all(screened)                          # 1H passed as both


def test_default_is_production(stub, monkeypatch):
    read_tfs = []
    real_read = pbt._tf_reading
    monkeypatch.setattr(pbt, "_tf_reading", lambda sym, tf, win, external=None,
                        market_cap=None: read_tfs.append((sym, tf)) or
                        real_read(sym, tf, win, external, market_cap))
    pbt.replay(market(), max_slots=2, execute=False)
    assert ("BTC", "2H") in read_tfs and any(tf == "2H" for s, tf in read_tfs if s != "BTC")


def test_compare_runs_both_setups_at_both_floors(stub):
    res = oh.compare(market(), core=["BTC", "ETH", "SOL"], days=5, floors=(69.0, 62.0))
    assert [r["label"] for r in res["rows"]] == [
        "live: 1H+2H, every 4h · floor 69", "live: 1H+2H, every 4h · floor 62",
        "1H only, every hour · floor 69", "1H only, every hour · floor 62"]
    assert oh.render_telegram(res).startswith("⏱️")


@pytest.mark.parametrize("oneh_pnl, phrase", [(12.0, "beats live"), (3.0, "does not beat")])
def test_verdict(oneh_pnl, phrase):
    rows = [{"label": "live: 1H+2H, every 4h · floor 69", "setup": oh.SETUPS[0][0],
             "trades": 80, "pnl_usd": 8.0, "max_dd_usd": 5.0},
            {"label": "1H only, every hour · floor 62", "setup": oh.SETUPS[1][0],
             "trades": 200, "pnl_usd": oneh_pnl, "max_dd_usd": 5.5}]
    assert phrase in oh.verdict({"rows": rows})
