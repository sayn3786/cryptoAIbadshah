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


def _cvd(trend, real=True):
    return {"trend": trend, "usd": True, "unit": "usd"} if real else {"trend": trend, "unit": "base"}


def test_short_squeeze_setup_and_its_mirror():
    flat = candles([100, 100.5, 99.8, 100.2, 100.4, 101])
    a = {"spot_cvd": _cvd("bullish"), "futures_cvd": _cvd("bearish"), "candles": flat}
    got = fa.coin_reads("BTC", a, [])
    assert got[0]["kind"] == "short_squeeze_setup" and got[0]["direction"] == "bullish"
    mirror = fa.coin_reads("BTC", {**a, "spot_cvd": _cvd("bearish"), "futures_cvd": _cvd("bullish")}, [])
    assert mirror[0]["kind"] == "long_squeeze_setup" and mirror[0]["direction"] == "bearish"


def test_squeeze_needs_real_cvd_a_flat_price_and_is_new():
    flat = candles([100, 100.5, 99.8, 100.2, 100.4, 101])
    a = {"spot_cvd": _cvd("bullish"), "futures_cvd": _cvd("bearish"), "candles": flat}
    assert fa.coin_reads("BTC", {**a, "spot_cvd": _cvd("bullish", real=False)}, []) == []
    moving = {**a, "candles": candles([100, 101, 102, 103, 104, 106])}
    assert fa.coin_reads("BTC", moving, []) == []                     # price already moved 6%
    seen = [{"spot_cvd": "bullish", "futures_cvd": "bearish"}]
    assert fa.coin_reads("BTC", a, seen) == []                        # already there yesterday
    assert fa.coin_reads("BTC", a, [{"spot_cvd": "neutral", "futures_cvd": "bearish"}])


# ── OI divergence (Nov 21 2026): price down big while OI in coins rises ────
def _div_analysis(px0, px1, oi_usd0, oi_usd1, funding, days=29):
    D = 86_400_000
    t0 = 1_790_000_000_000 // D * D
    candles = [{"timestamp": t0 + i * D, "open": 1, "high": 1, "low": 1,
                "close": px0 + (px1 - px0) * i / days} for i in range(days + 1)]
    hist = [{"timestamp": t0 + i * D, "oi": oi_usd0 + (oi_usd1 - oi_usd0) * i / days}
            for i in range(days + 1)]
    return {"candles": candles,
            "open_interest": {"source": "okx", "change_pct": 0, "window_bars": 5, "history": hist},
            "funding_rate": None if funding is None else {"source": "okx", "current_8h": funding}}


def test_oi_divergence_drop_with_negative_funding_is_squeeze_risk():
    import flow_anomalies as fa
    # price 92k -> 58k (-37%); OI in USD 20B -> 14.4B = coins 217k -> 248k (+14%)
    a = _div_analysis(92_000, 58_000, 20e9, 14.4e9, -0.01)
    r = [x for x in fa.coin_reads("BTC", a, []) if x["kind"] == "oi_divergence"][0]
    assert r["direction"] == "bullish" and r["price_pct"] < -35 and r["value"] > 10
    assert "crowded shorts" in r["label"] and "not yet tested" in r["label"]


def test_oi_divergence_drop_with_positive_funding_is_flush_risk():
    import flow_anomalies as fa
    r = fa._oi_divergence(_div_analysis(92_000, 58_000, 20e9, 14.4e9, 0.01))
    assert r["direction"] == "bearish" and "longs averaging down" in r["label"]


def test_oi_divergence_needs_size_and_coins_not_usd():
    import flow_anomalies as fa
    assert fa._oi_divergence(_div_analysis(92_000, 85_000, 20e9, 20e9, -0.01)) is None   # -8% only
    # OI in USD fell with price, coins flat: no divergence
    assert fa._oi_divergence(_div_analysis(92_000, 58_000, 20e9, 12.6e9, -0.01)) is None
    assert fa._oi_divergence(_div_analysis(92_000, 58_000, 20e9, 14.4e9, -0.01, days=10)) is None
    m = fa._oi_divergence({**_div_analysis(92_000, 58_000, 20e9, 14.4e9, -0.01),
                           "open_interest": {"source": "mock", "history": []}})
    assert m is None


def test_oi_divergence_rally_mirror():
    import flow_anomalies as fa
    r = fa._oi_divergence(_div_analysis(50_000, 60_000, 10e9, 14e9, 0.02))
    assert r["direction"] == "bearish" and "long-squeeze" in r["label"]
