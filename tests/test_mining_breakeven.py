"""
Tests for the miner break-even model: marginal (electricity) vs all-in
(electricity + amortized hardware + opex), and env-configurability.

Pure/synthetic; no live APIs.
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import btc_onchain as o                                                 # noqa: E402

HS = 9e20   # ~900 EH/s network hashrate


def test_marginal_breakeven_electricity_only():
    # 21 J/TH, $0.06/kWh, 450 BTC/day → per-BTC electricity cost
    be = o._break_even(HS, 21.0)
    ths = HS / 1e12
    expected = round((ths * 21.0 / 1000) * 24 * o.ELECTRICITY_KWH / o.DAILY_BTC_MINED, 0)
    assert be == expected
    # less efficient fleet breaks even HIGHER
    assert o._break_even(HS, 28.0) > o._break_even(HS, 21.0)


def test_all_in_is_above_marginal():
    marginal = o._break_even(HS, 21.0)
    all_in = o._break_even_all_in(HS, 21.0)
    assert all_in > marginal, "all-in (adds hardware + opex) must exceed marginal"
    # avg fleet all-in > efficient all-in
    assert o._break_even_all_in(HS, 28.0) > o._break_even_all_in(HS, 21.0)


def test_all_in_math_explicit():
    # explicit params so the formula is pinned
    be = o._break_even_all_in(HS, 21.0, kwh=0.05, hw_usd_per_th=18.0,
                              hw_life_days=1460.0, opex_pct=0.10)
    ths = HS / 1e12
    elec = (ths * 21.0 / 1000) * 24 * 0.05
    hw = (18.0 * ths) / 1460.0
    expected = round((elec + hw) * 1.10 / o.DAILY_BTC_MINED, 0)
    assert be == expected


def test_all_in_equals_marginal_when_no_hardware_or_opex():
    # zero hardware amortization and zero opex → all-in collapses to marginal
    ai = o._break_even_all_in(HS, 21.0, kwh=o.ELECTRICITY_KWH,
                              hw_usd_per_th=0.0, hw_life_days=1460.0, opex_pct=0.0)
    assert ai == o._break_even(HS, 21.0)


def test_ranges_return_both_tiers():
    m_eff, m_avg = o._break_even_range(HS)
    a_eff, a_avg = o._break_even_all_in_range(HS)
    assert m_avg > m_eff and a_avg > a_eff
    assert a_eff > m_eff and a_avg > m_avg


def test_zero_hashrate_is_none():
    assert o._break_even(0, 21.0) is None
    assert o._break_even_all_in(0, 21.0) is None


def test_env_configurable(monkeypatch):
    # overriding the power cost via env changes the computed marginal break-even
    monkeypatch.setenv("BTC_POWER_COST_KWH", "0.12")   # double the default
    monkeypatch.setenv("BTC_EFF_AVERAGE_JTH", "30")
    mod = importlib.reload(o)
    try:
        assert mod.ELECTRICITY_KWH == 0.12
        assert mod.EFFICIENCY_AVERAGE_J_TH == 30.0
        # doubling $/kWh doubles the electricity break-even
        base = mod._break_even(HS, 21.0)
        assert abs(base - 2 * round((HS / 1e12 * 21.0 / 1000) * 24 * 0.06 / mod.DAILY_BTC_MINED, 0)) <= 1
    finally:
        monkeypatch.delenv("BTC_POWER_COST_KWH", raising=False)
        monkeypatch.delenv("BTC_EFF_AVERAGE_JTH", raising=False)
        importlib.reload(o)   # restore module defaults for other tests
