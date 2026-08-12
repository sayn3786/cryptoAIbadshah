"""
Price ÷ Daily Chain Buys — the per-subnet buy-pressure screen + multi-day
accumulation.

The TAO ecosystem assembly (get_tao_ecosystem) fetches per-subnet Alpha price
and the daily TAO buy volume from the dTAO pool endpoint. This proves:
  * the two are combined into a `chain_buys` leaderboard (all subnets, ranked by
    price ÷ daily-buys, lowest first),
  * a 24h buy-pressure ranking is exposed, and
  * the free feed's 24h-only buys are snapshotted daily into KV and summed into
    trailing 7d / 30d windows.

The fetchers and KV are stubbed so the test is pure — no network, no API key.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bittensor_eco as eco                                            # noqa: E402


class _FakeKV:
    """In-memory stand-in for the kv module (get_value / set_value)."""
    def __init__(self):
        self.store = {}

    def get_value(self, key):
        return self.store.get(key)

    def set_value(self, key, value, ttl_seconds=0):
        self.store[key] = value
        return True


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


_UNSET = object()


def _run(monkeypatch, subnets, pools, *, kv=_UNSET, date="2026-08-12", flow_hist=None):
    monkeypatch.setattr(eco, "TAOSTATS_KEY", "x")
    monkeypatch.setattr(eco, "kv", _FakeKV() if kv is _UNSET else kv)
    monkeypatch.setattr(eco, "_utc_date", lambda: date)
    eco._cache.clear()
    eco._last_good.clear()
    monkeypatch.setattr(eco, "_network_stats", lambda: {"staked_pct": 50})
    monkeypatch.setattr(eco, "_subnets", lambda: subnets)
    monkeypatch.setattr(eco, "_pools", lambda: pools)
    monkeypatch.setattr(eco, "_flow_from_history", lambda: flow_hist)
    return eco.get_tao_ecosystem()


def test_chain_buys_ranks_lowest_ratio_first_and_lists_all_subnets(monkeypatch):
    subnets = [_subnet(1, "engy", 0.0329), _subnet(2, "Chutes", 0.0858)]
    pools = {1: _pool(0.0329, 144, 100_000), 2: _pool(0.0858, 369, 100_000)}
    out = _run(monkeypatch, subnets, pools)

    cb = out["chain_buys"]
    assert cb["count"] == 2
    assert len(cb["rows"]) == 2, "all subnets, not just top 10"
    assert [r["netuid"] for r in cb["rows"]] == [1, 2], "lowest price/buys first"
    r0 = cb["rows"][0]
    assert r0["daily_chain_buys"] == 144
    assert abs(r0["price_per_buy"] - 0.000228) < 1e-5


def test_24h_buy_pressure_ranks_by_volume(monkeypatch):
    subnets = [_subnet(1, "engy", 0.0329), _subnet(2, "Chutes", 0.0858)]
    pools = {1: _pool(0.0329, 144, 100_000), 2: _pool(0.0858, 369, 100_000)}
    out = _run(monkeypatch, subnets, pools)
    # Chutes (369) buys more than engy (144) → first in the 24h leaderboard.
    assert [r["netuid"] for r in out["chain_buys"]["buys_24h"]] == [2, 1]


def test_seven_and_thirty_day_windows_accumulate_across_days(monkeypatch):
    kv = _FakeKV()
    subnets = [_subnet(1, "engy", 0.0329), _subnet(2, "Chutes", 0.0858)]
    pools = {1: _pool(0.0329, 100, 100_000), 2: _pool(0.0858, 300, 100_000)}

    # Day 1 — one day of history.
    out = _run(monkeypatch, subnets, pools, kv=kv, date="2026-08-11")
    assert out["chain_buys"]["d7"]["days"] == 1
    assert out["chain_buys"]["d7"]["target_days"] == 7

    # Day 2 — a second UTC day; buys sum across both.
    out = _run(monkeypatch, subnets, pools, kv=kv, date="2026-08-12")
    d7 = out["chain_buys"]["d7"]
    assert d7["days"] == 2
    top = d7["rows"][0]
    assert top["netuid"] == 2 and top["buys"] == 600      # 300 + 300


def test_same_day_is_not_double_counted(monkeypatch):
    kv = _FakeKV()
    subnets = [_subnet(1, "engy", 0.0329)]
    pools = {1: _pool(0.0329, 100, 100_000)}
    _run(monkeypatch, subnets, pools, kv=kv, date="2026-08-12")
    out = _run(monkeypatch, subnets, pools, kv=kv, date="2026-08-12")   # same day again
    assert out["chain_buys"]["d7"]["rows"][0]["buys"] == 100, "one write per UTC day"


def test_windows_survive_without_kv(monkeypatch):
    # kv is None (unconfigured): nothing persists, so at most today's in-memory
    # day shows and the ratio table still works — no crash.
    subnets = [_subnet(1, "engy", 0.0329)]
    pools = {1: _pool(0.0329, 100, 100_000)}
    out = _run(monkeypatch, subnets, pools, kv=None)
    cb = out["chain_buys"]
    assert cb["d7"]["days"] <= 1                           # nothing accumulates
    assert len(cb["rows"]) == 1                            # ratio table still works


def test_inflow_outflow_leaders_24h(monkeypatch):
    s1 = _subnet(1, "engy", 0.0329); s1["flow_24h"] = 800
    s2 = _subnet(2, "Chutes", 0.0858); s2["flow_24h"] = -300
    pools = {1: _pool(0.0329, 100, 100_000), 2: _pool(0.0858, 300, 100_000)}
    # flow_24h is unit-calibrated against the aggregate; net 24h = 800−300 = 500
    # → scale resolves to 1.0 so the values pass through.
    out = _run(monkeypatch, [s1, s2], pools, flow_hist={"net_24h_tao": 500})
    ir = out["inflow_ranks"]
    assert [r["netuid"] for r in ir["h24"]["in"]] == [1]      # only positive
    assert ir["h24"]["in"][0]["flow"] == 800
    assert [r["netuid"] for r in ir["h24"]["out"]] == [2]     # only negative
    assert ir["h24"]["out"][0]["flow"] == -300


def test_flow_composition_flags_fresh_vs_rotation(monkeypatch):
    s1 = _subnet(1, "engy", 0.0329); s1["flow_24h"] = 800
    s2 = _subnet(2, "Chutes", 0.0858); s2["flow_24h"] = -300
    pools = {1: _pool(0.0329, 100, 100_000), 2: _pool(0.0858, 300, 100_000)}
    out = _run(monkeypatch, [s1, s2], pools, flow_hist={"net_24h_tao": 500})
    c = out["flow_composition"]["h24"]
    assert c["gross_in"] == 800 and c["gross_out"] == 300 and c["net"] == 500
    assert c["fresh_share_pct"] == 62          # round(500/800*100) = round(62.5) → 62
    assert c["direction"] == "inflow" and c["label"] == "mostly fresh flow"


def test_mostly_rotation_when_flows_cancel(monkeypatch):
    s1 = _subnet(1, "a", 0.03); s1["flow_24h"] = 1000
    s2 = _subnet(2, "b", 0.03); s2["flow_24h"] = -950
    pools = {1: _pool(0.03, 100, 100_000), 2: _pool(0.03, 100, 100_000)}
    out = _run(monkeypatch, [s1, s2], pools, flow_hist={"net_24h_tao": 50})
    c = out["flow_composition"]["h24"]
    assert c["fresh_share_pct"] == 5 and c["label"] == "mostly rotation"


def test_flow_windows_accumulate_across_days(monkeypatch):
    kv = _FakeKV()
    s1 = _subnet(1, "engy", 0.03); s1["flow_24h"] = 400
    s2 = _subnet(2, "b", 0.03); s2["flow_24h"] = -100
    pools = {1: _pool(0.03, 100, 100_000), 2: _pool(0.03, 100, 100_000)}
    _run(monkeypatch, [s1, s2], pools, kv=kv, date="2026-08-11", flow_hist={"net_24h_tao": 300})
    out = _run(monkeypatch, [s1, s2], pools, kv=kv, date="2026-08-12", flow_hist={"net_24h_tao": 300})
    d7 = out["inflow_ranks"]["d7"]
    assert d7["days"] == 2
    assert d7["in"][0]["netuid"] == 1 and d7["in"][0]["flow"] == 800     # 400 + 400


def test_a_subnet_without_buy_volume_is_skipped(monkeypatch):
    subnets = [_subnet(1, "engy", 0.0329), _subnet(2, "Chutes", 0.0858)]
    pools = {1: _pool(0.0329, 144, 100_000),
             2: {"name": None, "alpha_price_tao": 0.0858, "tao_in_pool": 500,
                 "amm_vol_24h_raw": 100_000}}                       # no daily_buys_raw
    out = _run(monkeypatch, subnets, pools)
    assert [r["netuid"] for r in out["chain_buys"]["rows"]] == [1]
