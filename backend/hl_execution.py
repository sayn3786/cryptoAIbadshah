"""
Hyperliquid execution SAFETY SPINE — Phase 3.

This is the gate every future order write must pass, and the idempotency that
makes a placement exactly-once. It does NOT sign anything and does NOT place any
order — signing + placement arrive in Phase 4, behind this gate. Everything here
defaults OFF, so a deploy cannot trade until a human explicitly arms it.

Arming requires ALL of, or it stays disarmed:
  LIVE_TRADING_ENABLED  on   (feature switch, default off)
  HL_KILL_SWITCH        off  (a one-flip stop that overrides the above)
  HYPERLIQUID_ACCOUNT_ADDRESS set  (whose account we trade)
  HYPERLIQUID_AGENT_KEY       set  (the no-withdrawal agent key that will sign)

Idempotency: an order intent is keyed on (signal_id, intent, candle_ts) — the
CAUSING candle, never wall-clock — exactly like the signal store's lifecycle
writes. A deterministic client order id (cloid) is derived from the same triple,
so a retried monitor/cron run produces the same cloid and the same ledger key:
the exchange and our KV both dedup it. Placement is exactly-once per cause.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict

import hl_account
from kv import claim as _kv_claim, exists as _kv_exists

_ORDER_NS = "hlorder:"

# Per-trade sizing (testnet defaults). Read here so arm-status can surface them;
# the actual sizing gate lives in hl_meta.can_place.
DEFAULT_NOTIONAL_USD = 12.0
DEFAULT_LEVERAGE = 3.0


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def live_enabled() -> bool:
    return _flag("LIVE_TRADING_ENABLED")


def kill_switch_on() -> bool:
    return _flag("HL_KILL_SWITCH")


def agent_key_present() -> bool:
    """True when the agent key is set. We never read or return the value."""
    return bool((os.getenv("HYPERLIQUID_AGENT_KEY") or "").strip())


def arm_status() -> Dict[str, Any]:
    """Whether live order placement is armed, and precisely why not. Reveals no
    secret (only presence booleans)."""
    reasons = []
    if not live_enabled():
        reasons.append("LIVE_TRADING_ENABLED is off")
    if kill_switch_on():
        reasons.append("HL_KILL_SWITCH is on")
    if not hl_account.configured():
        reasons.append("HYPERLIQUID_ACCOUNT_ADDRESS is not set")
    if not agent_key_present():
        reasons.append("HYPERLIQUID_AGENT_KEY is not set")
    return {
        "armed": not reasons,
        "env": hl_account._env(),
        "live_enabled": live_enabled(),
        "kill_switch_on": kill_switch_on(),
        "account_configured": hl_account.configured(),
        "agent_key_present": agent_key_present(),
        "notional_usd": _num_env("HL_TRADE_NOTIONAL_USD", DEFAULT_NOTIONAL_USD),
        "leverage": _num_env("HL_LEVERAGE", DEFAULT_LEVERAGE),
        "reasons": reasons,
        # No signer or placement exists yet regardless of the switches.
        "live_ready": False,
    }


def is_armed() -> bool:
    return arm_status()["armed"]


def client_order_id(signal_id: Any, intent: str, candle_ts: Any) -> str:
    """A deterministic Hyperliquid cloid — '0x' + 32 hex (128 bits) — from the
    CAUSING (signal, intent, candle). A retry yields the SAME cloid, so the
    exchange rejects a duplicate; placement is exactly-once per cause."""
    raw = f"{signal_id}|{intent}|{candle_ts}".encode()
    return "0x" + hashlib.sha256(raw).hexdigest()[:32]


def order_ledger_key(signal_id: Any, intent: str, candle_ts: Any) -> str:
    return f"{_ORDER_NS}{signal_id}:{intent}:{candle_ts}"


def claim_order(signal_id: Any, intent: str, candle_ts: Any) -> bool:
    """Atomic exact-once claim for an order intent (KV SET-NX). True only for the
    first caller across retries / concurrent runs / cold starts. Mirrors the
    signal store's per-candle idempotency so nothing double-places."""
    return _kv_claim(order_ledger_key(signal_id, intent, candle_ts))


def order_already_placed(signal_id: Any, intent: str, candle_ts: Any) -> bool:
    return _kv_exists(order_ledger_key(signal_id, intent, candle_ts))
