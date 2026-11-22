"""Order blocks (order_blocks.py): causal detection, states, panel rows."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import order_blocks as ob                                            # noqa: E402

D = 86_400_000


def _c(i, o, h, l, c):
    return {"timestamp": i * D, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def _bull_series():
    """Range, a swing high at 105, a down candle, then a strong break up."""
    cs = []
    px = [100, 101, 102, 103, 105, 103, 102, 101, 100, 99, 98]       # swing high at i=4 (105)
    for i, p in enumerate(px):
        cs.append(_c(i, p - 0.5, p + 0.5, p - 1, p))
    cs.append(_c(11, 98.5, 99, 96, 96.5))                            # i=11 DOWN candle: the OB
    cs.append(_c(12, 97, 103, 96.8, 102.5))
    cs.append(_c(13, 102.5, 109, 102, 108))                          # i=13 closes above 105.5
    for i in range(14, 20):
        cs.append(_c(i, 108, 109, 107, 108))
    return cs


def test_bullish_order_block_found_at_the_last_down_candle():
    f = [x for x in ob.formations(_bull_series()) if x["type"] == "bullish"]
    assert f and f[0]["index"] == 11 and f[0]["formed_at"] == 13
    assert f[0]["bottom"] == 96 and f[0]["top"] == 99


def test_states_fresh_tested_broken():
    cs = _bull_series()
    blk = [x for x in ob.formations(cs) if x["type"] == "bullish"][0]
    assert ob.state(blk, cs)["status"] == "fresh"
    cs2 = cs + [_c(20, 108, 108, 98.5, 100)]                         # dips into the zone, holds
    assert ob.state(blk, cs2)["status"] == "tested" and ob.state(blk, cs2)["first_touch"] == 20
    cs3 = cs2 + [_c(21, 100, 100, 94, 95)]                           # closes below the low
    assert ob.state(blk, cs3)["status"] == "broken"
    live = ob.detect(cs3)
    assert all(x["bottom"] != 96 for x in live["bullish"])


def test_detect_reports_nearest_with_distance():
    live = ob.detect(_bull_series())
    b = live["bullish"][0]
    assert b["status"] == "fresh" and b["dist_pct"] < 0 and not b["inside"]


def test_no_lookahead_formation_index():
    cs = _bull_series()
    for x in ob.formations(cs):
        early = [y for y in ob.formations(cs[:x["formed_at"]]) if y["index"] == x["index"]
                 and y["type"] == x["type"]]
        assert not early                                              # unknown before the break


def test_panel_rows():
    from patterns import build_structure_panel
    from test_flag_pattern_correctness import _make_candles
    cs = _make_candles(60, up=True)
    a = {"candles": cs, "symbol": "BTC", "timeframe": "1D", "ema_trend": {}, "supertrend": {},
         "signal": {"direction": "NEUTRAL", "strength": 0},
         "order_blocks": {"bullish": [{"bottom": 90.0, "top": 95.0, "dist_pct": -3.2, "status": "fresh",
                                       "touches": 0, "bars_ago": 12, "inside": False}], "bearish": []}}
    rows = {r["label"]: r for r in build_structure_panel(a)["rows"]}
    assert rows["Bull Order Block"]["value"].startswith("90.0000")
    assert "3.2% below" in rows["Bull Order Block"]["detail"] and "no edge" in rows["Bull Order Block"]["detail"]
    assert rows["Bear Order Block"]["value"] == "—"


def test_order_block_test_scan():
    import order_block_test as obt
    cs = _bull_series() + [_c(20, 108, 108, 98.5, 100)] + [_c(21 + k, 100, 104, 99, 103) for k in range(25)]
    evs = obt.scan(cs, "X")
    assert len(evs) == 1 and evs[0]["kind"] == "bullish" and evs[0]["ts"] == 20 * D


def test_scan_applies_the_cooldown_in_time_order(monkeypatch):
    import order_block_test as obt
    # two blocks: the one formed FIRST is touched LATER; the earlier touch must be kept
    blocks = [{"type": "bullish", "bottom": 90.0, "top": 95.0, "formed_at": 1, "index": 0},
              {"type": "bullish", "bottom": 80.0, "top": 85.0, "formed_at": 2, "index": 1}]
    touch = {id(blocks[0]): 40, id(blocks[1]): 30}
    monkeypatch.setattr(obt.ob, "formations", lambda d: blocks)
    monkeypatch.setattr(obt.ob, "state", lambda b, d, upto=None: {"first_touch": touch[id(b)]})
    daily = [{"timestamp": k * D, "open": 92, "high": 96, "low": 88, "close": 92} for k in range(80)]
    evs = obt.scan(daily, "X")
    assert [e["ts"] for e in evs] == [30 * D]          # the earlier touch wins; 40 is in its cooldown
