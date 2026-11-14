"""The chart-pattern outcome study: confirmed breakouts, target vs stop, a
fair random-walk chance, and slices learned on discovery checked on holdout."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pattern_outcome_study as po                                     # noqa: E402

D = 86_400_000


def bar(i, o, c, h=None, lo=None):
    return {"timestamp": i * D, "open": o, "close": c, "high": h if h is not None else max(o, c),
            "low": lo if lo is not None else min(o, c), "volume": 1}


EV = {"direction": "bullish", "entry": 100.0, "target": 110.0, "stop": 95.0}


def test_outcome_target_stop_and_both_in_one_candle_counts_as_stop():
    assert po.outcome(EV, [bar(1, 100, 104), bar(2, 104, 111)])["result"] == "target"
    o = po.outcome(EV, [bar(1, 100, 96, lo=94)])
    assert o["result"] == "stop" and o["r"] < -1
    assert po.outcome(EV, [bar(1, 100, 100, h=111, lo=94)])["result"] == "stop"
    t = po.outcome(EV, [bar(1, 100, 111)])
    assert abs(t["r"] - (2 - po.COST * 100 / 5)) < 1e-9


def test_open_after_max_hold_and_pending_when_too_recent():
    flat = [bar(k, 100, 101) for k in range(po.MAX_HOLD)]
    o = po.outcome(EV, flat)
    assert o["result"] == "open" and o["bars"] == po.MAX_HOLD and 0 < o["r"] < 1
    assert po.outcome(EV, flat[:3])["result"] == "pending"


def _flag(**kw):
    p = {"_kind": "flag", "direction": "bullish", "confirmed": True, "target": 120.0,
         "flag_low": 90.0, "flag_high": 101.0, "breakout_ts": 39 * D,
         "breakout_volume": {"level": "strong"}}
    p.update(kw)
    return p


def test_event_only_on_the_breakout_candle_and_sane_levels():
    w = [bar(k, 100, 100 + (k % 3), h=103, lo=98) for k in range(40)]
    ev = po.event_from(_flag(), w, "1D")
    assert ev and ev["stop"] == 90.0 and ev["target"] == 120.0 and ev["type"] == "bull flag"
    assert ev["feat"]["breakout volume"] == "strong" and ev["feat"]["chart"] == "1D"
    assert po.event_from(_flag(breakout_ts=38 * D), w, "1D") is None      # older breakout
    assert po.event_from(_flag(confirmed=False), w, "1D") is None
    assert po.event_from(_flag(flag_low=150.0), w, "1D") is None          # stop on the wrong side


def test_chance_is_the_random_walk_probability_and_stats():
    evs = [{"result": "target", "chance": 0.25, "r": 3.0, "bars": 4},
           {"result": "stop", "chance": 0.25, "r": -1.0, "bars": 2},
           {"result": "open", "chance": 0.25, "r": 0.5, "bars": 50},
           {"result": "pending", "chance": 0.25, "r": None, "bars": 1}]
    st = po.stats(evs)
    assert (st["n"], st["resolved"], st["target"], st["stop"], st["open"]) == (3, 2, 1, 1, 1)
    assert st["hit_pct"] == 50.0 and st["chance_pct"] == 25.0 and st["edge_pts"] == 25.0
    assert st["avg_r"] == round(2.5 / 3, 2)
    assert st["z"] == round((1 - 0.5) / (2 * 0.25 * 0.75) ** 0.5, 2)


def _ev(ts, hit, val):
    return {"ts": ts, "result": "target" if hit else "stop", "chance": 0.3,
            "r": 2.0 if hit else -1.0, "bars": 3, "feat": {"chart": val}}


def test_a_slice_must_be_promising_on_discovery_and_hold_on_holdout():
    cut = 1000
    disc = [_ev(k, k % 2 == 0, "1D") for k in range(40)]                 # 50% vs 30%, z 2.8
    good = [_ev(cut + k, k % 2 == 0, "1D") for k in range(24)]
    bad = [_ev(cut + k, k % 5 == 0, "1D") for k in range(24)]             # 20% < 30%
    row = po.learn(disc + good, cut)["rows"][0]
    assert row["promising"] and row["confirmed"]
    row = po.learn(disc + bad, cut)["rows"][0]
    assert row["promising"] and not row["confirmed"]
    few = po.learn(disc[:20] + good, cut)["rows"][0]
    assert not few["promising"]
    # well above chance but too few to rule out luck: not promising
    lucky = [_ev(k, k % 5 < 2, "1D") for k in range(30)]                 # 40% vs 30%, z 1.2
    assert not po.learn(lucky + good, cut)["rows"][0]["promising"]


def test_fixed_rule_and_workflow():
    assert (po.MIN_DISC, po.MIN_HOLD, po.EDGE_PTS, po.MIN_AVG_R, po.HOLDOUT_DAYS, po.MAX_HOLD) == \
        (30, 20, 8.0, 0.15, 365, 50)
    assert (po.Z_DISC, po.Z_HOLD) == (2.0, 1.65)
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "pattern-outcomes]" in wf and "python -m pattern_outcome_study --telegram" in wf
