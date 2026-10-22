"""
v59 (HL only): +5 to a LONG on a coin whose last daily close was a daily
momentum setup, for the 24 hours after that close; never to SHORTs.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import daily_setup as dsu                                              # noqa: E402

H = 3_600_000


def _cand(sym, strength, **kw):
    return {"symbol": sym, "strength": strength, "direction": "LONG", "entry": 1.0,
            "sl": 0.9, "tp_targets": [1.1, 1.2], **kw}


@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    monkeypatch.setenv("HL_AUTO_EXECUTE", "on")
    monkeypatch.delenv("HL_EXTRA_SIGNALS", raising=False)
    monkeypatch.delenv("HL_DAILY_MOMENTUM_BOOST", raising=False)
    monkeypatch.delenv("HL_BOTTOM_READ_BOOST", raising=False)
    monkeypatch.setenv("HL_AUTO_MIN_STRENGTH", "69")
    return app


@pytest.fixture
def kv_mem(monkeypatch):
    import kv
    mem = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "get_value", lambda k: mem.get(k))
    return mem


def test_switch(app_mod, monkeypatch):
    assert app_mod._daily_momentum_boost() == 5.0                 # on by default
    for raw, want in (("0", 0.0), ("off", 0.0), ("8", 8.0), ("50", 5.0), ("x", 5.0)):
        monkeypatch.setenv("HL_DAILY_MOMENTUM_BOOST", raw)
        assert app_mod._daily_momentum_boost() == want


def test_boost_lifts_longs_only(app_mod):
    cands = [_cand("ETH", 80), _cand("SOL", 75), _cand("LINK", 72),
             _cand("ADA", 65), _cand("XRP", 63), _cand("AVAX", 66, direction="SHORT")]
    got = app_mod._hl_extra_candidates(cands, cands[:3], {}, {"ADA", "XRP", "AVAX"})
    assert [(c["symbol"], c["display_strength"], c["hl_boost_reason"]) for c in got] == \
        [("ADA", 70, "daily momentum close")]                      # XRP 68 still short; AVAX a SHORT


def test_stacks_with_the_bottom_read_boost(app_mod):
    got = app_mod._hl_extra_candidates([_cand("ADA", 55)], [], {"ADA": {"bullish"}}, {"ADA"})
    assert got[0]["display_strength"] == 70 and got[0]["hl_boost"] == 15
    assert got[0]["hl_boost_reason"] == "1D bottom/top read with the trade + daily momentum close"


def test_off_means_no_boost(app_mod, monkeypatch):
    monkeypatch.setenv("HL_DAILY_MOMENTUM_BOOST", "0")
    assert app_mod._hl_extra_candidates([_cand("ADA", 65)], [], {}, {"ADA"}) == []


def test_stored_set_counts_for_24_hours(app_mod, kv_mem):
    close = 1_000 * H
    app_mod._store_daily_momentum({"ADA": close, "SOL": close - 30 * H}, now_ms=close)
    assert app_mod._hl_daily_momentum(close + 23 * H) == {"ADA"}
    assert app_mod._hl_daily_momentum(close + 25 * H) == set()
    assert app_mod._hl_daily_momentum(close - H) == set()           # never before the close


def test_nothing_stored_or_switched_off(app_mod, kv_mem, monkeypatch):
    assert app_mod._hl_daily_momentum() == set()
    app_mod._store_daily_momentum({"ADA": 10 ** 13})
    monkeypatch.setenv("HL_DAILY_MOMENTUM_BOOST", "off")
    assert app_mod._hl_daily_momentum(10 ** 13 + H) == set()


def test_scan_reads_the_last_closed_daily_candle(app_mod, monkeypatch):
    bars = [{"timestamp": i * 24 * H, "open": 1, "high": 1, "low": 1, "close": 1,
             "volume": 1} for i in range(dsu.WINDOW + 1)]
    monkeypatch.setattr(app_mod, "_fetch_alert_candles",
                        lambda sym, tf, limit=None: ((bars, None) if sym != "BAD"
                                                     else ([], "stale")))
    monkeypatch.setattr(dsu, "read_close", lambda closed, sym: 1 if sym == "ADA" else 0)
    got = app_mod._daily_momentum_scan(["ADA", "SOL", "BAD"])
    assert got == {"ADA": dsu.WINDOW * 24 * H + 24 * H}


def test_boost_marks_reach_the_decisions_report(app_mod, kv_mem):
    from datetime import datetime, timezone
    slot = datetime(2026, 10, 23, 8, tzinfo=app_mod._SGT)
    app_mod._record_hl_boosts(slot, [{"symbol": "ADA", "hl_boost": 5.0,
                                      "hl_boost_reason": "daily momentum close"},
                                     {"symbol": "SOL"}])
    assert app_mod._hl_boosts(slot) == {"ADA": "+5 daily momentum close"}
    assert json.loads(kv_mem[app_mod._hl_boost_key(slot)]) == {"ADA": "+5 daily momentum close"}


def test_setup_rules_are_shared_with_the_backtests():
    import combo_study
    import daily_setup_compare
    assert daily_setup_compare.setup is dsu.setup
    assert combo_study.candle_features is dsu.candle_features
    assert dsu.read_close([], "X") == 0
    win = [{"timestamp": i, "open": 1, "high": 1.01, "low": 0.99, "close": 1, "volume": 1}
           for i in range(dsu.WINDOW)]
    assert dsu.read_close(win, "X", score_fn=lambda w, s: {
        "score_breakdown": {"flags": -20, "macd": -8}}) == -1
