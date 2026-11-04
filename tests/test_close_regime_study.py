"""Close picks by market condition (close_regime_study)."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import close_picks_compare as cpc                                      # noqa: E402
import close_regime_study as cr                                        # noqa: E402
from test_amd_forward_close_picks import bar, daily                    # noqa: E402

D = cr.DAY_MS


def trend(n, start, step, d0=20000):
    return [bar((d0 + i) * D, start + step * i, start + step * i + 1, start + step * i - 1,
                start + step * i + 0.5) for i in range(n)]


def test_conditions_use_closed_candles_only():
    up = trend(300, 100, 1.0)
    close = up[250]["timestamp"]                     # day 249 is the last closed one
    c = cr.conditions(up, up, close)
    assert c["btc200"] is True and c["btc50"] is True and c["coin200"] is True
    assert c["adx_trend"] is True                    # a clean trend
    later = up[:250] + [bar(x["timestamp"], 1, 1, 1, 1) for x in up[250:]]
    assert cr.conditions(later, later, close) == c   # the future changes nothing
    down = trend(300, 400, -1.0)
    d = cr.conditions(down, down, down[250]["timestamp"])
    assert d["btc200"] is False and d["btc50"] is False and d["coin200"] is False
    assert cr.conditions(up[:50], up[:50], up[50]["timestamp"])["btc200"] is None


def test_filters_keep_the_right_picks():
    f = dict(cr.FILTERS)
    long_up = {"direction": "LONG", "cond": {"btc200": True, "btc50": True, "adx_trend": False,
                                             "coin200": False}}
    short_up = {**long_up, "direction": "SHORT"}
    assert f["all picks (today)"](short_up)
    assert f["BTC above its 200-day"](short_up)
    assert f["with BTC's 200-day"](long_up) and not f["with BTC's 200-day"](short_up)
    assert f["with BTC's 50/200"](long_up) and not f["with the coin's 200-day"](long_up)
    assert f["with the coin's 200-day"](short_up) and not f["BTC trending (ADX ≥ 20)"](long_up)
    unknown = {"direction": "LONG", "cond": {"btc200": None, "btc50": None, "adx_trend": None,
                                             "coin200": None}}
    # The regime filters drop a pick whose condition is unknown; the rule under
    # test only drops shorts KNOWN to be below the 200-day.
    assert not any(keep(unknown) for name, keep in cr.FILTERS[1:] if name != cr.RULE)


def test_adx_reads_trend_vs_chop():
    random.seed(1)
    chop = [bar(i * D, 100, 100 + random.uniform(0.5, 2), 100 - random.uniform(0.5, 2),
                100 + random.uniform(-0.5, 0.5)) for i in range(80)]
    assert cr.adx(chop) < 20 < cr.adx(trend(80, 100, 1.0))
    assert cr.adx(chop[:20]) is None


def test_end_to_end_with_a_fake_engine():
    syms = ("BTC", "ETH", "SOL")
    d = {s: daily(700, i + 1, start=100 * (i + 1)) for i, s in enumerate(syms)}
    h4s = {s: [bar(c["timestamp"] + k * cpc.H4_MS, c["open"], c["high"], c["low"], c["close"])
               for c in d[s] for k in range(6)] for s in syms}

    def read(win, tf, sym):
        p = float(win[-1]["close"])
        long = float(win[-1]["close"]) >= float(win[-5]["close"])
        return {"direction": "LONG" if long else "SHORT", "strength": 90, "entry": p,
                "sl": p * (0.97 if long else 1.03), "rr_ratio": 2.0, "current_price": p,
                "tp_targets": [p * (1.03 if long else 0.97), p * (1.06 if long else 0.94)]}
    end = d["BTC"][-40]["timestamp"]
    start = end - 400 * D
    picks = cr.replay(d, start_ms=start, end_ms=end, corr={}, read_fn=read, log=lambda *_: None)
    assert picks["1D"] and all("cond" in p for p in picks["1D"])
    res = cr.compare(picks, h4s, start_ms=start, end_ms=end, seeds=2, log=lambda *_: None)
    assert len(res["bounds"]) == 2
    assert {r["filter"] for r in res["rows"]} == {x[0] for x in cr.FILTERS}
    text = cr.render_telegram(res, 3)
    assert "market condition" in text and "with BTC's 200-day" in text


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m close_regime_study --telegram --days 1080" in wf


def test_candidate_needs_both_halves_profit_size_and_random():
    base = (-10.0, -5.0, 1)
    ok = ({"r_total": 5.0, "trades": 40}, {"r_total": 2.0, "trades": 35})
    assert cr.candidate(*ok, 3, 6, base)
    assert not cr.candidate({"r_total": 5.0, "trades": 20}, ok[1], 3, 6, base)   # too few
    assert not cr.candidate({"r_total": -1.0, "trades": 40}, ok[1], 3, 6, base)  # a losing half
    assert not cr.candidate(*ok, 2, 6, base)                                      # random < half
    assert not cr.candidate(*ok, 1, 2, (-10.0, -5.0, 1))                          # not more than base


def test_the_rule_under_test_and_its_check():
    keep = dict(cr.FILTERS)[cr.RULE]
    short_below = {"direction": "SHORT", "cond": {"btc200": False}}
    assert not keep(short_below)
    assert keep({"direction": "LONG", "cond": {"btc200": False}})
    assert keep({"direction": "SHORT", "cond": {"btc200": True}})
    assert keep({"direction": "SHORT", "cond": {"btc200": None}})      # unknown: kept
    res = {"cuts": {"1D": {"BTC<200|SHORT|older": (10, -4.0), "BTC<200|SHORT|newer": (5, 1.0)},
                    "BW": {"BTC<200|SHORT|older": (3, 2.5)}, "1W": {}}}
    lines = cr.rule_check(res)
    assert "15 trades, -3.0R" in lines[0] and "HOLDS" in lines[0]
    assert "does NOT hold" in lines[1] and "no shorts below" in lines[2]


def test_workflow_runs_the_bear_market_check():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m close_regime_study --telegram --days 720 --end-days-ago 1080" in wf
