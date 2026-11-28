import pullback_level_test as pl


def bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "timestamp": 0}


def test_nearest_zone_on_the_trend_side():
    zones = [(95, 97), (80, 82), (103, 105)]
    assert pl.nearest(zones, 100, 2.0, "LONG") == (95, 97)        # 1.5 ATR below
    assert pl.nearest(zones, 100, 2.0, "SHORT") == (103, 105)
    assert pl.nearest([(99.8, 99.9)], 100, 2.0, "LONG") is None   # too close (< 0.3 ATR)


def test_pullback_touch_then_reaction_close():
    after = [bar(100, 100, 97.5, 98), bar(98, 98.5, 96, 96.5), bar(96.5, 98, 96, 97.8)]
    assert pl.pullback("LONG", (95, 97), 2.0, after) == 2
    failed = [bar(100, 100, 94, 94.2), bar(94, 99, 94, 98)]        # closed 0.25 ATR below the zone first
    assert pl.pullback("LONG", (95, 97), 2.0, failed) is None
    assert pl.pullback("LONG", (95, 97), 2.0, [bar(100, 101, 99, 100)] * 6) is None   # never touched


def test_short_mirror():
    after = [bar(100, 103.5, 100, 103), bar(103, 103.2, 101, 101.5)]
    assert pl.pullback("SHORT", (103, 105), 2.0, after) == 1


def test_era_verdicts():
    evs = [{"ts": 0, "has_level": True, "base": 0.0, "level": 0.5, "level_traded": True,
            "random": 0.0, "random_traded": True} for _ in range(40)]
    e = pl.era(evs)
    assert e["passes"] and e["better"]
    assert not pl.era(evs[:20])["better"]


def test_render():
    e = pl.era([])
    assert "no ❌" in pl.render_telegram({"eras": {"A": e, "B": e}, "edge": False, "better": False,
                                          "coins": [], "n": 0})
