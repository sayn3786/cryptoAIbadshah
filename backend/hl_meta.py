"""
Hyperliquid perp exchange metadata + a pure sizing gate — Phase 2.

Answers, for a candidate signal, "can this even be placed on Hyperliquid, and at
what size?" — WITHOUT placing anything. It fetches the public perp `meta`
(per-asset szDecimals, max leverage, and the numeric asset index order payloads
use) and applies Hyperliquid's rounding rules:

  * size    → rounded DOWN to the asset's szDecimals (so notional never overshoots)
  * price   → <= 5 significant figures AND <= (6 - szDecimals) decimal places
  * min order value → $10 notional (HYPERLIQUID_MIN_ORDER_USD)

`can_place` is pure (takes the asset meta + free collateral, no network), so the
whole gate is unit-testable. Nothing here signs or sends an order.
"""
from __future__ import annotations

import math
import os
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

import hl_account

# Single source of truth for the minimum: the paper account already uses it.
try:
    from paper_account import HYPERLIQUID_MIN_ORDER_USD as HL_MIN_ORDER_USD
except Exception:                                        # noqa: BLE001
    HL_MIN_ORDER_USD = 10.0

PERP_MAX_PRICE_DECIMALS = 6      # spot would be 8; we only trade perps
PRICE_SIG_FIGS = 5

# Our base tickers are Hyperliquid perp coin names 1:1 for the majors we trade;
# this map is only for the odd mismatch (none today). resolve_coin falls back to
# the upper-cased symbol and then checks it actually exists in the live universe.
_COIN_OVERRIDES: Dict[str, str] = {}

_meta_cache: Dict[str, Any] = {"ts": 0.0, "env": None, "table": None}
_META_TTL = 3600                 # szDecimals / maxLeverage only change on new listings


# ── metadata ─────────────────────────────────────────────────────────────────

def _parse_universe(meta: Any) -> Dict[str, Dict[str, Any]]:
    """coin -> {asset_id, sz_decimals, max_leverage} from a `meta` payload.
    asset_id is the index in universe — the value order payloads use for `a`."""
    table: Dict[str, Dict[str, Any]] = {}
    for i, a in enumerate((meta or {}).get("universe") or []):
        name = a.get("name")
        if not name:
            continue
        table[name.upper()] = {
            "asset_id": i,
            "sz_decimals": int(a.get("szDecimals") or 0),
            "max_leverage": int(a.get("maxLeverage") or 1),
            "only_isolated": bool(a.get("onlyIsolated") or False),
        }
    return table


def asset_table(*, env: Optional[str] = None, session: Optional[Any] = None,
                force: bool = False) -> Dict[str, Dict[str, Any]]:
    """The perp asset table, cached (meta changes only on new listings). Never
    raises: returns the last good table, or {} if we never fetched one."""
    e = hl_account._env(env)
    now = time.time()
    if (not force and _meta_cache["table"] is not None and _meta_cache["env"] == e
            and now - _meta_cache["ts"] < _META_TTL):
        return _meta_cache["table"]
    try:
        meta = hl_account._post_info({"type": "meta"}, env=env, session=session)
        table = _parse_universe(meta)
        if table:
            _meta_cache.update(ts=now, env=e, table=table)
        return table or (_meta_cache["table"] or {})
    except Exception:                                    # noqa: BLE001
        return _meta_cache["table"] or {}


def resolve_coin(symbol: str, table: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Our base ticker → the Hyperliquid perp coin name, if it is listed."""
    if not symbol:
        return None
    coin = _COIN_OVERRIDES.get(symbol.upper(), symbol.upper())
    return coin if coin in table else None


# ── rounding (Hyperliquid rules) ─────────────────────────────────────────────

def round_size(size: Optional[float], sz_decimals: int) -> Optional[float]:
    """Round DOWN to szDecimals places, so the resulting notional never exceeds
    the target. Rounding down also means a too-small order lands at 0 (rejected)."""
    if size is None or size <= 0:
        return None
    q = 10 ** max(0, int(sz_decimals))
    r = math.floor(size * q) / q
    return r if r > 0 else None                          # too small for one lot → None


def round_price(px: Optional[float], sz_decimals: int, *,
                max_decimals: int = PERP_MAX_PRICE_DECIMALS,
                sig_figs: int = PRICE_SIG_FIGS) -> Optional[float]:
    """A price Hyperliquid will accept: <= sig_figs significant figures AND
    <= (max_decimals - szDecimals) decimal places. Integers are always allowed."""
    if not px or px <= 0:
        return None
    d = Decimal(str(px))
    # (1) significant figures
    shift = sig_figs - 1 - d.adjusted()
    d_sig = (d.scaleb(shift).to_integral_value(rounding=ROUND_HALF_UP).scaleb(-shift))
    # (2) decimal-place cap
    allowed = max(0, max_decimals - int(sz_decimals))
    d_final = d_sig.quantize(Decimal(1).scaleb(-allowed), rounding=ROUND_HALF_UP)
    return float(d_final)


# ── the pure sizing gate ─────────────────────────────────────────────────────

def can_place(symbol: str, entry_px: Optional[float], *,
              notional_usd: float, leverage: float,
              free_collateral_usd: Optional[float],
              table: Dict[str, Dict[str, Any]],
              min_notional_usd: float = HL_MIN_ORDER_USD) -> Dict[str, Any]:
    """Whether a signal could be placed on Hyperliquid, and the concrete size.

    Pure: pass the asset `table` and current `free_collateral_usd`; no network.
    Reason codes on reject: SYMBOL_NOT_ON_HYPERLIQUID, BAD_ENTRY_PRICE,
    SIZE_ROUNDS_TO_ZERO, BELOW_MIN_NOTIONAL, INSUFFICIENT_MARGIN.
    Leverage is clamped to the asset max (never rejected on it alone).
    """
    def _reject(reason, **extra):
        return {"ok": False, "reason": reason, "symbol": symbol, **extra}

    coin = resolve_coin(symbol, table)
    if coin is None:
        return _reject("SYMBOL_NOT_ON_HYPERLIQUID")
    meta = table[coin]
    if not entry_px or entry_px <= 0:
        return _reject("BAD_ENTRY_PRICE", coin=coin)

    lev_req = max(1.0, float(leverage or 1))
    lev = min(lev_req, float(meta["max_leverage"]))
    lev_clamped = lev < lev_req

    size = round_size((notional_usd or 0) / entry_px, meta["sz_decimals"])
    if not size:
        # Notional too small to express one lot of this asset at its precision.
        return _reject("SIZE_ROUNDS_TO_ZERO", coin=coin,
                       sz_decimals=meta["sz_decimals"])
    notional = size * entry_px
    if notional + 1e-9 < min_notional_usd:
        return _reject("BELOW_MIN_NOTIONAL", coin=coin, size=size,
                       notional_usd=round(notional, 6),
                       min_notional_usd=min_notional_usd)

    margin = notional / lev
    if free_collateral_usd is not None and margin > float(free_collateral_usd) + 1e-9:
        return _reject("INSUFFICIENT_MARGIN", coin=coin, size=size,
                       notional_usd=round(notional, 6),
                       margin_required_usd=round(margin, 6),
                       free_collateral_usd=round(float(free_collateral_usd), 6),
                       leverage=lev)

    return {
        "ok": True, "reason": None, "symbol": symbol, "coin": coin,
        "asset_id": meta["asset_id"], "size": size,
        "price": round_price(entry_px, meta["sz_decimals"]),
        "notional_usd": round(notional, 6),
        "leverage": lev, "leverage_clamped": lev_clamped,
        "margin_required_usd": round(margin, 6),
        "sz_decimals": meta["sz_decimals"], "max_leverage": meta["max_leverage"],
    }


def plan_order(symbol: str, entry_px: float, *, notional_usd: float, leverage: float,
               env: Optional[str] = None, session: Optional[Any] = None) -> Dict[str, Any]:
    """can_place wired to the live meta + our current perp collateral. Reads
    only; still places nothing."""
    table = asset_table(env=env, session=session)
    if not table:
        return {"ok": False, "reason": "META_UNAVAILABLE", "symbol": symbol}
    free = None
    try:
        free = hl_account.account_state(env=env, session=session).get("account_value_usd")
    except Exception:                                    # noqa: BLE001
        pass
    return can_place(symbol, entry_px, notional_usd=notional_usd, leverage=leverage,
                     free_collateral_usd=free, table=table)


def _num_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
