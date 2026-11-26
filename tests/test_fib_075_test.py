import fib_075_test as f

D = 86_400_000


def bar(i, o, h, l, c):
    return {"timestamp": i * D, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def leg_then_pullback(depth_close_green):
    c = []
    px = 100.0
    for i in range(20):                       # flat base, low pivot at 10
        lo = 90.0 if i == 10 else 99.0
        c.append(bar(len(c), 100, 101, lo, 100))
    for i in range(10):                       # rally to 150
        px = 100 + 5 * (i + 1)
        c.append(bar(len(c), px - 4, px + (1 if i == 9 else 0), px - 5, px))
    for i in range(8):                        # slide: pivot high confirmed after 5 red bars
        px -= 6
        c.append(bar(len(c), px + 6, px + 6.5, px - 0.5, px))
    c.append(bar(len(c), px, px + 2, px - 1, px + 1.5) if depth_close_green else
             bar(len(c), px, px + 1, px - 1, px - 0.5))
    for i in range(30):
        c.append(bar(len(c), px, px + 1, px - 1, px))
    return c


def test_pivots_alternate():
    pv = f.pivots(leg_then_pullback(True))
    kinds = [k for _, k, _ in pv]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


def test_a_green_close_in_the_zone_trades_long():
    evs = f.scan(leg_then_pullback(True), "X")
    assert evs and all(e["direction"] == "LONG" for e in evs)
    assert {e["zone"] for e in evs} <= {z for z, _, _ in f.ZONES}


def test_no_trend_close_no_trade_in_that_zone():
    green = {e["zone"] for e in f.scan(leg_then_pullback(True), "X")}
    red = {e["zone"] for e in f.scan(leg_then_pullback(False), "X")}
    assert len(red) <= len(green)


def test_period_rule_needs_n_avg_and_z():
    evs = [{"zone": f.TARGET, "r": 1.0 if i % 3 else -1.0, "direction": "LONG"} for i in range(60)]
    evs += [{"zone": "0.5-0.618", "r": 1.0 if i % 2 else -1.0, "direction": "LONG"} for i in range(400)]
    p = f.period(evs)
    assert p["zones"][f.TARGET]["n"] == 60 and p["z"] is not None
    assert p["passes"] == (p["z"] >= 2)
    few = f.period(evs[:20] + evs[60:])
    assert not few["passes"]


def test_render_without_trades():
    text = f.render_telegram({"1D": {"periods": {}}, "4H": {"periods": {}}})
    assert "no trades" in text
