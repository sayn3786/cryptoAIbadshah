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


from datetime import datetime, timezone                                 # noqa: E402


def test_network_efficiency_estimate_declines_and_floors():
    anchor = datetime(o.EFF_ANCHOR_YEAR, o.EFF_ANCHOR_MONTH, 1, tzinfo=timezone.utc)
    assert o._network_efficiency_estimate(anchor) == o.EFF_ANCHOR_AVG
    # one year later it has improved (lower J/TH), but not below the floor
    later = datetime(o.EFF_ANCHOR_YEAR + 1, o.EFF_ANCHOR_MONTH, 1, tzinfo=timezone.utc)
    assert o._network_efficiency_estimate(later) < o.EFF_ANCHOR_AVG
    far = datetime(o.EFF_ANCHOR_YEAR + 20, o.EFF_ANCHOR_MONTH, 1, tzinfo=timezone.utc)
    assert o._network_efficiency_estimate(far) == o.EFF_FLOOR_AVG
    # never rises above the anchor for dates before it
    before = datetime(o.EFF_ANCHOR_YEAR - 1, 1, 1, tzinfo=timezone.utc)
    assert o._network_efficiency_estimate(before) == o.EFF_ANCHOR_AVG


def test_current_efficiencies_auto_by_default(monkeypatch):
    monkeypatch.delenv("BTC_EFF_AVERAGE_JTH", raising=False)
    monkeypatch.delenv("BTC_EFF_EFFICIENT_JTH", raising=False)
    eff, avg, src = o._current_efficiencies()
    assert src == "auto"                       # no env var required
    assert avg == o._network_efficiency_estimate()
    assert eff == round(avg * o.EFF_EFFICIENT_RATIO, 1)
    assert eff < avg                            # top-tier more efficient than avg


def test_current_efficiencies_env_override_wins(monkeypatch):
    monkeypatch.setenv("BTC_EFF_AVERAGE_JTH", "25")
    monkeypatch.setenv("BTC_EFF_EFFICIENT_JTH", "18")
    eff, avg, src = o._current_efficiencies()
    assert (eff, avg, src) == (18.0, 25.0, "env")


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
