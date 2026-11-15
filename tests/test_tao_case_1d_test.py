"""The TAO case on 1D: SHORT 62+ into a bottoming radar - short or long?"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import tao_case_1d_test as tc                                          # noqa: E402

D = tc.DAY_MS


def _sig(d="SHORT", s=91, mode="bottom", level="elevated", labels=()):
    return {"direction": d, "strength": s,
            "reversal_radar": {"mode": mode, "level": level,
                               "signals": [{"label": x} for x in labels]}}


def test_classify_the_screenshot_case_and_its_signs():
    g = tc.classify(_sig(labels=("RSI oversold", "Extreme fear", "Stoch RSI turning up")))
    assert tc.CASE in g and tc.PLAIN not in g
    assert "SHORT 62+ with 'RSI oversold' (context)" in g and "SHORT 62+ with 'Extreme fear' (context)" in g
    assert not any("Stoch" in x for x in g)                          # not one of the five
    assert tc.classify(_sig(level="building")) == [tc.PLAIN]          # radar not hot
    assert tc.classify(_sig(s=61)) == []                             # below the floor
    assert tc.classify(_sig(d="LONG", mode="top", level="high")) == [tc.MIRROR]


def _daily(n=400):
    return [{"timestamp": k * D, "open": 100 + (k % 5), "close": 100 + ((k + 1) % 5),
             "high": 106, "low": 94, "volume": 1} for k in range(n)]


def test_scan_trades_both_sides_with_cooldown_and_passes_fear_greed():
    seen_fg = []

    def sig_fn(window, sym, fg):
        i = int(window[-1]["timestamp"]) // D
        seen_fg.append(fg)
        return _sig() if i in (300, 310, 330) else {}
    fg = {k * D: 10 for k in range(400)}
    evs = tc.scan(_daily(), "TAO", fg, signal_fn=sig_fn)
    case = [e for e in evs if e["family"] == tc.CASE]
    assert [e["ts"] // D for e in case] == [300, 330]                # 310 inside the cooldown
    assert case[0]["direction"] == "SHORT" and case[0]["opp"]["result"] in ("target", "stop", "open")
    assert set(seen_fg) == {10}


def test_fear_greed_parse():
    class R:
        def json(self):
            return {"data": [{"value": "10", "timestamp": "1794700800"}, {"value": "x"}]}

    class S:
        def get(self, *a, **k):
            return R()
    assert tc.fetch_fear_greed(S()) == {1794700800000: 10}


def test_workflow():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert ", tao-case" in wf and "python -m tao_case_1d_test --telegram" in wf
