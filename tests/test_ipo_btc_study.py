"""BTC around big IPOs vs random dates, SpaceX excluded from the verdict."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import ipo_btc_study as ib                                             # noqa: E402

D = ib.DAY_MS


def _closes(fn, start="2019-01-01", days=2800):
    d0 = ib._day(start)
    return {d0 + k * D: fn(k) for k in range(days)}


def test_returns_use_calendar_days_around_day_zero():
    c = _closes(lambda k: 100.0 + k)
    d0 = ib._day("2019-05-10")
    k0 = (d0 - ib._day("2019-01-01")) // D
    assert ib.ret(c, d0, -50, 0) == ((100 + k0) / (100 + k0 - 50) - 1) * 100
    assert ib.ret(c, ib._day("2030-01-01"), 0, 18) is None


def test_flat_market_is_not_supported_and_spacex_is_not_counted():
    res = ib.study(_closes(lambda k: 100.0), draws=500)
    w = res["windows"][0]
    assert w["n"] == len(ib.IPOS) - 1 and w["avg"] == 0.0 and not res["supported"]
    assert any(r["name"] == "SpaceX" for r in res["rows"])


def test_a_real_dip_into_every_ipo_is_supported():
    ipo_days = [ib._day(d) for _, d in ib.IPOS]
    base = ib._day("2019-01-01")

    def price(k):
        t = base + k * D
        for d0 in ipo_days:
            if d0 - 50 * D <= t <= d0:
                return 100.0 - 30 * (t - (d0 - 50 * D)) / (50 * D)
        return 100.0
    res = ib.study(_closes(price), draws=2000)
    w = res["windows"][0]
    assert w["down"] == w["n"] and w["p"] < 0.05 and res["supported"]


def test_p_value_tails():
    pool = [float(x) for x in range(-10, 11)]
    assert ib.p_value([-10.0, -10.0], pool, "low", draws=2000) < 0.05
    assert ib.p_value([10.0, 10.0], pool, "high", draws=2000) < 0.05
    assert ib.p_value([0.0], pool, "low", draws=2000) > 0.3


def test_render_and_workflow():
    res = ib.study(_closes(lambda k: 100.0), draws=200)
    text = ib.render_telegram(res)
    assert "SpaceX 2026-06-12" in text and "not counted" in text and "NOT SUPPORTED" in text
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", ipo-btc" in wf and "python -m ipo_btc_study --telegram" in wf
