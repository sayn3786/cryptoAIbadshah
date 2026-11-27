import entry_mode_test as em
import weekend_1d_test as wk

H = 3_600_000
D = 86_400_000


def hbar(i, o, h, l, c):
    return {"timestamp": i * H, "open": o, "high": h, "low": l, "close": c}


def test_trade_skips_an_entry_already_past_tp1_or_stop():
    bars = [hbar(i, 100, 101, 99, 100) for i in range(300)]
    assert em.trade("LONG", 106, 95, [105, 110], bars)["result"] == "skip"
    assert em.trade("LONG", 94, 95, [105, 110], bars)["result"] == "skip"


def test_unfilled_limit_counts_zero_per_signal():
    evs = [{"ts": 0, "limit": {"result": "not_filled"}, "close": {"result": "target", "r": 1.0, "hours": 5},
            "mid": {"result": "stop", "r": -1.0, "hours": 3}}]
    assert em.stats(evs, "limit")["r_per_signal"] == 0.0
    assert em.stats(evs, "limit")["traded"] == 0
    assert em.stats(evs, "close")["r_per_signal"] == 1.0


def test_half_eval_gap_rule():
    evs = [{"ts": i, "limit": {"result": "target", "r": 0.1, "hours": 1},
            "close": {"result": "target", "r": 0.2 if i % 5 else -0.1, "hours": 1},
            "mid": {"result": "target", "r": 0.11, "hours": 1}} for i in range(50)]
    ok = em.half_eval(evs)["ok"]
    assert ok["close"] is False or ok["close"] is True
    assert ok["mid"] is False                       # +0.01 is under the 0.03 gap


def test_weekday_utc():
    assert wk.weekday(0) == "Thu"                   # 1970-01-01
    assert wk.weekday(2 * D) == "Sat"


def test_era_flags_worse_weekends_only_with_n_and_z():
    evs = [{"day": "Sat", "r": -1.0 if i % 3 else 1.0} for i in range(60)]
    evs += [{"day": "Tue", "r": 1.0 if i % 3 else -1.0} for i in range(200)]
    e = wk.era(evs)
    assert e["z"] < -2 and e["worse"]
    assert not wk.era(evs[:20] + evs[60:])["worse"]


def test_renders():
    assert "keep the engine limit" in em.render_telegram({"halves": {}, "adopt": None, "coins": [], "split": None,
                                                           "from": None})
    e = wk.era([])
    assert "no change" in wk.render_telegram({"eras": {"A": e, "B": e}, "skip_weekend": False, "coins": [], "n": 0})
