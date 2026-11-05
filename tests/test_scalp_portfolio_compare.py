"""The portfolio replay runs the execution rules (scalp_guard) like the live book."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_guard as sg                                               # noqa: E402
import scalp_portfolio_compare as spc                                  # noqa: E402

M = spc.M15
COINS = ("BTC", "ETH", "SOL", "ADA")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("SCALP_MAX_NEW_PER_CANDLE", "SCALP_MAX_LONGS", "SCALP_MAX_SHORTS",
              "SCALP_MAX_RISK_USD", "SCALP_STOPOUT_COOLDOWN_HOURS", "SCALP_HOT_GUARD"):
        monkeypatch.delenv(k, raising=False)


def flat(n=400):
    return [{"timestamp": i * M, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0,
             "volume": 1.0} for i in range(n)]


def cand(sym, slot, d="LONG", strength=110.0, q_ok=True):
    sgn = 1 if d == "LONG" else -1
    return {"symbol": sym, "slot_ms": slot, "direction": d, "strength": strength, "entry": 100.0,
            "sl": 100 - sgn * 0.909, "target": 100 + sgn * 1.364,
            "quality": {"ok": q_ok, "ext_atr": 0.5}}


def stop_sim(hold_bars=2):
    """Every trade is stopped `hold_bars` candles after its signal."""
    def sim(rec, bars, **kw):
        return {"taken": True, "outcome": "stop", "closed_at": rec["slot_ms"] + hold_bars * M,
                "net_pct": -1.04}
    return sim


def data(cands):
    q = {}
    for c in cands:
        q.setdefault(c["slot_ms"], {}).setdefault(c["symbol"], {})[c["direction"]] = True
    return {"cands": cands, "qualifying": q}


def run(d, **flags):
    m15 = {s: flat() for s in COINS}
    kw = {"limits": False, "reentry": False, "adds": False, "quality": False, "hot": False,
          **flags}
    return spc.run_book(d, m15, start_ms=0, end_ms=400 * M, sim=stop_sim(), **kw)


def test_baseline_takes_every_long_and_limits_cap_them():
    burst = data([cand(s, 100 * M) for s in COINS])
    base = run(burst)
    assert base["trades"] == 4 and base["max_same_way"] == 4 and base["rejected_pct"] == 0
    lim = run(burst, limits=True)
    assert lim["trades"] == 2 and lim["max_same_way"] == 2
    assert lim["rejected"] == {"CANDLE_LIMIT": 2}
    assert os.getenv("SCALP_MAX_LONGS") is None                         # environment restored


def test_reentry_waits_for_cooldown_and_a_reset():
    # ETH LONG stopped at slot 102; a qualifying LONG read every candle after
    cs = [cand("ETH", (100 + k) * M) for k in range(0, 40)]
    still = run(data(cs), reentry=True)
    assert still["trades"] == 1 and still["rejected"].get("STOPOUT_COOLDOWN")
    assert still["rejected"].get("STOPOUT_NO_RESET")                     # time alone is not enough
    # one quiet candle after the cooldown resets it
    gap = [c for c in cs if c["slot_ms"] != 125 * M]
    reset = run(data(gap), reentry=True)
    assert reset["trades"] == 2
    free = run(data(cs))                                                 # without the rule
    assert free["trades"] > 5 and free["max_stop_run"] > 5


def test_quality_gate_and_metrics():
    cs = [cand("ETH", 100 * M, q_ok=False), cand("SOL", 100 * M)]
    r = run(data(cs), quality=True)
    assert r["trades"] == 1 and r["rejected"] == {"ENTRY_QUALITY": 1}
    assert r["net_usd"] == pytest.approx(-1.04 / 100 * spc.NOTIONAL, abs=0.01)


def test_report_and_workflow():
    res = {"start": 0, "end": 400 * M, "days": 4.2, "coins": list(COINS),
           "rows": [{"label": v[0], **run(data([cand("ETH", 100 * M)]))} for v in spc.VARIANTS]}
    text = spc.render_telegram(res)
    assert "portfolio" in text and "end_days_ago 270" in text and "stop-out run" in text
    assert "how hot the market was" in text and "same-way targets hit" in text
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-portfolio) MODULE=scalp_portfolio_compare" in wf


def test_the_replay_uses_the_live_rules():
    src = open(spc.__file__).read()
    for name in ("sg.Batch", "sg.reentry_block", "sg.observe", "sg.record_exit", "sg.rank_key",
                 "sg.entry_quality"):
        assert name in src, name


def test_the_hot_market_cooldown_in_the_replay():
    coins6 = ("BTC", "ETH", "SOL", "ADA", "XRP", "DOGE")
    m15 = {s: flat() for s in coins6}
    burst = data([cand(s, 100 * M) for s in coins6] + [cand("BTC", 104 * M)] +
                 [cand("ETH", 120 * M, d="SHORT")])
    kw = {"limits": False, "reentry": False, "adds": False, "quality": False, "start_ms": 0,
          "end_ms": 400 * M, "sim": stop_sim()}
    off = spc.run_book(burst, m15, hot=False, **kw)
    on = spc.run_book(burst, m15, hot=True, **kw)
    assert off["trades"] == 8
    assert on["rejected"] == {"HOT_MARKET": 7} and on["trades"] == 1      # only the short
    assert os.getenv("SCALP_HOT_GUARD") is None
    # entries are tagged with the heat at entry
    reads = {b["bucket"]: b["trades"] for b in off["split"]["reads"]}
    assert reads["6-9"] == 6 and reads["1-2"] == 2     # the hour is 4 closes: 104 no longer sees 100
    assert {b["bucket"]: b["trades"] for b in off["split"]["tps"]}["0"] == 8
    assert [v[0] for v in spc.VARIANTS][-2:] == ["+ hot-market cooldown",
                                                 "all changes + hot-market cooldown"]


def test_basket_move_is_the_median_the_trades_way():
    H_ = spc.HOUR_MS
    cl = {"A": {0: 100.0, 4 * H_: 102.0}, "B": {0: 100.0, 4 * H_: 101.0},
          "C": {0: 100.0, 4 * H_: 97.0}, "D": {4 * H_: 50.0}}
    assert spc.basket_move(cl, 4 * H_, "LONG") == pytest.approx(1.0)
    assert spc.basket_move(cl, 4 * H_, "SHORT") == pytest.approx(-1.0)
    assert spc.basket_move({}, 4 * H_, "LONG") is None
