"""
Pattern-confirmation Telegram alerts: message formatting + the lightweight scan
that extracts freshly-confirmed patterns (with a freshness guard). Synthetic
candles; no live APIs or network (send is never called here).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from telegram import build_pattern_alert_message                         # noqa: E402

T0, STEP = 1_000_000, 3_600_000


def _series(points, pad=0.15):
    return [{"timestamp": T0 + i * STEP, "open": p, "high": p + pad,
             "low": p - pad, "close": p} for i, p in enumerate(points)]


# ── message formatting ──────────────────────────────────────────────────────
def test_message_lists_each_confirmed_pattern():
    msg = build_pattern_alert_message([
        {"symbol": "BTC", "timeframe": "1D", "label": "Inverse Head & Shoulders",
         "direction": "bullish", "break_dir": "up", "level": 100.0, "target": 120.0},
        {"symbol": "ETH", "timeframe": "1W", "label": "Double Top",
         "direction": "bearish", "break_dir": "down", "level": 90.0, "target": 70.0},
    ])
    assert "Pattern Confirmed" in msg
    assert "BTC/USDT 1D" in msg and "Inverse Head & Shoulders" in msg
    assert "ETH/USDT 1W" in msg and "Double Top" in msg
    assert "↑" in msg and "↓" in msg                 # break direction arrows
    assert "🎯" in msg                                # target present


def test_message_handles_missing_level_and_target():
    msg = build_pattern_alert_message([
        {"symbol": "SOL", "timeframe": "1D", "label": "Symmetrical Triangle",
         "direction": "bullish", "break_dir": "up", "level": None, "target": None}])
    assert "SOL/USDT 1D" in msg and "Symmetrical Triangle" in msg


# ── lightweight scan + freshness ────────────────────────────────────────────
DT = [80, 84, 88, 92, 96, 100, 97, 94, 91, 90, 92, 95, 98, 100.4]        # double top base


def test_confirmed_pattern_is_extracted_when_fresh():
    pytest.importorskip("flask")
    import app
    cs = _series(DT + [97, 93, 89, 87])              # closes below neckline on the last bars
    found = app._confirmed_patterns_for(cs, "1D")
    tops = [p for p in found if p["kind"] == "reversal" and "Top" in p["label"]]
    assert tops, "a fresh confirmed double top must be extracted"
    t = tops[0]
    assert t["direction"] == "bearish" and t["break_dir"] == "down"
    assert t["target"] is not None and t["break_ts"] is not None


def test_stale_confirmation_is_filtered_out():
    pytest.importorskip("flask")
    import app
    # Confirm, then pad many flat bars so the break is far older than the window.
    cs = _series(DT + [97, 93, 89, 87] + [87.0] * 8)
    found = app._confirmed_patterns_for(cs, "1D")
    assert not [p for p in found if p["kind"] == "reversal"], \
        "a confirmation older than PATTERN_ALERT_FRESH_BARS must be filtered"


def test_pattern_alerts_endpoint_returns_confirmed(monkeypatch):
    # The bell endpoint returns confirmed patterns (no KV mutation).
    pytest.importorskip("flask")
    import app
    monkeypatch.setattr(app, "SYMBOLS", {"BTC": "BTCUSDT"})
    monkeypatch.setattr(app, "PATTERN_BELL_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_closed_spot", lambda sym, tf: _series(DT + [97, 93, 89, 87]))
    app._pattern_bell_cache["data"] = None
    app._pattern_bell_cache["ts"] = 0
    resp = app.app.test_client().get("/api/pattern-alerts")
    assert resp.status_code == 200
    alerts = resp.get_json()["alerts"]
    assert any(a["symbol"] == "BTC" and a["type"] in ("double_top", "triple_top") for a in alerts)


def test_bell_detection_time_is_persisted_not_scan_time(monkeypatch):
    # `detected_at` must be the FIRST time we saw the alert, held stable across
    # scans — not re-stamped with the scan time on every refresh.
    pytest.importorskip("flask")
    import app
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "PATTERN_BELL_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_closed_spot", lambda sym, tf: _series(DT + [97, 93, 89, 87]))
    store = {}
    monkeypatch.setattr(app, "_kv_get", lambda k: store.get(k))
    monkeypatch.setattr(app, "_kv_set", lambda k, v, *a, **kw: (store.__setitem__(k, v), True)[1])

    app._pattern_bell_cache["data"] = None
    app._pattern_bell_cache["ts"] = 0
    first = app.app.test_client().get("/api/pattern-alerts").get_json()["alerts"]
    assert first, "expected a confirmed alert"
    a0 = first[0]
    assert a0.get("first_ms", 0) > 0
    assert store, "the first sighting must be persisted"

    # A later scan (cache cleared) reuses the STORED time, so detection stays put.
    app._pattern_bell_cache["data"] = None
    app._pattern_bell_cache["ts"] = 0
    second = app.app.test_client().get("/api/pattern-alerts").get_json()["alerts"]
    a1 = next(x for x in second
              if x["symbol"] == a0["symbol"] and x.get("break_ts") == a0.get("break_ts"))
    assert a1["first_ms"] == a0["first_ms"]
    assert a1["detected_at"] == a0["detected_at"]


def test_bell_detection_time_falls_back_when_kv_unavailable(monkeypatch):
    # KV down (get/set raise) must not break the bell — it still returns a
    # timestamp (now), just not persisted.
    pytest.importorskip("flask")
    import app

    def _boom(*a, **k):
        raise RuntimeError("kv down")
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "PATTERN_BELL_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_closed_spot", lambda sym, tf: _series(DT + [97, 93, 89, 87]))
    monkeypatch.setattr(app, "_kv_get", _boom)
    monkeypatch.setattr(app, "_kv_set", _boom)
    app._pattern_bell_cache["data"] = None
    app._pattern_bell_cache["ts"] = 0
    resp = app.app.test_client().get("/api/pattern-alerts")
    assert resp.status_code == 200
    a = resp.get_json()["alerts"][0]
    assert a.get("first_ms", 0) > 0 and a.get("detected_at")


def test_engulf_endpoint_detection_time_is_persisted(monkeypatch):
    # The engulf bell shares the first-seen persistence: its `detected_at` must be
    # a stable first sighting (seeded from the weekly close), not the scan time.
    pytest.importorskip("flask")
    import app
    now_ms = int(__import__("time").time() * 1000)
    open_ms = now_ms - 10 * 86400 * 1000        # opened 10d ago → weekly close 3d ago
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "SYMBOLS", {"BTC": "BTCUSDT"})
    monkeypatch.setattr(app.client, "get_spot_klines", lambda *a, **k: [{"x": 1}])
    monkeypatch.setattr(app, "_split_closed", lambda candles, secs: (candles, None))
    monkeypatch.setattr(app, "detect_engulfing", lambda closed, lookback=2: [
        {"direction": "bullish", "body_ratio": 2.0, "candles_ago": 1,
         "timestamp": open_ms, "engulf_open": 1.0, "engulf_close": 2.0}])
    store = {}
    monkeypatch.setattr(app, "_kv_get", lambda k: store.get(k))
    monkeypatch.setattr(app, "_kv_set", lambda k, v, *a, **kw: (store.__setitem__(k, v), True)[1])

    app._engulf_cache["data"] = None
    app._engulf_cache["ts"] = 0
    a0 = app.app.test_client().get("/api/engulf-alerts").get_json()["alerts"][0]
    assert a0.get("first_ms", 0) > 0
    # Seeded from the weekly close (open + 7d), which is in the past here.
    assert a0["first_ms"] == open_ms + 7 * 86400 * 1000
    assert store, "engulf first sighting must be persisted"

    app._engulf_cache["data"] = None
    app._engulf_cache["ts"] = 0
    a1 = app.app.test_client().get("/api/engulf-alerts").get_json()["alerts"][0]
    assert a1["first_ms"] == a0["first_ms"] and a1["detected_at"] == a0["detected_at"]


def test_pattern_alerts_endpoint_excludes_forming_divergence(monkeypatch):
    # The bell is a CONFIRMED-only surface. A forming (provisional) divergence is
    # a Telegram-only early heads-up — anchored on its prior pivot for dedup — so
    # it must not appear in the bell feed even though it shares the scan function.
    pytest.importorskip("flask")
    import app
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "PATTERN_BELL_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_closed_spot", lambda sym, tf: _series(DT))
    monkeypatch.setattr(app, "_confirmed_patterns_for", lambda closed, tf: [
        {"kind": "reversal", "type": "double_top", "label": "Double Top",
         "direction": "bearish", "break_dir": "down", "level": 100.0,
         "target": 70.0, "break_ts": 123},
        {"kind": "divergence_forming", "event": "forming", "type": "bearish",
         "label": "Forming Bearish RSI Divergence", "direction": "bearish",
         "break_dir": None, "level": None, "target": None, "break_ts": 99,
         "rsi_gap": 6.4, "age_candles": 0, "closes_to_confirm": 3},
    ])
    app._pattern_bell_cache["data"] = None
    app._pattern_bell_cache["ts"] = 0
    resp = app.app.test_client().get("/api/pattern-alerts")
    assert resp.status_code == 200
    kinds = [a["kind"] for a in resp.get_json()["alerts"]]
    assert "reversal" in kinds
    assert "divergence_forming" not in kinds, "forming divergence must stay out of the bell"


def test_scan_confirmed_patterns_parallel_and_claims(monkeypatch):
    # The cron/endpoint scan runs fetches in parallel and returns only newly
    # KV-claimed confirmations (exact-once). Mock the fetch + claim (no network).
    pytest.importorskip("flask")
    import app
    # SCAN_SYMBOLS, not SYMBOLS: the browsable list is 50 while the scanned one
    # is deliberately 31, because every scanned symbol costs fetches on a path
    # already close to its timeout. See tests/test_symbol_scope.py.
    monkeypatch.setattr(app, "SYMBOLS", {"BTC": "BTCUSDT", "ETH": "ETHUSDT"})
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC", "ETH"))
    monkeypatch.setattr(app, "PATTERN_ALERT_TFS", ["1D"])
    monkeypatch.setattr(app, "_fetch_closed_spot", lambda sym, tf: _series(DT + [97, 93, 89, 87]))
    claimed = set()
    monkeypatch.setattr(app, "_kv_claim", lambda key: claimed.add(key) or True)
    out = app._scan_confirmed_patterns()
    syms = {a["symbol"] for a in out}
    assert syms == {"BTC", "ETH"}                    # both scanned in parallel
    assert all(a["kind"] == "reversal" for a in out if "Top" in a.get("label", ""))
    # a second scan claims nothing new (exact-once)
    monkeypatch.setattr(app, "_kv_claim", lambda key: key not in claimed)
    assert app._scan_confirmed_patterns() == []


def test_kv_file_fallback_claims_exactly_once(tmp_path, monkeypatch):
    # With no KV configured, claim() uses the local file and is exact-once.
    import kv
    monkeypatch.setattr(kv, "_KV_URL", "")
    monkeypatch.setattr(kv, "_KV_TOKEN", "")
    monkeypatch.setattr(kv, "_FILE", str(tmp_path / "dedup.json"))
    key = "patalert:BTC:1D:reversal:double_top:123"
    assert kv.claim(key) is True          # first caller wins
    assert kv.claim(key) is False         # second caller skips
    assert kv.exists(key) is True


def test_kv_rest_claim_uses_set_nx(monkeypatch):
    # With KV configured, claim() issues SET NX and honors its result.
    import kv
    monkeypatch.setattr(kv, "_KV_URL", "https://example.upstash.io")
    monkeypatch.setattr(kv, "_KV_TOKEN", "tok")
    store = {}

    def fake_cmd(*args):
        if args[0] == "SET" and "NX" in args:      # SET key 1 NX EX ttl
            k = args[1]
            if k in store:
                return None
            store[k] = "1"
            return "OK"
        if args[0] == "EXISTS":
            return 1 if args[1] in store else 0
        return None

    monkeypatch.setattr(kv, "_kv_cmd", fake_cmd)
    key = "patalert:ETH:1W:triangle:rising_wedge:999"
    assert kv.claim(key) is True
    assert kv.claim(key) is False
    assert kv.exists(key) is True
    assert kv.exists("patalert:none") is False


# ── failure events ──────────────────────────────────────────────────────────
def test_failure_alert_message():
    msg = build_pattern_alert_message([
        {"symbol": "TAO", "timeframe": "1D", "label": "Falling Wedge",
         "direction": "bullish", "event": "failed", "level": 199.2,
         "reason": "gave back the whole breakout move", "retest": "retest_failed"}])
    assert "FAILED" in msg and "TAO/USDT 1D" in msg
    assert "retest failed" in msg


def test_mixed_confirmed_and_failed_message():
    msg = build_pattern_alert_message([
        {"symbol": "TAO", "timeframe": "1D", "label": "Falling Wedge",
         "direction": "bullish", "event": "failed", "level": 199.2},
        {"symbol": "BTC", "timeframe": "1W", "label": "Double Bottom",
         "direction": "bullish", "break_dir": "up", "level": 100.0, "target": 120.0}])
    assert "Pattern Update" in msg
    assert "FAILED" in msg and "confirmed" in msg


def test_alert_id_separates_confirmed_from_failed(monkeypatch):
    pytest.importorskip("flask")
    import app
    base = {"kind": "triangle", "type": "falling_wedge", "break_ts": 123}
    a = app._pattern_alert_id("TAO", "1D", {**base, "event": "confirmed"})
    b = app._pattern_alert_id("TAO", "1D", {**base, "event": "failed"})
    assert a != b, "a failure must alert separately from its confirmation"
