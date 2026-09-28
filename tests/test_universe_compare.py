"""
More coins / every 69+ signal backtest.

Checks the replay returns every candidate, that "top 3" reproduces
production's selection per universe while "all 69+" takes every qualifying
candidate, that HL's caps (open positions, orders per slot) are applied, and
that the new coins' own trades are reported.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import entry_exit_compare as ee                                       # noqa: E402
import portfolio_backtest as pbt                                      # noqa: E402
import universe_compare as uc                                         # noqa: E402
from test_cadence_compare import H, T0, market, stub                  # noqa: E402,F401
from test_entry_exit_compare import NOFEE, bar, rec                   # noqa: E402


def _c(sym, slot, strength, corr=0.0):
    return {"symbol": sym, "slot_ms": slot, "strength": strength, "avg_tf_strength": strength,
            "quality_score": 50, "direction": "LONG", "btc_corr": corr,
            "entry": 100.0, "sl": 97.0, "tp_targets": [103.0, 106.0]}


CANDS = [_c("ETH", 1, 80), _c("SOL", 1, 75), _c("LTC", 1, 72), _c("XRP", 1, 70),
         _c("ADA", 1, 60), _c("ETH", 2, 65)]


def test_replay_can_return_every_candidate(stub):
    rep = pbt.replay(market(("BTC", "ETH", "SOL", "LINK", "SUI")), max_slots=3,
                     execute=False, keep_published=True, keep_candidates=True)
    assert len(rep["candidates"]) >= len(rep["published"]) > 0
    per_slot = {}
    for c in rep["candidates"]:
        per_slot.setdefault(c["slot_ms"], []).append(c["rank"])
    assert all(sorted(r) == list(range(1, len(r) + 1)) for r in per_slot.values())


def test_top3_is_chosen_within_the_universe():
    core = ["BTC", "ETH", "SOL", "XRP", "ADA"]
    t = uc.top3(CANDS, core)
    assert [(c["symbol"], c["slot_ms"]) for c in t] == \
        [("ETH", 1), ("SOL", 1), ("XRP", 1), ("ETH", 2)]
    t_all = uc.top3(CANDS, core + ["LTC"])
    assert [c["symbol"] for c in t_all if c["slot_ms"] == 1] == ["ETH", "SOL", "LTC"]


def test_all_floor_takes_every_qualifying_candidate():
    got = uc.all_floor(CANDS, ["ETH", "SOL", "LTC", "XRP", "ADA"], 69)
    assert [(c["symbol"], c["rank"]) for c in got] == \
        [("ETH", 1), ("SOL", 2), ("LTC", 3), ("XRP", 4)]


def test_hl_caps_limit_open_positions_and_orders_per_slot():
    c1 = [bar(i, 100, 100.5, 99.5) for i in range(0, 60)]        # nothing ever exits
    m = {s: {"1H": c1, "2H": []} for s in ("A", "B", "C", "D", "E")}
    pub = [{**rec(), "symbol": s, "rank": i} for i, s in enumerate("ABCD", 1)]
    pub.append({**rec(), "symbol": "E", "slot_ms": rec()["slot_ms"] + 4 * H, "rank": 1})
    capped = ee.run_book(pub, m, skip=lambda r: False, exit_cfg=ee.HL_TODAY,
                         min_strength=62, max_open=3, max_per_slot=3, **NOFEE)
    assert [t["symbol"] for t in capped["trades"]] == ["A", "B", "C"]
    assert capped["counts"]["capped"] == 2                     # D (slot cap), E (3 open)
    free = ee.run_book(pub, m, skip=lambda r: False, exit_cfg=ee.HL_TODAY,
                       min_strength=62, **NOFEE)
    assert len(free["trades"]) == 5


def test_compare_runs_every_set_under_both_cap_modes(stub):
    m = market(("BTC", "ETH", "SOL", "LTC"))
    res = uc.compare(m, core=["BTC", "ETH", "SOL"], days=5, floor=62)
    assert res["coins"] == {"core": 3, "extended": 4, "new": ["LTC"]}
    assert [r["group"] for r in res["rows"]] == ["HL caps today (3 open)"] * 4 + ["no cap"] * 4
    assert res["rows"][0]["label"] == "3 coins, top 3 (today)"
    assert res["rows"][0]["new_coins"] == {"trades": 0}
    text = uc.render_telegram(res)
    assert text.startswith("🪙") and "NO CAP" in text and "LTC" in text


def test_excluded_coin_never_enters():
    assert "GOMINING" in uc.EXCLUDED


def test_verdict_mentions_new_coins_and_the_cap():
    base = {"group": "HL caps today (3 open)", "label": "30 coins, top 3 (today)",
            "trades": 50, "total_net_pct": 10.0, "max_open": 3, "new_coins": {"trades": 0}}
    more = {"group": "HL caps today (3 open)", "label": "49 coins, all 69+", "trades": 70,
            "total_net_pct": 15.0, "max_open": 3,
            "new_coins": {"trades": 20, "avg_net_pct": 0.1}}
    free = {"group": "no cap", "label": "49 coins, all 69+", "trades": 120,
            "total_net_pct": 30.0, "max_open": 9, "new_coins": {"trades": 40, "avg_net_pct": 0.05}}
    v = uc.verdict({"rows": [base, more, free]})
    assert "49 coins, all 69+ (70 vs 50 trades" in v
    assert "Raising the cap" in v and "up to 9 open" in v and "profitable" in v
