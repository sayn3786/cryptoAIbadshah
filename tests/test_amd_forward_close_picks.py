"""The AMD forward test (alert-only, paper) and the close-picks replay study."""
import json
import os
import random
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import amd_live as al                                                  # noqa: E402
import close_picks_compare as cpc                                      # noqa: E402
import close_publish as cp                                             # noqa: E402

M = al.M15
H = al.HOUR_MS
DAY = al.DAY_MS


def bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def amd_bars(day=20000, sweep_at=4, after=None):
    """Asia 100-101, then London; the candle `sweep_at` after 06:00 wicks to
    101.5 and closes 100.8 (a SHORT). `after` = later candles' (h, l, c)."""
    t0 = day * DAY
    bars = [bar(t0 + i * M, 100.5, 101.0 if i == 3 else 100.8, 100.0 if i == 9 else 100.2, 100.5)
            for i in range(24)]
    for k in range(sweep_at):
        bars.append(bar(t0 + (24 + k) * M, 100.5, 100.7, 100.3, 100.5))
    bars.append(bar(t0 + (24 + sweep_at) * M, 100.5, 101.5, 100.6, 100.8))
    for k, (h, lo, c) in enumerate(after or []):
        bars.append(bar(t0 + (25 + sweep_at + k) * M, 100.8, h, lo, c))
    return bars


# ── AMD forward test: the detector on the latest close, paper levels ────────

def test_latest_finds_the_setup_only_on_the_closed_candle():
    bars = amd_bars()
    close = bars[-1]["timestamp"] + M
    s = al.latest("ETH", bars, close + 60_000)
    assert s and s["direction"] == "SHORT" and s["slot_ms"] == close and s["state"] == "open"
    assert abs(s["paper_sl"] - 100.8 * (1 + 0.10 / 11)) < 1e-9          # -10% of margin
    assert abs(s["paper_tp"] - 100.8 * (1 - 0.15 / 11)) < 1e-9          # +15% of margin
    assert al.latest("ETH", bars, close + M + 1) is None                # a candle late
    later = amd_bars(after=[(100.9, 100.6, 100.7)])
    assert al.latest("ETH", later, later[-1]["timestamp"] + M + 1) is None   # an earlier setup
    assert "alert only, no order" in al.alert(s) and "London swept the high" in al.alert(s)


def test_the_detector_is_the_backtests():
    import scalp_amd_compare as amd
    assert amd.setups is al.setups and amd.manipulation is al.manipulation
    assert set(al.COINS) == set(__import__("scalp_chop_compare").COINS)


def _trade(**kw):
    s = al.latest("ETH", amd_bars(), amd_bars()[-1]["timestamp"] + M + 1)
    return {**s, **kw}


def test_paper_settle_tp_stop_timeout_and_void():
    t = _trade()
    base = amd_bars()
    nxt = base[-1]["timestamp"] + M
    tp = base + [bar(nxt, 100.8, 100.9, 99.3, 99.4)]
    r = al.settle(t, tp)
    assert r["exit_reason"] == "tp" and r["fill"] == 100.8 and r["pnl_usd"] > 0
    assert abs(r["pnl_usd"] - round(550 * ((100.8 - t["paper_tp"]) / 100.8 - 8 / 10_000), 2)) < 0.01
    both = base + [bar(nxt, 100.8, 101.9, 99.3, 100.0)]
    assert al.settle(t, both)["exit_reason"] == "stop"                  # stop first
    assert al.settle(t, both)["pnl_usd"] < -5
    flat = base + [bar(nxt + k * M, 100.8, 100.9, 100.7, 100.8) for k in range(17)]
    assert al.settle(t, flat)["exit_reason"] == "timeout"
    assert al.settle(t, base + [bar(nxt, 100.8, 100.9, 100.7, 100.8)]) is None   # still running
    gone = [bar(nxt + 5 * M, 100.8, 100.9, 100.7, 100.8)]
    assert al.settle(t, gone)["state"] == "void"
    s = al.summary([r, al.settle(t, both), {"state": "open"}, {"state": "void"}])
    assert s["closed"] == 2 and s["wins"] == 1 and s["open"] == 1 and s["win_rate_pct"] == 50.0


def test_switch(monkeypatch):
    assert al.enabled()
    monkeypatch.setenv("AMD_FORWARD", "0")
    assert not al.enabled()


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
    sent = []
    monkeypatch.setattr(app, "_scalp_private", lambda t: sent.append(t))
    import hl_exchange

    def _no_orders(*a, **k):
        raise AssertionError("the AMD forward test must never place an order")
    for name in ("send_market_open", "send_stop", "send_take_profit", "cancel_order"):
        monkeypatch.setattr(hl_exchange, name, _no_orders, raising=False)
    monkeypatch.delenv("AMD_FORWARD", raising=False)
    app._sent, app._mem = sent, mem
    return app


def test_amd_run_alerts_once_books_paper_and_settles(app_mod):
    base = amd_bars()
    close = base[-1]["timestamp"] + M
    data = {"ETH": base}
    fetch = lambda sym: data.get(sym, [])                                # noqa: E731
    out = app_mod._amd_run(close + 60_000, fetch_fn=fetch)
    assert out["ran"] and out["new"] == 1 and out["open"] == 1
    assert len(app_mod._sent) == 1 and "AMD forward test" in app_mod._sent[0]
    again = app_mod._amd_run(close + 120_000, fetch_fn=fetch)
    assert again["new"] == 0 and len(app_mod._sent) == 1                 # alerted once
    data["ETH"] = base + [bar(close, 100.8, 100.9, 99.3, 99.4)]          # the target
    out = app_mod._amd_run(close + M + 60_000, fetch_fn=fetch)
    assert out["closed"] == 1 and out["wins"] == 1 and out["pnl_usd"] > 0
    assert "AMD paper: ETH SHORT closed at target" in app_mod._sent[-1]
    stored = json.loads(app_mod._mem[app_mod._amd_key()])
    assert stored[0]["state"] == "closed" and stored[0]["exit_reason"] == "tp"


def test_amd_run_is_off_and_never_raises(app_mod, monkeypatch):
    monkeypatch.setenv("AMD_FORWARD", "0")
    assert app_mod._amd_run(0, fetch_fn=lambda s: [])["reason"] == "AMD_OFF"
    monkeypatch.delenv("AMD_FORWARD")

    def boom(sym):
        raise RuntimeError("down")
    out = app_mod._amd_run(10 ** 12, fetch_fn=boom)
    assert out["ran"] and out["new"] == 0                                # no data: quiet


def test_scalp_api_shows_the_forward_test(app_mod, monkeypatch):
    import scalp_store
    monkeypatch.setattr(scalp_store, "recent", lambda env, days=14: [])
    monkeypatch.setenv("HL_ADMIN_TOKEN", "hl-test-token")
    base = amd_bars()
    app_mod._amd_run(base[-1]["timestamp"] + M + 1, fetch_fn=lambda s: base if s == "ETH" else [])
    body = app_mod.app.test_client().get(
        "/api/hl/scalp", headers={"x-hl-token": "hl-test-token"}).get_json()
    f = body["amd_forward"]
    assert f["alert_only"] and f["summary"]["open"] == 1 and f["recent"][0]["symbol"] == "ETH"


def test_cron_runs_both(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_scalp_run", lambda: {"ok": True})
    monkeypatch.setattr(app_mod, "_amd_run", lambda: {"ran": True, "new": 0})
    monkeypatch.setattr(app_mod, "_cron_authorized", lambda: True)
    body = app_mod.app.test_client().post("/api/cron/scalp").get_json()
    assert body["ok"] and body["amd"]["ran"]


# ── the close-picks replay ──────────────────────────────────────────────────

def daily(n=400, seed=1, start=100.0, d0=20000):
    random.seed(seed)
    p, out = start, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0, 0.03)
        out.append(bar((d0 + i) * DAY, o, max(o, p) * 1.01, min(o, p) * 0.99, p))
    return out


def test_candles_never_include_an_unclosed_period():
    d = daily(120)
    close = d[100]["timestamp"]                       # 00:00 of day 100: day 99 just closed
    assert cpc.candles_for("1D", d, close)[-1]["timestamp"] == d[99]["timestamp"]
    for w in cpc.weeks_closed(d, close):
        assert w["timestamp"] + 7 * DAY <= close
    for w in cpc.business_closed(d, close):
        assert w["timestamp"] + 5 * DAY <= close
    for m in cpc.months_closed(d, close):
        mo = datetime.fromtimestamp(m["timestamp"] / 1000, timezone.utc)
        nxt = datetime(mo.year + (mo.month == 12), mo.month % 12 + 1, 1, tzinfo=timezone.utc)
        assert nxt.timestamp() * 1000 <= close


def test_closes_follow_the_live_schedule():
    sat = int(datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp() * 1000)
    got = cpc.closes(sat, sat + 2 * DAY)
    assert got == [("1D", sat), ("BW", sat), ("1D", sat + DAY), ("1D", sat + 2 * DAY),
                   ("1W", sat + 2 * DAY)]


def _rec(direction="LONG", **kw):
    return {"symbol": "ETH", "timeframe": "1D", "direction": direction, "entry": 100.0,
            "sl": 98.0, "tp_targets": [102.0, 104.0], "close_ms": 0, "signal_price": 100.0,
            **kw}


def h4(path, t0=0):
    """4H candles from (high, low, close) tuples."""
    return [bar(t0 + k * cpc.H4_MS, 100, h, lo, c) for k, (h, lo, c) in enumerate(path)]


FLAT_REST = [(100.5, 99.5, 100)] * 50


def test_settle_uses_the_trackers_rules():
    # TP1 then back to entry: half at +2%, half at breakeven (stop moved).
    r = cpc.settle(_rec(), h4([(100.2, 99.9, 100), (102.1, 100.5, 101.5), (101, 99.9, 100)]
                              + FLAT_REST))
    assert r["outcome"] == "tp1+stop" and abs(r["r"] - (0.5 * 0.02 - (1.5 + 0.75 + 3.25) / 1e4) / 0.02) < 1e-3
    stop = cpc.settle(_rec(), h4([(100.2, 99.9, 100), (100.5, 97.9, 98)] + FLAT_REST))
    assert stop["outcome"] == "stop" and stop["r"] < -1
    both = cpc.settle(_rec(), h4([(100.2, 99.9, 100), (104.5, 97.5, 100)] + FLAT_REST))
    assert both["outcome"] == "stop"                                     # pessimistic
    never = cpc.settle(_rec(entry=95.0), h4([(101, 99, 100)] * 60))
    assert never is None                                                 # not filled in 24h
    short = cpc.settle(_rec(), h4([(100.2, 99.9, 100)]))
    assert short is None                                                 # history too short


def test_mirror_flips_every_level_around_the_close():
    flips = [cpc.mirror(_rec(), random.Random(k)) for k in range(20)]
    shorts = [f for f in flips if f["direction"] == "SHORT"]
    assert shorts and all(f["entry"] == 100 and f["sl"] == 102 and f["tp_targets"] == [98, 96]
                          for f in shorts)


def test_compare_and_render_with_a_fake_engine():
    syms = ("BTC", "ETH", "SOL")
    d = {s: daily(420, i + 1, start=100 * (i + 1)) for i, s in enumerate(syms)}
    h4s = {s: [bar(c["timestamp"] + k * cpc.H4_MS, c["open"], c["high"], c["low"], c["close"])
               for c in d[s] for k in range(6)] for s in syms}

    def read(win, tf, sym):
        p = float(win[-1]["close"])
        return {"direction": "LONG", "strength": 90, "entry": p, "sl": p * 0.97,
                "tp_targets": [p * 1.03, p * 1.06, p * 1.09], "rr_ratio": 2.0,
                "current_price": p}
    end = d["BTC"][-40]["timestamp"]
    res = cpc.compare(d, h4s, start_ms=end - 60 * DAY, end_ms=end, corr={}, read_fn=read,
                      seeds=3, log=lambda *_: None)
    tops = [r for r in res["rows"] if r["period"] != "all-candidates"]
    assert len(tops) == 3 * cpc.PERIODS and any(r["trades"] for r in tops)
    assert all(r["picks"] <= 5 * 21 for r in tops if r["tf"] == "1D")
    text = cpc.render_telegram(res)
    assert "Close picks replayed" in text and "random direction" in text


def test_workflow_runs_close_picks():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "python -m close_picks_compare --telegram --days 540" in wf
    assert "scalp-amd, close-picks" in wf


# ── the funding layer ───────────────────────────────────────────────────────

def test_parse_funding_csv_by_header_and_by_position():
    with_head = ("calc_time,funding_interval_hours,last_funding_rate\n"
                 "1700000000000,8,0.0001\n1700028800000,8,-0.00025\n")
    assert cpc.parse_funding_csv(with_head) == [(1700000000000, 0.01, 8.0),
                                                (1700028800000, -0.025, 8.0)]
    bare = "1700000000000,4,0.0002\n"
    assert cpc.parse_funding_csv(bare) == [(1700000000000, 0.02, 4.0)]
    odd = "funding_time,funding_rate\n1700000000000,0.0003\n"
    assert cpc.parse_funding_csv(odd) == [(1700000000000, 0.03, 8.0)]
    assert cpc.parse_funding_csv("") == []


def test_funding_at_uses_only_settlements_before_the_close():
    t0 = 20000 * DAY
    series = [(t0 + k * 8 * H, 0.01 * (k + 1), 8.0) for k in range(15)]
    close = t0 + 5 * 8 * H                                  # settlement k=5 is AT the close
    f = cpc.funding_at(series, close)
    assert f["current"] == 0.06 and f["history"][-1]["timestamp"] == close
    assert len(f["history"]) == 6 and f["current_8h"] == 0.06
    assert cpc.funding_at(series, t0 - 1) is None            # nothing yet
    assert cpc.funding_at(series, t0 + 30 * DAY) is None     # stale: no data
    four = [(t0, 0.02, 4.0)]
    assert cpc.funding_at(four, t0)["current_8h"] == 0.04     # normalised to 8h


def test_the_engine_sees_the_closes_funding_and_the_report_says_so():
    syms = ("BTC", "ETH")
    d = {s: daily(420, i + 1, start=100 * (i + 1)) for i, s in enumerate(syms)}
    h4s = {s: [bar(c["timestamp"] + k * cpc.H4_MS, c["open"], c["high"], c["low"], c["close"])
               for c in d[s] for k in range(6)] for s in syms}
    seen = []

    def read(win, tf, sym, extra=None):
        seen.append(extra)
        p = float(win[-1]["close"])
        long = not extra or extra["funding_rate"]["current"] < 0     # funding flips it
        return {"direction": "LONG" if long else "SHORT", "strength": 90, "entry": p,
                "sl": p * (0.97 if long else 1.03), "rr_ratio": 2.0, "current_price": p,
                "tp_targets": [p * (1.03 if long else 0.97), p * (1.06 if long else 0.94)]}
    end = d["BTC"][-40]["timestamp"]
    fund = {"ETH": [(t, 0.05, 8.0) for t in range(end - 90 * DAY, end + DAY, 8 * H)]}
    res = cpc.compare(d, h4s, start_ms=end - 30 * DAY, end_ms=end, corr={}, read_fn=read,
                      seeds=2, funding=fund, log=lambda *_: None)
    assert res["variants"] == [cpc.CANDLES_ONLY, cpc.WITH_FUNDING]
    assert any(e and e["funding_rate"]["current"] == 0.05 for e in seen)
    assert res["funding"]["coins"] == ["ETH"] and 0 < res["funding"]["with_funding"] < res["funding"]["reads"]
    text = cpc.render_telegram(res)
    assert "Funding found for 1 of 2 coins" in text and "candles + funding" in text


def test_workflow_runs_the_funding_and_record_studies():
    root = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")
    wf = open(os.path.join(root, "cadence-backtest.yml")).read()
    assert "python -m close_picks_compare --telegram --days 540 --funding" in wf
    assert "python -m close_record --telegram" in wf
    weekly = open(os.path.join(root, "close-record.yml")).read()
    assert "schedule:" in weekly and "python -m close_record --telegram" in weekly


# ── the live record ─────────────────────────────────────────────────────────

import close_record as cr                                              # noqa: E402


def _row(status="TP_HIT", ret=2.0, tf="1D", sym="ETH", gen=None, **kw):
    gen = gen if gen is not None else 20380 * DAY
    return {"id": f"{sym}{gen}{status}", "symbol": sym, "timeframe": tf, "direction": "LONG",
            "entry_price": "100", "stop_loss": "98", "status": status,
            "realized_return_pct": ret, "generated_at": datetime.fromtimestamp(
                gen / 1000, timezone.utc).isoformat(),
            "targets": [{"target_number": 2, "target_price": "104"},
                        {"target_number": 1, "target_price": "102"}], **kw}


def test_record_rows_become_settle_inputs_and_tracker_r():
    rec = cr.to_rec(_row())
    assert rec["tp_targets"] == [102.0, 104.0] and rec["entry"] == 100 and rec["sl"] == 98
    assert cr.to_rec(_row(targets=[])) is None
    assert cr.tracker_r(_row(ret=-2.0)) == -1.0 and cr.tracker_r(_row(ret=None)) is None
    t = cr.tracker_stats([_row(), _row(status="SL_HIT", ret=-2.0), _row(status="CANCELLED"),
                          _row(status="OPEN", ret=None)])
    assert (t["trades"], t["unfilled"], t["running"], t["win_rate_pct"]) == (2, 1, 1, 50.0)
    assert t["r_total"] == 0.0 and t["pf"] == 1.0


def test_record_report_is_too_early_with_few_trades():
    gen = 20380 * DAY
    h4s = {"ETH": [bar(gen + k * cpc.H4_MS, 100, 100.5, 99.5, 100) for k in range(60)]}
    res = cr.compare([_row(gen=gen), _row(tf="1W", gen=gen)], h4s, seeds=2)
    text = cr.render_telegram(res)
    assert "live record" in text and "too early" in text and "no picks yet" in text


def test_history_endpoint_attaches_targets_on_request(monkeypatch):
    pytest.importorskip("flask")
    import app
    calls = []

    class Store:
        DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE = 50, 200
        SignalValidationError = ValueError

        def list_signals(self, **kw):
            calls.append(kw)
            return {"items": [{"id": "x"}], "has_more": False}

        def attach_targets(self, rows):
            for r in rows:
                r["targets"] = [{"target_number": 1}]
            return rows
    monkeypatch.setattr(app, "_signal_store", lambda: Store())
    monkeypatch.setattr(app, "_db_guard", lambda: None)
    c = app.app.test_client()
    plain = c.get("/api/signals/history").get_json()
    assert "targets" not in plain["items"][0]                       # unchanged by default
    got = c.get("/api/signals/history?include_targets=1&strategy_version=v60_close_top5").get_json()
    assert got["items"][0]["targets"] == [{"target_number": 1}]
    assert calls[-1]["strategy_version"] == "v60_close_top5"
