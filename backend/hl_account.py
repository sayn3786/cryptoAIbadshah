"""
Read-only Hyperliquid account adapter — Phase 1 of the live-execution work.

Reads OUR OWN account's balance and open perpetual positions from Hyperliquid's
public ``info`` endpoint. It performs NO signing and places NO orders — the info
endpoint needs no key. This is the proof-of-connection and the "what do I
actually hold" reconcile foundation that a later, explicitly-armed executor
builds on; by construction it can place nothing.

Config (env):
  HYPERLIQUID_ENV              testnet (default) | mainnet — picks the API host
  HYPERLIQUID_ACCOUNT_ADDRESS  the MAIN account address (0x…); info requests use
                               the account's public address, never the agent's

The agent/API key (HYPERLIQUID_AGENT_KEY) is deliberately NOT read here: reads
don't need it, so Phase 1 never touches a secret that could place a trade.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

_TIMEOUT = 8

_MAINNET = "https://api.hyperliquid.xyz"
_TESTNET = "https://api.hyperliquid-testnet.xyz"


def _env(explicit: Optional[str] = None) -> str:
    return (explicit or os.getenv("HYPERLIQUID_ENV", "testnet")).strip().lower()


def base_url(env: Optional[str] = None) -> str:
    """API host for the configured network. Anything but an explicit 'mainnet'
    stays on TESTNET — a config typo must never silently point live."""
    return _MAINNET if _env(env) == "mainnet" else _TESTNET


def account_address(explicit: Optional[str] = None) -> str:
    return (explicit or os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")).strip()


def configured() -> bool:
    """True when an account address is set — the minimum to read state."""
    return bool(account_address())


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _post_info(body: Dict[str, Any], *, env: Optional[str] = None,
               session: Optional[Any] = None) -> Any:
    """POST to the info endpoint. `session` is injectable for tests (any object
    with a .post(url, json=, timeout=) returning a .json()/.raise_for_status())."""
    resp = (session or requests).post(f"{base_url(env)}/info", json=body,
                                      timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def parse_state(data: Any, address: str, env: Optional[str] = None) -> Dict[str, Any]:
    """Normalise a Hyperliquid ``clearinghouseState`` payload into a compact,
    UI-friendly account snapshot. Pure — no network — so it is unit-testable
    against a captured sample."""
    data = data or {}
    ms = data.get("marginSummary") or {}
    positions = []
    for ap in data.get("assetPositions") or []:
        p = ap.get("position") or {}
        szi = _num(p.get("szi"))
        if not szi:                                   # flat / zero-size → skip
            continue
        lev = p.get("leverage") or {}
        positions.append({
            "coin": p.get("coin"),
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "signed_size": szi,
            "entry_px": _num(p.get("entryPx")),
            "position_value_usd": _num(p.get("positionValue")),
            "unrealized_pnl_usd": _num(p.get("unrealizedPnl")),
            "leverage": _num(lev.get("value")),
            "leverage_type": lev.get("type"),
            "liquidation_px": _num(p.get("liquidationPx")),
            "margin_used_usd": _num(p.get("marginUsed")),
        })
    return {
        "configured": True,
        "env": _env(env),
        "address": address,
        "account_value_usd": _num(ms.get("accountValue")),
        "total_margin_used_usd": _num(ms.get("totalMarginUsed")),
        "total_notional_usd": _num(ms.get("totalNtlPos")),
        "withdrawable_usd": _num(data.get("withdrawable")),
        "open_positions": positions,
        "open_position_count": len(positions),
        # This adapter can only READ. Nothing here (or downstream of it yet) can
        # sign or place an order; a later phase flips this behind an arm switch.
        "live_ready": False,
    }


def account_state(address: Optional[str] = None, *, env: Optional[str] = None,
                  session: Optional[Any] = None) -> Dict[str, Any]:
    """Read our account's balance + open positions. Returns a not-configured
    marker (no exception) when no address is set."""
    addr = account_address(address)
    if not addr:
        return {"configured": False,
                "error": "HYPERLIQUID_ACCOUNT_ADDRESS is not set"}
    data = _post_info({"type": "clearinghouseState", "user": addr},
                      env=env, session=session)
    return parse_state(data, addr, env)


def _mask(addr: str) -> str:
    """0x1234…ABCD — enough to recognise the account without printing it whole."""
    a = addr or ""
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 12 else a


def public_status(*, env: Optional[str] = None,
                  session: Optional[Any] = None) -> Dict[str, Any]:
    """A SAFE, public-facing connection summary for the dashboard / a browser.

    Hyperliquid account state is already public on-chain, so a read view leaks
    nothing new — but we still (a) mask the address and (b) show the exact
    balance only on TESTNET; on mainnet we report funded/not without the figure,
    so a public page never advertises real-money position size. Never raises.
    """
    e = _env(env)
    if not configured():
        return {"configured": False, "connected": False, "env": e}
    try:
        st = account_state(env=env, session=session)
    except Exception:                                    # noqa: BLE001
        return {"configured": True, "connected": False, "env": e,
                "address": _mask(account_address())}
    is_testnet = e != "mainnet"
    val = st.get("account_value_usd")
    return {
        "configured": True,
        "connected": True,
        "env": st.get("env", e),
        "address": _mask(st.get("address", "")),
        # Exact balance on testnet only; on mainnet report funded, not the size.
        "account_value_usd": val if is_testnet else None,
        "funded": bool(val and val > 0),
        "open_position_count": st.get("open_position_count", 0),
        "live_ready": False,
    }
