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


def test_dedup_ids_round_trip(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    import app
    f = tmp_path / "alerts.json"
    monkeypatch.setattr(app, "_PATTERN_ALERT_FILE", str(f))
    app._pattern_alert_ids_save({"BTC:1D:reversal:double_top:123"})
    assert "BTC:1D:reversal:double_top:123" in app._pattern_alert_ids_load()
