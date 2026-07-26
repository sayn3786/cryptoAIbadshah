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


def test_scan_confirmed_patterns_parallel_and_claims(monkeypatch):
    # The cron/endpoint scan runs fetches in parallel and returns only newly
    # KV-claimed confirmations (exact-once). Mock the fetch + claim (no network).
    pytest.importorskip("flask")
    import app
    monkeypatch.setattr(app, "SYMBOLS", {"BTC": "BTCUSDT", "ETH": "ETHUSDT"})
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
