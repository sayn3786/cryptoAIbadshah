"""
Close trades: the 1D top five every day at 00:00 UTC (8 AM SGT), plus the
business week (Mon-Fri, closing Saturday) and the crypto week (1W, closing
Monday), one grouped post a day, replacing the 4H publication.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import close_publish as cp                                           # noqa: E402

DAY = 86_400_000
MON = datetime(2026, 10, 26, tzinfo=timezone.utc)          # a Monday
SAT = datetime(2026, 10, 24, tzinfo=timezone.utc)
WED = datetime(2026, 10, 21, tzinfo=timezone.utc)


def test_which_closes_are_due():
    assert cp.due(MON + timedelta(minutes=5)) == ["1D", "1W"]
    assert cp.due(SAT + timedelta(minutes=5)) == ["1D", "BW"]
    assert cp.due(WED + timedelta(hours=2)) == ["1D"]
    assert cp.due(datetime(2026, 10, 25, 0, 3, tzinfo=timezone.utc)) == ["1D"]   # Sunday
    assert cp.close_key(MON + timedelta(hours=1)) == "2026-10-26"
    assert cp.expected_close_ms("1W", MON + timedelta(minutes=3)) == int(MON.timestamp() * 1000)


def _daily(start: datetime, days: int, base=100.0):
    out = []
    for i in range(days):
        ts = int((start + timedelta(days=i)).timestamp() * 1000)
        p = base + i
        out.append({"timestamp": ts, "open": p, "high": p + 2, "low": p - 1,
                    "close": p + 1, "volume": 10.0, "taker_buy_volume": 4.0})
    return out


def test_business_week_folds_monday_to_friday_and_skips_the_weekend():
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)       # Monday
    weeks = cp.business_weeks(_daily(start, 14))              # two full calendar weeks
    assert [w["timestamp"] for w in weeks] == [
        int(start.timestamp() * 1000), int((start + timedelta(days=7)).timestamp() * 1000)]
    w = weeks[0]                                              # Mon 100 .. Fri 104
    assert (w["open"], w["close"], w["high"], w["low"]) == (100, 105, 106, 99)
    assert w["volume"] == 50.0 and w["taker_buy_volume"] == 20.0     # five days, no weekend


def test_a_gappy_past_week_is_dropped_but_the_current_week_may_be_partial():
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    days = _daily(start, 17)                                  # through Wed of week 3
    del days[2]                                               # week 1 loses its Wednesday
    weeks = cp.business_weeks(days)
    assert len(weeks) == 2                                    # week 2, and the forming week 3
    assert weeks[-1]["timestamp"] == int((start + timedelta(days=14)).timestamp() * 1000)


def read(direction="LONG", strength=70, entry=100.0, sl=95.0, tps=(110.0, 115.0, 120.0),
         live=100.5, tradeable=True, quality="good"):
    sig = {"direction": direction, "strength": strength, "entry": entry, "sl": sl,
           "tp_targets": list(tps), "rr_ratio": round(abs(tps[1] - entry) / abs(entry - sl), 4),
           "current_price": entry, "structure_factors": []}
    return {"direction": direction, "strength": strength, "sig": sig,
            "signal_price": entry, "live_price": live, "tradeable": tradeable,
            "data_quality": quality}


NO_BTC = {"direction": "NEUTRAL", "scale": 0.0}


def test_screen_reads_the_close_and_nudges_by_the_higher_timeframe():
    up = cp.screen("SOL", read(), read(), tf="1D", corr_factor=1, influence=NO_BTC)
    down = cp.screen("SOL", read(), read("SHORT"), tf="1D", corr_factor=1, influence=NO_BTC)
    flat = cp.screen("SOL", read(), None, tf="1D", corr_factor=1, influence=NO_BTC)
    assert up["ok"] and (up["strength"], up["htf_adj"]) == (75, 5)
    assert down["ok"] and down["strength"] == 65
    assert flat["ok"] and flat["strength"] == 70 and flat["htf_dir"] == "NEUTRAL"


def test_screen_gates():
    def why(r, h=None):
        return cp.screen("SOL", r, h, tf="1D", corr_factor=1, influence=NO_BTC)["reason"]
    assert why(None) == "MISSING_TIMEFRAME"
    assert why(read(direction="NEUTRAL")) == "NEUTRAL"
    assert why(read(tradeable=False)) == "NOT_TRADEABLE"
    assert why(read(strength=53), read("SHORT")) == "MIN_STRENGTH"      # 53 - 5 < 51
    assert why(read(tps=(103.0, 106.0, 109.0))) == "LOW_RR"
    assert why(read(live=111.0)) == "TP1_BEHIND_LIVE"


def rec(sym, d="LONG", s=70.0, tf="1D", corr=0.5):
    return {"symbol": sym, "direction": d, "strength": s, "rr_ratio": 2.0, "timeframe": tf,
            "btc_corr": corr, "entry": 100, "sl": 95, "tp_targets": [110, 115]}


def test_select_keeps_the_best_five():
    picks = cp.select([rec(f"C{i}", s=60 + i) for i in range(8)])
    assert [p["symbol"] for p in picks] == ["C7", "C6", "C5", "C4", "C3"]


def test_a_coin_two_closes_pick_the_same_way_is_grouped_first():
    sets = {"1D": [rec("ETH", s=72), rec("SOL", s=80), rec("XRP", "SHORT")],
            "1W": [rec("SOL", s=66, tf="1W"), rec("XRP", "LONG", tf="1W")]}
    g = cp.group(sets)
    assert (g[0]["symbol"], g[0]["tfs"]) == ("SOL", ["1D", "1W"])
    assert [(e["symbol"], e["direction"]) for e in g[1:]] == [
        ("ETH", "LONG"), ("XRP", "SHORT"), ("XRP", "LONG")]       # opposite ways stay apart
    text = cp.build_message(sets, day=MON)
    assert "SOL LONG [1D + 1W]" in text and "closes agree" in text
    assert "Daily (1D) + Crypto week (1W)" in text and "Mon 26 Oct, 8:00 AM SGT" in text
    assert "Picks: 1D 3/5, 1W 2/5" in text


def test_an_empty_close_still_says_so():
    text = cp.build_message({"1D": [], "BW": []}, day=SAT)
    assert "No setup cleared the bar" in text and "Business week (Mon-Fri)" in text


# ── the app side ─────────────────────────────────────────────────────────────

@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    import kv
    mem = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "get_value", lambda k: mem.get(k))
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=0: (k not in mem)
                        and (mem.__setitem__(k, "1") or True))
    monkeypatch.setattr(kv, "release", lambda k: mem.pop(k, None))
    monkeypatch.setattr(app, "_kv_get", lambda k: mem.get(k))
    monkeypatch.setattr(app, "_kv_set", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(app, "_post_twitter_signals", lambda *a: True)
    monkeypatch.setattr(app, "get_analysis", lambda *a: {})
    app._mem = mem
    return app


def test_monday_scans_one_close_per_call_then_posts_once(app_mod):
    scans, posts = [], []

    def compute(tf, now):
        scans.append(tf)
        return {"ready": True, "recs": [rec("SOL", tf=tf)], "rejected": {}, "candidates": 3}

    def send(sets, now):
        posts.append(sets)
        return True

    at = MON + timedelta(minutes=3)
    first = app_mod._close_run(at, compute=compute, send=send)
    assert first["computed"]["tf"] == "1D" and first["next"] == "1W" and not posts
    second = app_mod._close_run(at, compute=compute, send=send)
    assert second["telegram"] == "sent" and scans == ["1D", "1W"]
    assert set(posts[0]) == {"1D", "1W"} and second["picks"]["1W"] == ["SOL LONG"]
    third = app_mod._close_run(at, compute=compute, send=send)
    assert third["telegram"].startswith("skipped") and len(posts) == 1 and scans == ["1D", "1W"]


def test_waits_while_the_exchanges_still_serve_the_old_candle(app_mod):
    out = app_mod._close_run(WED + timedelta(minutes=3),
                             compute=lambda tf, now: {"ready": False, "current": 4, "of": 30},
                             send=lambda *a: pytest.fail("nothing to send yet"))
    assert out["reason"] == "DATA_NOT_CURRENT" and out["ok"]


def test_a_missed_close_is_not_published_late(app_mod):
    out = app_mod._close_run(WED + timedelta(hours=5),
                             compute=lambda *a: pytest.fail("too late"), send=lambda *a: True)
    assert out == {"ok": True, "ran": False, "reason": "PAST_CLOSE_WINDOW"}


def _analysis(direction, strength, closed_ms, entry=100.0):
    sig = {"direction": direction, "strength": strength, "entry": entry,
           "sl": entry * 0.95 if direction == "LONG" else entry * 1.05,
           "tp_targets": ([entry * 1.1, entry * 1.15] if direction == "LONG"
                          else [entry * 0.9, entry * 0.85]),
           "current_price": entry, "structure_factors": []}
    sig["rr_ratio"] = 3.0                  # TP2 15% away over a 5% stop
    return {"signal": sig, "live_price": entry, "signal_price": entry,
            "data_quality": "good", "tradeable": True, "signal_candle_closed_at": closed_ms}


def test_compute_picks_from_current_candles_and_records_them(app_mod, monkeypatch):
    import signal_publish
    now = SAT + timedelta(minutes=4)
    close = int(SAT.timestamp() * 1000)
    strength = {s: 55 + i for i, s in enumerate(app_mod.SCAN_SYMBOLS)}
    strength["ETH"] = 99

    def build(sym, tf):
        assert tf == "BW"
        if sym == "XRP":
            return _analysis("LONG", 99, close - 5 * DAY)     # still last week's candle
        return _analysis("LONG", strength[sym], close)

    recorded = []

    def persist(r, analysis, **kw):
        recorded.append((r["symbol"], r["timeframe"], kw["strategy_name"], kw["strategy_version"]))
        return signal_publish.PersistResult(symbol=r["symbol"], ok=True, actionable=True,
                                            signal_id="id-" + r["symbol"])

    monkeypatch.setattr(signal_publish, "persist_recommendation", persist)
    monkeypatch.setattr(app_mod, "get_btc_mining_signals", lambda: {})
    out = app_mod._close_compute("BW", now, build=build,
                                 higher=lambda sym, tf: _analysis("LONG", 60, close))
    assert out["ready"] and out["rejected"]["XRP"] == "NOT_CURRENT"
    picks = [r["symbol"] for r in out["recs"]]
    assert len(picks) == 5 and picks[0] == "ETH" and "XRP" not in picks
    assert all(r[1:] == ("BW", "close_top5", "v60_close_top5") for r in recorded)
    assert out["recs"][0]["signal_id"] == "id-ETH"


def test_compute_waits_when_most_coins_are_still_on_the_old_candle(app_mod):
    old = int(SAT.timestamp() * 1000) - DAY
    out = app_mod._close_compute("1D", SAT + timedelta(minutes=3),
                                 build=lambda sym, tf: _analysis("LONG", 70, old),
                                 higher=lambda *a: pytest.fail("not ready"))
    assert out["ready"] is False


def test_cron_endpoint_is_scheduler_only(app_mod):
    c = app_mod.app.test_client()
    assert c.post("/api/cron/close").status_code == 401
    assert "/api/cron/close" in app_mod._SCHEDULER_PATHS


def test_the_monitor_gives_close_trades_their_own_clock(monkeypatch):
    import signal_monitor as sm
    seen = {}

    class Store:
        def list_active_signals(self, **kw):
            return [{"id": 1, "symbol": "SOL", "timeframe": "1W", "status": "OPEN"},
                    {"id": 2, "symbol": "ETH", "timeframe": "2H", "status": "OPEN"}]

        def attach_targets(self, rows):
            return rows

    def fake(sig, targets, candles, **kw):
        seen[sig["timeframe"]] = (kw["fill_window_hours"], kw["max_age_hours"])
        return []

    monkeypatch.setattr(sm, "evaluate", fake)
    monkeypatch.setattr(sm, "_record_excursion", lambda *a, **k: False)
    sm.run_monitor(Store(), lambda s, tf: [{"timestamp": 0, "high": 1, "low": 1}],
                   windows=cp.WINDOWS)
    assert seen["1W"] == (72, 28 * 24)
    assert seen["2H"] == (sm.DEFAULT_FILL_WINDOW_HOURS, sm.DEFAULT_MAX_AGE_HOURS)
    assert cp.MONITOR_TF == {"1D": "1H", "BW": "4H", "1W": "4H"}
