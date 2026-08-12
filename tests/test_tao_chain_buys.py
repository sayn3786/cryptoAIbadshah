"""
Price ÷ Daily Chain Buys — the per-subnet buy-pressure screen.

The TAO ecosystem assembly (get_tao_ecosystem) already fetches per-subnet Alpha
price and the daily TAO buy volume from the dTAO pool endpoint. This proves the
two are combined into a `chain_buys` leaderboard: subnets ranked by
price ÷ daily-buys, lowest first (heaviest buying not yet in price).

The fetchers are stubbed so the test is pure — no network, no API key needed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bittensor_eco as eco                                            # noqa: E402


def _subnet(netuid, name, price):
    return {"netuid": netuid, "name": name, "alpha_price_tao": price,
            "emission": 100, "mcap": 1000, "tao_in_pool": 500,
            "chg_7d": 1.0, "chg_1d": 0.5,
            "flow_24h": None, "flow_7d": None, "flow_30d": None}


def _pool(price, buys_raw, vol_raw):
    return {"name": None, "alpha_price_tao": price, "tao_in_pool": 500,
            "mcap": 1000, "chg_1d": 0.5, "chg_7d": 1.0,
            "daily_buys_raw": buys_raw, "amm_vol_24h_raw": vol_raw,
            "amm_net_24h_raw": buys_raw * 0.1}


def _run(monkeypatch, subnets, pools):
    monkeypatch.setattr(eco, "TAOSTATS_KEY", "x")
    eco._cache.clear()
    eco._last_good.clear()
    monkeypatch.setattr(eco, "_network_stats", lambda: {"staked_pct": 50})
    monkeypatch.setattr(eco, "_subnets", lambda: subnets)
    monkeypatch.setattr(eco, "_pools", lambda: pools)
    monkeypatch.setattr(eco, "_flow_from_history", lambda: None)
    return eco.get_tao_ecosystem()


def test_chain_buys_ranks_lowest_ratio_first(monkeypatch):
    # Gross turnover 200k → scale resolves to 1.0, so buys read as-is.
    subnets = [_subnet(1, "engy", 0.0329), _subnet(2, "Chutes", 0.0858)]
    pools = {1: _pool(0.0329, 144, 100_000), 2: _pool(0.0858, 369, 100_000)}
    out = _run(monkeypatch, subnets, pools)

    cb = out["chain_buys"]
    assert [r["netuid"] for r in cb["rows"]] == [1, 2], "lowest price/buys first"
    r0 = cb["rows"][0]
    assert r0["daily_chain_buys"] == 144
    # 0.0329 / 144 ≈ 0.000228 (matches the study's ratio column)
    assert abs(r0["price_per_buy"] - 0.000228) < 1e-5
    assert cb["rows"][1]["price_per_buy"] > r0["price_per_buy"]


def test_a_subnet_without_buy_volume_is_skipped(monkeypatch):
    subnets = [_subnet(1, "engy", 0.0329), _subnet(2, "Chutes", 0.0858)]
    pools = {1: _pool(0.0329, 144, 100_000),
             2: {"name": None, "alpha_price_tao": 0.0858, "tao_in_pool": 500,
                 "amm_vol_24h_raw": 100_000}}                       # no daily_buys_raw
    out = _run(monkeypatch, subnets, pools)
    assert [r["netuid"] for r in out["chain_buys"]["rows"]] == [1]


def test_zero_or_missing_buys_do_not_divide_by_zero(monkeypatch):
    subnets = [_subnet(1, "engy", 0.0329)]
    pools = {1: _pool(0.0329, 0, 100_000)}                          # zero buys
    out = _run(monkeypatch, subnets, pools)
    assert "chain_buys" not in out or out["chain_buys"]["rows"] == []
