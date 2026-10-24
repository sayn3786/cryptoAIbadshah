"""
The 3-step ICT scalp on 15m: liquidity sweep -> fair value gap -> limit entry
in the gap, stop under the sweep, target the swing beyond.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import sweep_fvg_compare as sf                                         # noqa: E402

M = sf.M15


def b(i, o, h, lo, c):
    return {"timestamp": i * M, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def long_setup():
    """Swing high 110 at 5, swing low 100 at 12, sweep at 16, gap made at 18."""
    bars = []
    for i in range(20):
        bars.append(b(i, 102, 102.2, 101.8, 102))
    bars[5] = b(5, 107, 110, 106, 108)                     # swing high
    bars[12] = b(12, 102, 102.1, 100, 101)                 # swing low
    bars[16] = b(16, 101, 101.5, 99.5, 101)                # wick through 100, close back above
    bars[17] = b(17, 101, 105, 101, 104.5)                 # displacement
    bars[18] = b(18, 104.5, 106, 103, 105.5)               # low 103 > bar 16 high 101.5: gap
    bars[19] = b(19, 105, 105.5, 102.8, 104)               # back into the gap: fills at 103
    return bars


def test_pivots_are_known_only_after_confirmation():
    piv = sf.pivots(long_setup())
    assert (12, 100, 15) in list(piv["low"]) and (5, 110, 8) in list(piv["high"])
    assert sf._latest(piv["low"], 15) is None or sf._latest(piv["low"], 15)[0] != 12
    assert sf._latest(piv["low"], 16)[0] == 12


def test_finds_the_long_setup():
    got = sf.setups(long_setup(), at="top", min_rr=1.5)
    s = [x for x in got if x["direction"] == "LONG"][0]
    assert s["sweep"] == 16 and s["ready"] == 18 and s["limit"] == 103
    assert s["target"] == 110 and abs(s["sl"] - 99.5 * (1 - sf.STOP_BUFFER)) < 1e-9
    assert s["slot_ms"] == 19 * M and s["rr"] >= 1.5


def test_min_rr_and_mid_entry():
    assert not [x for x in sf.setups(long_setup(), min_rr=2.0) if x["direction"] == "LONG"]
    mid = [x for x in sf.setups(long_setup(), at="mid", min_rr=1.5) if x["direction"] == "LONG"][0]
    assert mid["limit"] == (101.5 + 103) / 2


def test_fill_and_stop_first():
    bars = long_setup()
    s = [x for x in sf.setups(bars) if x["direction"] == "LONG"][0]
    assert sf.fill(s, bars) == 19
    bars[19] = b(19, 101, 101, 99, 100)                    # gaps down through the gap: still a fill
    assert sf.fill(s, bars) == 19
    bars[19] = b(19, 106, 107, 104, 106)                   # never back to the gap
    assert sf.fill(s, bars + [b(20 + k, 106, 107, 105, 106) for k in range(10)]) is None


def test_trade_fills_at_the_limit_with_the_maker_fee():
    bars = long_setup() + [b(20, 104, 111, 103.5, 110.5)]
    s = {**[x for x in sf.setups(bars) if x["direction"] == "LONG"][0], "symbol": "ETH"}
    seen = {}

    def sim(rec, candles, **kw):
        seen.update(rec=rec, first=candles[0], kw=kw)
        return {"taken": True, "net_pct": 1.0, "closed_at": rec["slot_ms"] + M, "outcome": "tp1",
                "rr_at_fill": 1.9}
    t = sf.trade(s, bars, simulate=sim)
    assert t and seen["rec"]["entry"] == 103 and seen["first"]["open"] == 103
    assert seen["rec"]["tp_targets"] == [110] and seen["kw"]["costs"] == sf.LIMIT_FILL
    assert seen["kw"]["tp1_frac"] == 1.0 and seen["kw"]["max_hold_hours"] == sf.MAX_HOLD_H


def test_short_mirror():
    flip = [b(x["timestamp"] // M, 200 - x["open"], 200 - x["low"], 200 - x["high"],
              200 - x["close"]) for x in long_setup()]
    s = [x for x in sf.setups(flip) if x["direction"] == "SHORT"][0]
    assert s["limit"] == 97 and s["target"] == 90 and s["sl"] > 100.5


def test_book_one_per_coin_and_counts_missed():
    def sim(rec, candles, **kw):
        return {"taken": True, "net_pct": 2.0, "closed_at": rec["slot_ms"] + 10 * M,
                "outcome": "tp1", "rr_at_fill": 2}
    bars = long_setup()
    s = [x for x in sf.setups(bars) if x["direction"] == "LONG"][0]
    found = [{**s, "symbol": "ETH"}, {**s, "symbol": "ETH"},
             {**s, "symbol": "SOL", "limit": 50.0}]
    out = sf.book(found, {"ETH": bars, "SOL": bars}, simulate=sim)
    assert len(out["trades"]) == 1 and out["missed"] == 1


def test_compare_and_render():
    import random
    random.seed(5)
    bars, p = [], 100.0
    for i in range(sf.sc.WINDOW + 4 * sf.sc.H1_WINDOW + 400):
        o = p
        p *= 1 + random.gauss(0, 0.004)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
    end = int(bars[-1]["timestamp"]) + M
    res = sf.compare({"ETH": bars}, start_ms=end - 4 * 24 * 3_600_000, end_ms=end,
                     read_fn=lambda *a: {"direction": "LONG"}, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in sf.VARIANTS]
    assert sf.render_telegram(res).startswith("🪤")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "sweep-fvg) MODULE=sweep_fvg_compare" in wf


def test_confirmation_modes():
    bars = long_setup()
    s = {**[x for x in sf.setups(bars) if x["direction"] == "LONG"][0], "symbol": "ETH"}
    long_read = lambda w, tf, sym: {"direction": "LONG"}               # noqa: E731
    short_15m = lambda w, tf, sym: {"direction": "SHORT" if tf == "15M" else "LONG"}  # noqa: E731
    big = [b(i, 100, 101, 99, 100) for i in range(sf.sc.WINDOW)] + bars
    s2 = {**s, "ready": s["ready"] + sf.sc.WINDOW}
    assert sf.confirmed(s2, "none", big, [], [], short_15m, {})
    assert sf.confirmed(s2, "15m", big, [], [], long_read, {})
    assert not sf.confirmed(s2, "15m", big, [], [], short_15m, {})
    assert not sf.confirmed(s, "15m", bars, [], [], long_read, {})          # too little history
    assert not sf.confirmed(s2, "1h+15m", big, [], [], long_read, {})       # no 1H history
    labels = [v[0] for v in sf.VARIANTS]
    assert "entry gap top · R:R 1.5+ · 1H + 15m indicators agree" in labels
    assert "entry gap top · R:R 1.5+ · 15m indicators agree" in labels
