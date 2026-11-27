import trail_test as tt
import vol_regime_1d_test as vr

H = 3_600_000
D = 86_400_000


def b(i, o, h, l, c):
    return {"timestamp": i * H, "open": o, "high": h, "low": l, "close": c}


def run_up_then_drop():
    bars = [b(0, 100, 100.5, 99.5, 100)]
    px = 100
    for i in range(1, 40):                     # rally: TP1 102, TP2 104 hit, on to ~119
        px += 0.5
        bars.append(b(i, px - 0.5, px + 0.1, px - 0.6, px))
    for i in range(40, 200):                   # then fall back through entry
        px -= 0.2
        bars.append(b(i, px + 0.2, px + 0.3, px - 0.1, px))
    return bars


def test_trail_keeps_more_of_a_run_than_fixed_far_tp3():
    bars = run_up_then_drop()
    a = tt.walk("LONG", 100, 95, [102, 104, 150], bars, "A", 2.0)
    t1 = tt.walk("LONG", 100, 95, [102, 104, 150], bars, "T1", 2.0)
    assert a["result"] == "stop" and t1["result"] == "stop"
    assert t1["r"] > a["r"]                   # A's last third fell back to entry


def test_trail_never_loosens_and_needs_tp2():
    bars = [b(i, 100, 101, 99, 100) for i in range(200)]
    a = tt.walk("LONG", 100, 95, [102, 104, 106], bars, "A", 2.0)
    t = tt.walk("LONG", 100, 95, [102, 104, 106], bars, "T2", 2.0)
    assert a["r"] == t["r"]                   # TP2 never reached: identical


def test_btc_states_thirds():
    btc = []
    for i in range(500):
        amp = 1 if (i // 50) % 2 else 5
        btc.append({"timestamp": i * D, "open": 100, "high": 100 + amp, "low": 100 - amp, "close": 100})
    st = vr.btc_states(btc)
    assert set(st.values()) <= set(vr.STATES) and len(st) > 100


def test_era_rule():
    evs = [{"state": "wild", "r": -1.0 if i % 3 else 1.0, "direction": "LONG"} for i in range(60)]
    evs += [{"state": "calm", "r": 1.0 if i % 3 else -1.0, "direction": "SHORT"} for i in range(200)]
    e = vr.era(evs)
    assert e["wild"]["worse"] and not e["calm"]["worse"]


def test_renders():
    assert "keep the fixed TP3" in tt.render_telegram({"halves": {}, "adopt": None, "coins": [], "split": None,
                                                       "from": None})
    e = vr.era([])
    assert "no change" in vr.render_telegram({"eras": {"A": e, "B": e}, "filter": None, "coins": [], "n": 0})
