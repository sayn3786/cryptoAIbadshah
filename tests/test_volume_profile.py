import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import volume_profile as vp                                          # noqa: E402
import volume_profile_test as vpt                                    # noqa: E402

D = 86_400_000


def _c(i, lo, hi, c, v):
    return {"timestamp": i * D, "open": c, "high": hi, "low": lo, "close": c, "volume": v}


def test_poc_and_value_area():
    # most volume traded around 100, a little at 90 and 110
    cs = [_c(i, 99, 101, 100, 1000) for i in range(20)] + \
         [_c(20 + i, 89, 91, 90, 10) for i in range(5)] + [_c(25 + i, 109, 111, 110, 10) for i in range(5)]
    p = vp.profile(cs)
    assert 98.5 <= p["poc"] <= 101.5                          # bins are 0.44 wide
    assert p["val"] >= 95 and p["vah"] <= 105                    # 70% sits around 100
    r = vp.read(cs)
    assert r["where"] == "above the value area"                  # last close 110


def test_too_little_data():
    assert vp.profile([_c(0, 1, 2, 1.5, 1)]) is None


def test_scan_uses_only_previous_days():
    cs = [_c(i, 99, 101, 100, 1000) for i in range(31)]
    cs.append(_c(31, 94, 99.5, 99, 1000))                         # dips to VAL from above, holds
    cs += [_c(32 + i, 99, 106, 105, 1000) for i in range(25)]
    evs = vpt.scan(cs, "X")
    assert evs and evs[0]["ts"] == 31 * D and evs[0]["kind"] == "val"


def test_panel_rows():
    from patterns import build_structure_panel
    from test_flag_pattern_correctness import _make_candles
    cs = _make_candles(60, up=True)
    a = {"candles": cs, "symbol": "BTC", "timeframe": "1D", "ema_trend": {}, "supertrend": {},
         "signal": {"direction": "NEUTRAL", "strength": 0},
         "volume_profile": {"poc": 100.0, "vah": 104.0, "val": 96.0, "window": 30,
                            "where": "inside the value area", "poc_dist_pct": -1.2}}
    rows = {r["label"]: r for r in build_structure_panel(a)["rows"]}
    assert rows["Volume POC"]["value"].startswith("100.0000")
    assert "no edge" in rows["Value Area"]["detail"]
