"""
Hyperliquid execution safety spine (Phase 3): arm/kill switches + order
idempotency. No signing, no orders — this module places nothing. Verifies the
gate defaults OFF, the kill switch overrides, the deterministic cloid, and the
exact-once order claim (KV file fallback).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_execution as hx                                             # noqa: E402


def _arm_env(monkeypatch, *, live="on", kill=None, addr="0xABC", key="0xKEY"):
    for name in ("LIVE_TRADING_ENABLED", "HL_KILL_SWITCH",
                 "HYPERLIQUID_ACCOUNT_ADDRESS", "HYPERLIQUID_AGENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    if live is not None: monkeypatch.setenv("LIVE_TRADING_ENABLED", live)
    if kill is not None: monkeypatch.setenv("HL_KILL_SWITCH", kill)
    if addr is not None: monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", addr)
    if key is not None:  monkeypatch.setenv("HYPERLIQUID_AGENT_KEY", key)


# ── the gate defaults OFF ─────────────────────────────────────────────────────

def test_disarmed_by_default(monkeypatch):
    for name in ("LIVE_TRADING_ENABLED", "HL_KILL_SWITCH",
                 "HYPERLIQUID_ACCOUNT_ADDRESS", "HYPERLIQUID_AGENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    s = hx.arm_status()
    assert s["armed"] is False and hx.is_armed() is False
    assert "LIVE_TRADING_ENABLED is off" in s["reasons"]
    assert s["live_ready"] is False


def test_armed_only_when_all_conditions_met(monkeypatch):
    _arm_env(monkeypatch)
    s = hx.arm_status()
    assert s["armed"] is True and s["reasons"] == []
    assert s["live_enabled"] is True and s["agent_key_present"] is True


def test_kill_switch_overrides(monkeypatch):
    _arm_env(monkeypatch, kill="on")
    s = hx.arm_status()
    assert s["armed"] is False
    assert "HL_KILL_SWITCH is on" in s["reasons"]


def test_missing_agent_key_disarms(monkeypatch):
    _arm_env(monkeypatch, key=None)
    s = hx.arm_status()
    assert s["armed"] is False
    assert "HYPERLIQUID_AGENT_KEY is not set" in s["reasons"]
    assert s["agent_key_present"] is False


def test_missing_address_disarms(monkeypatch):
    _arm_env(monkeypatch, addr=None)
    assert hx.arm_status()["armed"] is False


def test_arm_status_reports_sizing_defaults(monkeypatch):
    _arm_env(monkeypatch)
    monkeypatch.delenv("HL_TRADE_NOTIONAL_USD", raising=False)
    monkeypatch.delenv("HL_LEVERAGE", raising=False)
    s = hx.arm_status()
    assert s["notional_usd"] == 25.0 and s["leverage"] == 3.0
    monkeypatch.setenv("HL_TRADE_NOTIONAL_USD", "40")
    monkeypatch.setenv("HL_LEVERAGE", "5")
    s2 = hx.arm_status()
    assert s2["notional_usd"] == 40.0 and s2["leverage"] == 5.0


def test_arm_status_never_leaks_the_key(monkeypatch):
    _arm_env(monkeypatch, key="0xSUPERSECRETKEY")
    import json
    assert "0xSUPERSECRETKEY" not in json.dumps(hx.arm_status())


# ── deterministic client order id (cloid) ────────────────────────────────────

def test_cloid_is_deterministic_and_well_formed():
    a = hx.client_order_id("sig1", "open", 1000)
    b = hx.client_order_id("sig1", "open", 1000)
    assert a == b                                     # a retry yields the same id
    assert a.startswith("0x") and len(a) == 34        # 0x + 32 hex = 128 bits
    int(a, 16)                                        # valid hex


def test_cloid_differs_by_signal_intent_and_candle():
    base = hx.client_order_id("sig1", "open", 1000)
    assert hx.client_order_id("sig2", "open", 1000) != base   # signal
    assert hx.client_order_id("sig1", "close", 1000) != base  # intent
    assert hx.client_order_id("sig1", "open", 2000) != base   # candle


# ── exact-once order claim ───────────────────────────────────────────────────

def test_claim_order_is_exact_once(tmp_path, monkeypatch):
    import kv
    monkeypatch.setattr(kv, "_KV_URL", "")            # force local-file fallback
    monkeypatch.setattr(kv, "_KV_TOKEN", "")
    monkeypatch.setattr(kv, "_FILE", str(tmp_path / "orders.json"))
    assert hx.claim_order("sig1", "open", 1000) is True     # first wins
    assert hx.claim_order("sig1", "open", 1000) is False    # retry is a no-op
    assert hx.order_already_placed("sig1", "open", 1000) is True
    # a different cause is its own claim
    assert hx.claim_order("sig1", "close", 1000) is True


# ── the arm-status endpoint is internal + fail-closed ────────────────────────

def _app():
    pytest.importorskip("flask")
    import app
    return app


def test_arm_status_endpoint_requires_internal_auth(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    resp = app.app.test_client().get("/api/hl/arm-status")
    assert resp.status_code == 401


def test_arm_status_endpoint_reports_disarmed(monkeypatch):
    app = _app()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    for name in ("LIVE_TRADING_ENABLED", "HYPERLIQUID_AGENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    resp = app.app.test_client().get("/api/hl/arm-status",
                                     headers={"x-cron-secret": "s3cret"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["armed"] is False and body["live_ready"] is False


def test_arm_status_endpoint_accepts_dedicated_admin_token(monkeypatch):
    # Operators can drive the HL admin endpoints with a self-set HL_ADMIN_TOKEN,
    # without needing the shared CRON_SECRET.
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setenv("HL_ADMIN_TOKEN", "hl-token-123")
    ok = app.app.test_client().get("/api/hl/arm-status",
                                   headers={"x-hl-token": "hl-token-123"})
    assert ok.status_code == 200
    # a wrong token is refused; with neither secret set it is fail-closed
    bad = app.app.test_client().get("/api/hl/arm-status",
                                    headers={"x-hl-token": "nope"})
    assert bad.status_code == 401
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    closed = app.app.test_client().get("/api/hl/arm-status")
    assert closed.status_code == 401
