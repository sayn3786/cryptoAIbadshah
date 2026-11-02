"""Multi-timeframe confluence zones and the bounce study."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cadence_compare as cc                                           # noqa: E402
import confluence as cf                                                # noqa: E402
import confluence_study as cs                                          # noqa: E402

H = cs.HOUR_MS
D = cs.DAY_MS


def bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def walk(n, seed, start=100.0, step=H, vol=0.006, t0=0):
    random.seed(seed)
    p, out = start, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0, vol)
        out.append(bar(t0 + i * step, o, max(o, p) * (1 + random.uniform(0, vol / 2)),
                       min(o, p) * (1 - random.uniform(0, vol / 2)), p))
    return out


# ── the zones ───────────────────────────────────────────────────────────────

def test_pivots_need_k_closed_candles_after():
    bars = [bar(i, 100, 101, 99, 100) for i in range(12)]
    bars[5] = bar(5, 100, 105, 99, 100)
    assert cf.pivots(bars)["high"] == [105]
    assert cf.pivots(bars[:8])["high"] == []                  # only 2 candles after it
    bars[7] = bar(7, 100, 105, 99, 100)                       # a double top
    assert cf.pivots(bars)["high"] == [105]                   # its first candle


def test_tf_levels_carry_every_kind_with_weights():
    lv = cf.tf_levels(walk(260, 3, step=D), "1D")
    kinds = {x["kind"] for x in lv}
    assert {"swing", "fib", "fib_key", "ema", "ema200", "prev", "open"} <= kinds
    ema200 = next(x for x in lv if x["label"] == "1D EMA 200")
    assert ema200["w"] == cf.TF_WEIGHT["1D"] * cf.KIND_WEIGHT["ema200"]
    assert not any(x["kind"] == "prev" for x in cf.tf_levels(walk(260, 3), "1H"))
    assert not any("200" in x["label"] for x in cf.tf_levels(walk(150, 3), "1H"))


def test_zones_merge_stacked_levels_and_mark_sides():
    lv = [{"price": 100.0, "w": 2.0, "tf": "1D", "label": "1D EMA 50"},
          {"price": 100.2, "w": 3.0, "tf": "1W", "label": "1W fib 0.618"},
          {"price": 100.3, "w": 1.0, "tf": "1H", "label": "1H swing low"},
          {"price": 110.0, "w": 4.0, "tf": "1M", "label": "1M previous high"}]
    zs = cf.zones(lv, price=105.0, tol=0.25)
    assert len(zs) == 2
    s, r = zs
    assert s["side"] == "support" and s["score"] == 6.0 and (s["lo"], s["hi"]) == (100.0, 100.3)
    assert abs(s["mid"] - (100 * 2 + 100.2 * 3 + 100.3) / 6) < 1e-9 and s["tfs"] == ["1H", "1D", "1W"]
    assert r["side"] == "resistance" and r["score"] == 4.0
    near = cf.nearest(zs, min_score=5)
    assert near["support"] is s and near["resistance"] is None
    assert "strength 6" in cf.describe(s) and "1W fib 0.618" in cf.describe(s)
    wide = [{"price": 100 + 0.2 * k, "w": 1.0, "tf": "1H", "label": str(k)} for k in range(10)]
    assert all(z["hi"] - z["lo"] <= 0.5 + 1e-9 for z in cf.zones(wide, 200, 0.25))   # capped width


# ── the study ───────────────────────────────────────────────────────────────

def test_outcome_is_measured_from_the_touch_close_break_first():
    h1 = [bar(0, 100, 100.2, 99.5, 100)]
    up = h1 + [bar(H, 100, 101.1, 99.6, 101)]
    assert cs.outcome(up, 0, 1.0, "support") == 1.0
    both = h1 + [bar(H, 100, 101.1, 98.9, 100)]
    assert cs.outcome(both, 0, 1.0, "support") == -1.0      # pessimistic
    assert cs.outcome(both, 0, 1.0, "resistance") == -1.0
    flat = h1 + [bar(H * k, 100, 100.3, 99.8, 100.5) for k in range(1, 30)]
    assert cs.outcome(flat, 0, 1.0, "support") == 0.5       # timeout: the move, capped


def test_touch_needs_to_come_from_the_far_side():
    z = {"lo": 99.0, "hi": 99.5}
    assert cs.touched(z, 100.0, bar(0, 100, 100.1, 99.4, 99.8)) == "support"
    assert cs.touched(z, 100.0, bar(0, 100, 100.1, 99.6, 99.8)) is None
    assert cs.touched(z, 98.0, bar(0, 98, 99.1, 97.9, 98.5)) == "resistance"


def test_levels_never_see_a_later_candle():
    h1 = walk(24 * 40, 5)
    daily = walk(300, 6, step=D, t0=-300 * D)
    lv = cs.Levels(h1, cc.aggregate(h1, 4), daily)
    t = 20 * D + 5 * H
    before = sorted((x["label"], round(x["price"], 9)) for x in lv.at(t))
    h1b = h1[:t // H] + [bar(x["timestamp"], 1, 1e9, 1e-9, 5) for x in h1[t // H:]]
    lv2 = cs.Levels(h1b, cc.aggregate(h1b, 4), daily)
    assert sorted((x["label"], round(x["price"], 9)) for x in lv2.at(t)) == before


def _market(n_coins, seed0):
    market, daily = {}, {}
    for k in range(n_coins):
        s = f"C{k}"
        h1 = walk(24 * 70, seed0 + k, start=100 * (k + 1))
        market[s] = {"1H": h1, "4H": cc.aggregate(h1, 4)}
        old = walk(300, seed0 + 50 + k, start=100 * (k + 1), step=D, vol=0.03, t0=-300 * D)
        days = [bar(h1[j]["timestamp"], h1[j]["open"], max(x["high"] for x in h1[j:j + 24]),
                    min(x["low"] for x in h1[j:j + 24]), h1[j + 23]["close"])
                for j in range(0, len(h1) - 23, 24)]
        daily[s] = old + days
    return market, daily


def test_no_edge_on_a_random_walk_and_the_report():
    market, daily = _market(3, 0)
    res = cs.compare(market, daily, start_ms=30 * D, end_ms=69 * D, log=lambda *_: None)
    assert res["random"]["n"] > 300
    for r in res["rows"]:
        assert not r["passes"], r                               # nothing to find here
        if r["n"] >= 200:
            assert abs(r["z"]) < 3, r                           # noise, honestly sized
    text = cs.render_telegram(res)
    assert "Confluence zones" in text and "random levels" in text


def test_workflow_runs_the_study():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "confluence) MODULE=confluence_study" in wf
