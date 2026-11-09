"""The pre-registered 1H / 4-hourly direction test."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import htf_vs_random as hv                                             # noqa: E402

H = hv.HOUR_MS


def bars(n, tf_ms, start=0, step=0.0):
    out, p = [], 100.0
    for i in range(n):
        o = p
        p = 100.0 + step * i
        out.append({"timestamp": start + i * tf_ms, "open": o, "high": max(o, p) + 0.5,
                    "low": min(o, p) - 0.5, "close": p, "volume": 1.0})
    return out


def market(n=400):
    return {s: {"1H": bars(n, H), "2H": bars(n // 2, 2 * H), "4H": bars(n // 4, 4 * H)}
            for s in ("BTC", "ETH")}


def test_prepare_uses_closed_candles_and_the_signals_chart():
    m = market()
    c = hv.prepare({"symbol": "ETH", "slot_ms": 100 * H, "direction": "LONG", "strength": 70},
                   m, "4H")
    assert c["entry"] == m["ETH"]["1H"][99]["close"]               # the last CLOSED 1H candle
    atr4 = hv.sg._atr(hv._closed(m["ETH"]["4H"], "4H", 100 * H))
    assert c["sl"] == pytest.approx(c["entry"] - 1.5 * atr4)
    assert c["target"] == pytest.approx(c["entry"] + 1.5 * atr4)
    assert all(b["timestamp"] + 4 * H <= 100 * H for b in hv._closed(m["ETH"]["4H"], "4H", 100 * H))
    s = hv.prepare({"symbol": "ETH", "slot_ms": 100 * H, "direction": "SHORT", "strength": 70},
                   m, "1H")
    assert s["sl"] > s["entry"] > s["target"]
    assert hv.prepare({"symbol": "ETH", "slot_ms": 3 * H, "direction": "LONG"}, m, "4H") is None


def long_wins_sim(rec, bars, **kw):
    win = rec["direction"] == "LONG"
    fill = float(bars[0]["open"]) if bars else rec["entry"]
    return {"taken": True, "fill": fill, "net_pct": 1.0 if win else -1.05,
            "closed_at": rec["slot_ms"] + H, "outcome": "tp1" if win else "stop", "r": None}


@pytest.fixture
def fake(monkeypatch):
    def metrics(trades, span):
        pnl = [t["pnl_usd"] for t in trades]
        w = sum(x for x in pnl if x > 0)
        lo = -sum(x for x in pnl if x <= 0)
        return {"trades": len(pnl), "pnl_usd": round(sum(pnl), 2), "pf_usd": round(w / lo, 2) if lo else 99,
                "win_rate_pct": round(100 * sum(1 for x in pnl if x > 0) / len(pnl), 1) if pnl else None}
    monkeypatch.setattr(hv.svr.ss, "metrics", metrics)


def test_compare_runs_both_signals_and_judges_them(fake):
    m = market(800)
    seen = []

    def replay(mk, **kw):
        seen.append((kw["interval_hours"], kw["primary_tf"]))
        step = kw["interval_hours"]
        return {"candidates": [{"symbol": "ETH", "slot_ms": k * H, "direction": "LONG",
                                "strength": 60 + (k % 20)}
                               for k in range(300, 790, step)]}
    res = hv.compare(m, days=20, log=lambda *_: None, seeds=10, replay=replay, sim=long_wins_sim)
    assert seen == [(1, "1H"), (4, "2H")]
    for s in res["signals"]:
        v = hv.verdicts(s)
        assert v["skill"] and v["worth"]                       # always-LONG wins in this fake
        rows = {r["label"]: r for r in s["rows"]}
        assert not rows["strength ≥ 69, direction REVERSED"]["beats_random"]
    text = hv.render_telegram(res)
    assert "direction skill YES" in text and "end_days_ago 700 and 800" in text


def test_the_rule_is_fixed_and_in_the_workflow():
    assert (hv.ATR_STOP, hv.ATR_TARGET, hv.TEST_FLOOR, hv.MIN_PF, hv.PERIODS) == (1.5, 1.5, 69.0, 1.15, (700, 800))
    assert [s[3] for s in hv.SIGNALS] == [12, 48]
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "htf-vs-random) MODULE=htf_vs_random" in wf
