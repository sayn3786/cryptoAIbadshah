import hs_strict_test as hs


def c(ts, close):
    return {"timestamp": ts, "open": close, "high": close + 1, "low": close - 1, "close": close}


def pat(t1, t2, top=True, neck=None):
    neck = neck if neck is not None else (t1 + t2) / 2
    return {"type": "head_shoulders" if top else "inverse_head_shoulders", "neckline": neck,
            "troughs": [{"price": t1, "timestamp": 2}, {"price": t2, "timestamp": 6}],
            "points": [{"role": "left_shoulder", "price": 1, "timestamp": 1},
                       {"role": "head", "price": 1, "timestamp": 4},
                       {"role": "right_shoulder", "price": 1, "timestamp": 8}]}


def test_level_dips_and_no_early_break_pass():
    w = [c(t, 190) for t in range(10)]
    assert hs.strict_ok(pat(186, 184), w)


def test_dips_too_far_apart_fail():
    w = [c(t, 190) for t in range(10)]
    assert not hs.strict_ok(pat(187, 170), w)


def test_close_through_neckline_between_head_and_rs_fails():
    w = [c(t, 190) for t in range(10)]
    w[6] = c(6, 180)                      # neckline 185: closed below it before the right shoulder
    assert not hs.strict_ok(pat(186, 184), w)
    w2 = [c(t, 190) for t in range(10)]
    w2[9] = c(9, 180)                     # after the right shoulder = the breakout, allowed
    assert hs.strict_ok(pat(186, 184), w2)


def test_inverse_mirror():
    w = [c(t, 100) for t in range(10)]
    w[5] = c(5, 110)                      # closed ABOVE an inverse neckline (105) early
    assert not hs.strict_ok(pat(104, 106, top=False), w)


def test_half_needs_z_and_n():
    evs = [{"ts": i, "tf": "4H", "result": "target" if i % 2 else "stop", "r": 1.0 if i % 2 else -1.0,
            "chance": 0.5, "bars": 3, "strict": i % 4 == 1} for i in range(200)]
    h = hs.half(evs)
    assert h["z"] is not None and h["z"] > 1 and h["passes"]
    assert not hs.half(evs[:40])["passes"]


def test_render():
    assert "no events" in hs.render_telegram({"halves": {}})
