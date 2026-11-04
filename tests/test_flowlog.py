"""The forward log of non-candle data at each 1D close (flowlog)."""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import flowlog as fl                                                   # noqa: E402


def analysis(**over):
    a = {"signal_price": 100.5,
         "funding_rate": {"current": 0.01, "current_8h": 0.01, "average": 0.008,
                          "history": [{"timestamp": 1, "rate": 0.01}] * 10, "source": "coinglass"},
         "open_interest": {"value": 1.2e9, "change_pct": -8.5, "quadrant": "longs_closing",
                           "squeeze": None, "history": [1] * 30, "source": "okx"},
         "liquidations": {"longs_liquidated": 2.5e6, "shorts_liquidated": 4e5,
                          "total": 2.9e6, "recent": [1] * 20, "source": "binance"},
         "long_short": {"ratio": 1.8, "long_pct": 64.3},
         "futures_cvd": {"trend": "bearish"}, "spot_cvd": {"trend": "bullish"},
         "cvd_divergence": {"type": "spot_dominated_up"}}
    a.update(over)
    return a


def test_snapshot_keeps_the_readings_not_the_histories():
    s = fl.snapshot(analysis())
    assert s["funding_8h"] == 0.01 and s["oi_change_pct"] == -8.5 and s["liq_longs_usd"] == 2.5e6
    assert s["oi_quadrant"] == "longs_closing" and s["long_pct"] == 64.3
    assert s["futures_cvd"] == "bearish" and s["cvd_divergence"] == "spot_dominated_up"
    assert s["sources"] == {"funding": "coinglass", "oi": "okx", "liq": "binance"}
    assert len(json.dumps(s)) < 800                      # small: no history arrays


def test_mock_data_is_never_logged_as_a_reading():
    s = fl.snapshot(analysis(funding_rate={"current_8h": 0.03, "average": 0.02, "source": "mock"},
                             liquidations={"longs_liquidated": 9e9, "source": "mock"}))
    assert s["funding_8h"] is None and s["funding_avg"] is None and s["liq_longs_usd"] is None
    assert s["sources"]["funding"] == "mock" and s["oi_usd"] == 1.2e9
    empty = fl.snapshot({})
    assert empty["funding_8h"] is None and empty["oi_usd"] is None and empty["futures_cvd"] is None


def test_binance_marks_its_mock_fallbacks():
    import binance
    cls = next((getattr(binance, n) for n in dir(binance)
                if isinstance(getattr(binance, n), type)
                and hasattr(getattr(binance, n), "get_funding_rate")
                and hasattr(getattr(binance, n), "get_liquidations")), None)
    assert cls is not None
    c = cls.__new__(cls)
    c._get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    assert c.get_funding_rate("ETHUSDT")["source"] == "mock"
    assert c.get_liquidations("ETHUSDT")["source"] == "mock"


def test_day_record_and_summary():
    rec = fl.day_record("2026-11-04", 1_793_750_400_000, {"ETH": analysis(), "BTC": analysis(
        funding_rate={"source": "mock"})})
    assert rec["date"] == "2026-11-04" and list(rec["coins"]) == ["BTC", "ETH"]
    s = fl.summary([rec, {"date": "2026-11-05", "coins": {"ETH": fl.snapshot(analysis())}}, {}])
    assert s["days_logged"] == 2 and s["first"] == "2026-11-04" and s["last"] == "2026-11-05"
    assert s["coins_per_day"] == 1.5 and s["real_pct"]["funding"] == pytest.approx(66.7)
    assert fl.summary([]) == {"days_logged": 0}


@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    import kv
    mem = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "get_value", lambda k: mem.get(k))
    monkeypatch.setenv("CRON_SECRET", "cron-test")
    app._mem = mem
    return app


def test_record_and_read_back_through_the_endpoint(app_mod):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert app_mod._flowlog_record(today, 1, {"ETH": analysis()})
    c = app_mod.app.test_client()
    assert c.get("/api/flowlog").status_code == 401                  # protected
    body = c.get("/api/flowlog?days=3", headers={"x-cron-secret": "cron-test"}).get_json()
    assert body["summary"]["days_logged"] == 1 and body["days"][0]["coins"]["ETH"]["oi_usd"] == 1.2e9
    only = c.get("/api/flowlog?days=3&summary=1", headers={"x-cron-secret": "cron-test"}).get_json()
    assert "days" not in only and only["summary"]["days_logged"] == 1
    assert c.get("/api/flowlog?until=bad", headers={"x-cron-secret": "cron-test"}).status_code == 400


def test_record_never_raises(app_mod, monkeypatch):
    import kv
    monkeypatch.setattr(kv, "set_value", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert app_mod._flowlog_record("2026-11-04", 1, {"ETH": analysis()}) is False


def test_the_daily_close_logs_once_ready(app_mod, monkeypatch):
    import close_publish
    calls = []
    monkeypatch.setattr(app_mod, "_flowlog_record", lambda day, ms, an: calls.append((day, ms, sorted(an))))
    now = datetime(2026, 11, 4, 0, 10, tzinfo=timezone.utc)
    expected = close_publish.expected_close_ms("1D", now)

    def build(sym, tf):
        return {"signal": {"direction": "NEUTRAL", "strength": 0},
                "signal_candle_closed_at": expected, "live_price": 1, "signal_price": 1}
    monkeypatch.setattr(app_mod, "SCAN_SYMBOLS", ("BTC", "ETH"))
    app_mod._close_compute("1D", now, build=build, higher=build)
    assert calls == [("2026-11-04", expected, ["BTC", "ETH"])]
    calls.clear()
    app_mod._close_compute("1W", now, build=build, higher=build)       # weekly: not logged
    assert calls == []


def test_the_monday_report_shows_the_log():
    import close_record as cr
    assert "nothing logged yet" in cr.flowlog_line({"days_logged": 0})
    line = cr.flowlog_line({"days_logged": 12, "first": "2026-11-04", "coins_per_day": 29.0,
                            "real_pct": {"funding": 100.0, "oi": 96.6, "liquidations": 89.7}})
    assert "12 days since 2026-11-04" in line and "OI 96.6%" in line
    assert "not reachable" in cr.flowlog_line(None)
