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
from typing import Any, Dict, List, Optional

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


def _classify_trigger(order: Dict[str, Any], side: str,
                      mark: Optional[float]) -> Optional[str]:
    """'sl' / 'tp' for a reduce-only trigger order, from Hyperliquid's orderType
    ("Stop Market", "Take Profit Limit", ...); falls back to where the trigger
    sits relative to the mark when the type is missing."""
    ot = str(order.get("orderType") or "").lower()
    if ot.startswith("stop"):
        return "sl"
    if ot.startswith("take profit"):
        return "tp"
    px = _num(order.get("triggerPx"))
    if px is None or mark is None:
        return None
    below = px < mark
    return ("sl" if below else "tp") if side == "long" else ("tp" if below else "sl")


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return round((a - b) / b * 100, 2) if a is not None and b else None


def enrich_positions(state: Dict[str, Any], orders: Any,
                     mids: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each open position with its live mark, stop-loss and take-profit(s).

    Pure (no network), so it is unit-testable against captured payloads.
    `orders` is Hyperliquid's frontendOpenOrders list; only REDUCE-ONLY TRIGGER
    orders on the position's coin count as its SL / TP. Distances are % from
    the current mark; R:R uses the nearest TP against the stop, from entry.
    """
    by_coin: Dict[str, List[Dict[str, Any]]] = {}
    for o in orders or []:
        if isinstance(o, dict) and o.get("isTrigger") and o.get("reduceOnly"):
            by_coin.setdefault(str(o.get("coin")), []).append(o)
    out = []
    for p in state.get("open_positions") or []:
        coin, side = p.get("coin"), p.get("side")
        entry, size = p.get("entry_px"), p.get("size") or 0
        mark = _num((mids or {}).get(coin))
        sign = 1 if side == "long" else -1
        sls, tps = [], []
        for o in by_coin.get(str(coin), []):
            kind = _classify_trigger(o, side, mark)
            px = _num(o.get("triggerPx"))
            if px is None or kind is None:
                continue
            (sls if kind == "sl" else tps).append(px)
        # The stop that fires first, and TPs nearest-first, in trade direction.
        sl = (max(sls) if side == "long" else min(sls)) if sls else None
        tps.sort(reverse=(side != "long"))
        tp = tps[0] if tps else None
        risk = abs(entry - sl) if entry is not None and sl is not None else None
        reward = abs(tp - entry) if entry is not None and tp is not None else None
        margin = p.get("margin_used_usd")
        upnl = p.get("unrealized_pnl_usd")
        out.append({
            **p,
            "mark_px": mark,
            "move_pct": (round(sign * _pct(mark, entry), 2)
                         if _pct(mark, entry) is not None else None),
            "roe_pct": round(upnl / margin * 100, 2) if upnl is not None and margin else None,
            "sl_px": sl,
            "sl_dist_pct": _pct(sl, mark),
            "tp_px": tp,
            "tp_dist_pct": _pct(tp, mark),
            "tp_all_px": tps,
            "risk_usd": round(risk * size, 2) if risk is not None else None,
            "reward_usd": round(reward * size, 2) if reward is not None else None,
            "rr": round(reward / risk, 2) if risk and reward is not None else None,
            "liq_dist_pct": _pct(p.get("liquidation_px"), mark),
            "protected": sl is not None,
        })
    return out


def positions_detail(address: Optional[str] = None, *, env: Optional[str] = None,
                     session: Optional[Any] = None) -> Dict[str, Any]:
    """Account snapshot + enriched positions (entry, mark, SL, TP, P&L, R:R).
    Three read-only info calls; open orders and mids are best-effort so a
    failure there still shows the positions, just without SL/TP or mark."""
    addr = account_address(address)
    if not addr:
        return {"configured": False,
                "error": "HYPERLIQUID_ACCOUNT_ADDRESS is not set"}
    state = parse_state(_post_info({"type": "clearinghouseState", "user": addr},
                                   env=env, session=session), addr, env)
    orders, mids, partial = [], {}, []
    if state["open_positions"]:
        try:
            orders = _post_info({"type": "frontendOpenOrders", "user": addr},
                                env=env, session=session) or []
        except Exception:                                # noqa: BLE001
            partial.append("orders")
        try:
            mids = all_mids(env=env, session=session)
        except Exception:                                # noqa: BLE001
            partial.append("mids")
    return {
        "configured": True,
        "env": state["env"],
        "address": _mask(addr),
        "account_value_usd": state["account_value_usd"],
        "total_notional_usd": state["total_notional_usd"],
        "withdrawable_usd": state["withdrawable_usd"],
        "unrealized_pnl_usd": round(sum(p.get("unrealized_pnl_usd") or 0
                                        for p in state["open_positions"]), 2),
        "positions": enrich_positions(state, orders, mids),
        "partial": partial,
    }


def all_mids(*, env: Optional[str] = None, session: Optional[Any] = None) -> Dict[str, Any]:
    """Every perp coin's current mid price, as returned by the info endpoint."""
    return _post_info({"type": "allMids"}, env=env, session=session) or {}


def mid_price(coin: str, *, env: Optional[str] = None,
              session: Optional[Any] = None) -> Optional[float]:
    """The live mid price for one coin — what a MARKET order will fill near, so
    order size and exposure must be computed from this, not a stale signal price."""
    return _num(all_mids(env=env, session=session).get(coin))


def _mask(addr: str) -> str:
    """0x1234…ABCD — enough to recognise the account without printing it whole."""
    a = addr or ""
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 12 else a


def _spot_usdc(data: Any) -> Optional[float]:
    """USDC total from a spotClearinghouseState payload, or None."""
    for b in (data or {}).get("balances") or []:
        if (b.get("coin") or "").upper() == "USDC":
            return _num(b.get("total"))
    return None


def spot_usdc(address: Optional[str] = None, *, env: Optional[str] = None,
              session: Optional[Any] = None) -> Optional[float]:
    """Our SPOT-wallet USDC balance (the drip lands here; perps collateral is a
    separate balance). None when unconfigured. Reads spotClearinghouseState."""
    addr = account_address(address)
    if not addr:
        return None
    data = _post_info({"type": "spotClearinghouseState", "user": addr},
                      env=env, session=session)
    return _spot_usdc(data)


def public_status(*, env: Optional[str] = None,
                  session: Optional[Any] = None) -> Dict[str, Any]:
    """A SAFE, public-facing connection summary for the dashboard / a browser.

    Reports BOTH the perps collateral (accountValue) and the spot USDC balance —
    the testnet drip lands in spot, so a zero perps value with spot USDC means
    "transfer spot → perps to trade". Hyperliquid account state is already public
    on-chain, but we still (a) mask the address and (b) show the exact figures
    only on TESTNET; on mainnet we report funded/not without the numbers, so a
    public page never advertises real-money size. Never raises.
    """
    e = _env(env)
    if not configured():
        return {"configured": False, "connected": False, "env": e}
    try:
        st = account_state(env=env, session=session)
    except Exception:                                    # noqa: BLE001
        return {"configured": True, "connected": False, "env": e,
                "address": _mask(account_address())}
    spot = None                                          # best-effort; never fails the status
    try:
        spot = spot_usdc(env=env, session=session)
    except Exception:                                    # noqa: BLE001
        pass
    is_testnet = e != "mainnet"
    perp_val = st.get("account_value_usd")
    perps_funded = bool(perp_val and perp_val > 0)
    spot_funded = bool(spot and spot > 0)
    return {
        "configured": True,
        "connected": True,
        "env": st.get("env", e),
        "address": _mask(st.get("address", "")),
        # Exact figures on testnet only; on mainnet report funded, not the size.
        "account_value_usd": perp_val if is_testnet else None,
        "spot_usdc_usd": spot if is_testnet else None,
        "perps_funded": perps_funded,
        "spot_funded": spot_funded,
        "funded": perps_funded or spot_funded,
        # A hint the UI can show: money is in spot, not usable as perp collateral.
        "needs_spot_to_perp_transfer": spot_funded and not perps_funded,
        "open_position_count": st.get("open_position_count", 0),
        "live_ready": False,
    }
