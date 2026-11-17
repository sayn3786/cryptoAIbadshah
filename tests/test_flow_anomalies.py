"""Positioning / flow anomalies at the daily close (information only)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import flow_anomalies as fa                                            # noqa: E402

D = 86_400_000


def candles(closes):
    return [{"timestamp": k * D, "open": c, "close": c, "high": c, "low": c, "volume": 1}
            for k, c in enumerate(closes)]


def test_funding_extremes_both_ways_and_mock_ignored():
    hot = fa.coin_reads("TAO", {"funding_rate": {"current_8h": 0.08, "source": "okx"}}, [])
    assert hot[0]["kind"] == "funding_extreme" and hot[0]["direction"] == "bearish"
    assert "longs crowded" in hot[0]["label"] and "not a reliable turn signal" in hot[0]["label"]
    cold = fa.coin_reads("TAO", {"funding_rate": {"current_8h": -0.05, "source": "okx"}}, [])
    assert cold[0]["direction"] == "bullish" and "shorts crowded" in cold[0]["label"]
    assert fa.coin_reads("TAO", {"funding_rate": {"current_8h": 0.2, "source": "mock"}}, []) == []
    assert fa.coin_reads("TAO", {"funding_rate": {"current_8h": 0.01, "source": "okx"}}, []) == []
    hist = [{"funding_8h": 0.01 + 0.001 * (k % 3)} for k in range(25)]
    z = fa.coin_reads("TAO", {"funding_rate": {"current_8h": 0.08, "source": "okx"}}, hist)
    assert "sd vs its last 25 days" in z[0]["label"]


def test_oi_flush_and_surge():
    flush = fa.coin_reads("SOL", {"open_interest": {"change_pct": -20, "window_bars": 5, "source": "okx"},
                                  "candles": candles([100, 98, 96, 94, 92, 90])}, [])
    assert flush[0]["kind"] == "oi_flush" and flush[0]["direction"] == "bullish"
    no_drop = fa.coin_reads("SOL", {"open_interest": {"change_pct": -20, "window_bars": 5, "source": "okx"},
                                    "candles": candles([100] * 6)}, [])
    assert no_drop == []                                   # OI down but price flat: not a flush
    surge = fa.coin_reads("SOL", {"open_interest": {"change_pct": 30, "window_bars": 5, "source": "okx"},
                                  "candles": candles([100, 101, 102, 103, 104, 105])}, [])
    assert surge[0]["kind"] == "oi_surge" and "+5%" in surge[0]["label"]


def test_liquidation_cascade_needs_history_size_and_3x():
    hist = [{"liq_longs_usd": 1_000_000.0, "liq_shorts_usd": 500_000.0} for _ in range(10)]
    a = {"liquidations": {"longs_liquidated": 4_000_000.0, "shorts_liquidated": 600_000.0, "source": "okx"}}
    got = fa.coin_reads("BTC", a, hist)
    assert [g["kind"] for g in got] == ["liquidation_cascade"] and got[0]["direction"] == "bullish"
    assert "4.0× its normal" in got[0]["label"]
    assert fa.coin_reads("BTC", a, hist[:3]) == []         # too little history
    small = {"liquidations": {"longs_liquidated": 900_000.0, "source": "okx"}}
    assert fa.coin_reads("BTC", small, [{"liq_longs_usd": 100.0}] * 10) == []   # under $1M


def test_etf_streak_and_break():
    out = [{"ts": k, "m": -100.0} for k in range(6)]
    s = fa.etf_reads("BTC", {"recent_days": out})
    assert s[0]["kind"] == "etf_streak" and "6 outflow days" in s[0]["label"]
    brk = fa.etf_reads("BTC", {"recent_days": out + [{"ts": 9, "m": 250.0}]})
    assert brk[0]["kind"] == "etf_streak_break" and "after 6 outflow days" in brk[0]["label"]
    assert fa.etf_reads("BTC", {"recent_days": [{"ts": 1, "m": 5}, {"ts": 2, "m": -5}]}) == []
    assert fa.etf_reads("BTC", None) == []


def test_coinbase_premium():
    p = fa.premium_read(65_100.0, 65_000.0)
    assert p and p[0]["value"] > 0.1 and "paying more" in p[0]["label"]
    assert fa.premium_read(65_010.0, 65_000.0) == [] and fa.premium_read(None, 1.0) == []


def test_detect_and_notes():
    hist_day = {"date": "2026-11-16", "coins": {"BTC": {"liq_longs_usd": 1e6}}}
    reads = fa.detect({"BTC": {"funding_rate": {"current_8h": 0.06, "source": "okx"}}}, [hist_day] * 8,
                      {"BTC": {"recent_days": [{"ts": k, "m": -50.0} for k in range(5)]}}, 65_200, 65_000)
    kinds = {r["kind"] for r in reads}
    assert kinds == {"funding_extreme", "etf_streak", "coinbase_premium"}
    lines = fa.notes(reads, "yesterday's close")
    assert lines[0].startswith("🧯 Flow anomalies (yesterday's close)") and len(lines) == 4
    assert lines[1].startswith("   🏦")                    # ETF first
    assert fa.notes([]) == []


def test_recorded_at_the_close_and_shown_in_the_update(monkeypatch):
    import app as app_mod
    import kv
    store = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
    monkeypatch.setattr(kv, "get_value", lambda k: store.get(k))
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reads = app_mod._flowanom_record(day, {"TAO": {"funding_rate": {"current_8h": 0.09, "source": "okx"}}},
                                     history=[], etfs={}, prices=(None, None))
    assert reads and json.loads(store[app_mod._flowanom_key(day)])["reads"][0]["symbol"] == "TAO"
    lines = app_mod._flow_anomaly_notes()
    assert lines[0].startswith("🧯 Flow anomalies") and "TAO funding" in lines[1]
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py")).read()
    assert "_flowanom_record(close_publish.close_key(now)" in src
    assert "notes=_mega_ipo_notes() + _flow_anomaly_notes()" in src
