import rsi_triple_test as rt

D = 86_400_000


def day(i, c):
    return {"timestamp": i * D, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1}


def test_sma_by_day():
    s = rt.sma_by_day([day(i, float(i + 1)) for i in range(250)])
    assert s[199 * D] == sum(range(1, 201)) / 200
    assert (198 * D) not in s


def test_entry_and_exit_on_daily():
    closes = [100 + i * 0.5 for i in range(260)]          # an uptrend above the 200-day average
    closes += [229, 226, 222, 217, 211]                     # 3+ falling closes: RSI(5) drops below 30
    closes += [216, 222, 228, 234]                          # bounce: RSI(5) back above 50
    s = rt.scan([day(i, c) for i, c in enumerate(closes)], "1D", "X")
    assert s["trades"], "expected one trade"
    t = s["trades"][0]
    assert t["bars"] >= 1 and t["ret"] > 0


def test_below_the_200_day_no_trade():
    closes = [300 - i * 0.5 for i in range(260)] + [168, 165, 161, 156, 150, 155, 162, 170]
    assert not rt.scan([day(i, c) for i, c in enumerate(closes)], "1D", "X")["trades"]


def test_period_rule_and_yardstick():
    tr = [{"ret": 0.05, "base": 0.01, "bars": 2} for _ in range(29)] + [{"ret": 0.06, "base": 0.0, "bars": 2}]
    p = rt.period(tr)
    assert p["n"] == 30 and p["passes"]
    assert not rt.period(tr[:29])["passes"]
    assert abs(rt.yardstick([1, 2, 4], [True, True, False], 1) - (1 - rt.COST)) < 1e-12


def test_render_empty():
    e = rt.period([])
    res = {tf: {"eras": {"A": e, "B": e}, "edge": False, "n": 0, "coins": 0} for tf in rt.TFS}
    assert "no trades" in rt.render_telegram(res)
