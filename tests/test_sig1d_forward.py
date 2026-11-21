"""The 1D engine-signal forward paper book (sig1d_forward) and its daily job."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import sig1d_forward as sf                                             # noqa: E402

D = sf.DAY_MS


def candles(n=60):
    return [{"timestamp": k * D, "open": 100, "close": 100, "high": 102, "low": 98, "volume": 1}
            for k in range(n)]


def test_opens_only_long_or_short_at_62_plus():
    t = sf.open_trade("TAO", {"direction": "SHORT", "strength": 91}, candles())
    assert t["direction"] == "SHORT" and t["sl"] == 106.0 and t["target"] == 94.0
    assert t["id"] == f"TAO:SHORT:{59 * D}" and t["strength"] == 91
    assert sf.open_trade("TAO", {"direction": "SHORT", "strength": 61.9}, candles()) is None
    assert sf.open_trade("TAO", {"direction": "NEUTRAL", "strength": 90}, candles()) is None


def test_one_per_coin_per_direction_per_20_days():
    book = [{"symbol": "TAO", "direction": "SHORT", "state": "closed", "day_ms": 50 * D}]
    assert not sf.can_open(book, "TAO", "SHORT", 60 * D)
    assert sf.can_open(book, "TAO", "LONG", 60 * D)                 # the other direction
    assert sf.can_open(book, "TAO", "SHORT", 70 * D)


def test_the_tested_signal_runs_on_closed_candles():
    import random
    r, p, c = random.Random(3), 100.0, []
    for k in range(260):
        o = p
        p *= 1 + r.gauss(0, 0.03)
        c.append({"timestamp": k * D, "open": o, "close": p, "high": max(o, p) * 1.01,
                  "low": min(o, p) * 0.99, "volume": 100})
    s = sf.signal(c[-240:], "X", {"value": 12, "label": "Extreme Fear"})
    assert s.get("direction") in ("LONG", "SHORT", "NEUTRAL") and "strength" in s


def test_daily_job_and_the_shared_endpoint(monkeypatch):
    import app as app_mod
    import kv
    store = {}
    monkeypatch.setattr(app_mod, "_sig1d_load", lambda: list(store.get("b", [])))
    monkeypatch.setattr(app_mod, "_sig1d_save", lambda b: store.__setitem__("b", list(b)))
    monkeypatch.setattr(sf, "UNIVERSE", frozenset({"TAO", "ETH"}))
    monkeypatch.setattr(kv, "claim", lambda *a, **k: True)
    monkeypatch.setattr(kv, "release", lambda *a, **k: None)
    res = app_mod._sig1d_daily(fetch_fn=lambda s: candles(), notify=False,
                               signal_fn=lambda c, s: {"direction": "LONG", "strength": 70})
    assert res["ran"] and res["opened"] == 2
    nxt = candles() + [{"timestamp": 60 * D, "open": 100, "close": 105, "high": 107, "low": 99.5,
                        "volume": 1}]
    res = app_mod._sig1d_daily(fetch_fn=lambda s: nxt, notify=False,
                               signal_fn=lambda c, s: {"direction": "LONG", "strength": 70})
    assert res["closed"] == 2 and res["opened"] == 0
    assert all(t["state"] == "closed" and t["exit_reason"] == "target" for t in store["b"])

    calls = []
    monkeypatch.setattr(app_mod, "_monitor_candles", lambda s, tf: calls.append(s) or candles())
    monkeypatch.setattr(app_mod, "_rev_fwd_daily",
                        lambda fetch_fn: [fetch_fn(s) for s in ("TAO", "ETH")] and {"ran": True})
    monkeypatch.setattr(app_mod, "_sig1d_daily",
                        lambda fetch_fn: [fetch_fn(s) for s in ("TAO", "ETH")] and {"ran": True})
    monkeypatch.setattr(app_mod, "_cron_authorized", lambda: True)
    with app_mod.app.test_request_context("/api/cron/rev-forward", method="POST"):
        resp, code = app_mod.api_cron_rev_forward()
    assert code == 200 and sorted(calls) == ["ETH", "TAO"]           # each coin fetched once


def test_shown():
    root = os.path.join(os.path.dirname(__file__), "..")
    js = open(os.path.join(root, "dashboard", "js", "scalp.js")).read()
    assert 'renderBook1D("sig1d_forward"' in js
    assert 'id="sig1dFwd"' in open(os.path.join(root, "dashboard", "scalp.html")).read()
