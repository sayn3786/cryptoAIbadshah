"""
Hyperliquid order SIGNING + placement — Phase 4a. The FIRST write path.

Nothing here fires unless a human has armed it (hl_execution.is_armed(): all
switches on, agent key present, kill switch off). On top of that, MAINNET is
hard-blocked unless HL_ALLOW_MAINNET is explicitly on — so this phase can only
trade testnet by default even when "armed". Signing uses the official
hyperliquid-python-sdk with the no-withdrawal AGENT key (imported lazily, only
when we actually send).

Every placement is:
  * exact-once — the Phase 3 deterministic cloid + KV claim (a retry no-ops),
  * reconciled — never opens a second position on a coin we already hold,
  * capped — max orders per run AND max total notional exposure.

`open_position` is a pure decision that takes the account snapshot, the asset
table and an injected `send_fn` (mocked in tests) — so the whole gate is tested
without the SDK or the network. The SDK send wrappers are the only impure part.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

import hl_account
import hl_execution
import hl_meta
from kv import release as _kv_release

DEFAULT_MAX_ORDERS_PER_RUN = 3
DEFAULT_MAX_EXPOSURE_USD = 100.0


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def caps() -> Dict[str, float]:
    return {
        "notional_usd": hl_execution._num_env("HL_TRADE_NOTIONAL_USD",
                                              hl_execution.DEFAULT_NOTIONAL_USD),
        "leverage": hl_execution._num_env("HL_LEVERAGE", hl_execution.DEFAULT_LEVERAGE),
        "max_orders_per_run": int(hl_execution._num_env("HL_MAX_ORDERS_PER_RUN",
                                                        DEFAULT_MAX_ORDERS_PER_RUN)),
        "max_exposure_usd": hl_execution._num_env("HL_MAX_EXPOSURE_USD",
                                                  DEFAULT_MAX_EXPOSURE_USD),
    }


# ── impure: sign + send via the SDK (agent key). Imported lazily. ────────────

def _exchange(env: Optional[str] = None):
    from eth_account import Account                       # lazy: only when sending
    from hyperliquid.exchange import Exchange
    key = (os.getenv("HYPERLIQUID_AGENT_KEY") or "").strip()
    if not key:
        raise RuntimeError("HYPERLIQUID_AGENT_KEY not set")
    wallet = Account.from_key(key)
    return Exchange(wallet, hl_account.base_url(env),
                    account_address=hl_account.account_address())


def send_market_open(coin: str, is_buy: bool, size: float, cloid_str: str,
                     env: Optional[str] = None) -> Any:
    from hyperliquid.utils.signing import Cloid
    return _exchange(env).market_open(coin, is_buy, size,
                                      cloid=Cloid.from_str(cloid_str))


def send_market_close(coin: str, cloid_str: str, env: Optional[str] = None) -> Any:
    from hyperliquid.utils.signing import Cloid
    return _exchange(env).market_close(coin, cloid=Cloid.from_str(cloid_str))


# ── pure: the guarded execution decision ─────────────────────────────────────

def _open_positions(account_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (account_state or {}).get("open_positions") or []


def _current_exposure_usd(account_state: Dict[str, Any]) -> float:
    total = 0.0
    for p in _open_positions(account_state):
        v = p.get("position_value_usd")
        if isinstance(v, (int, float)):
            total += abs(v)
    return total


def open_position(signal: Dict[str, Any], *, account_state: Dict[str, Any],
                  table: Optional[Dict[str, Any]] = None,
                  run_order_count: int = 0,
                  send_fn: Optional[Callable] = None,
                  claim_fn: Optional[Callable] = None,
                  release_fn: Optional[Callable] = None,
                  cfg: Optional[Dict[str, Any]] = None,
                  env: Optional[str] = None) -> Dict[str, Any]:
    """Attempt one live OPEN for a signal, through every guard. Returns a result
    dict and NEVER raises for a business reject. `signal` needs: symbol, entry,
    direction (LONG/SHORT), id, candle_ts.

    Reason codes: DISARMED, MAINNET_NOT_ALLOWED, MAX_ORDERS_PER_RUN,
    <can_place reason>, MAX_EXPOSURE, POSITION_EXISTS, ALREADY_PLACED,
    SEND_FAILED.
    """
    cfg = cfg or caps()
    e = hl_account._env(env)

    st = hl_execution.arm_status()
    if not st["armed"]:
        return {"ok": False, "reason": "DISARMED", "detail": st["reasons"]}
    # Even armed, real money is a separate, explicit gate.
    if e == "mainnet" and not _flag("HL_ALLOW_MAINNET"):
        return {"ok": False, "reason": "MAINNET_NOT_ALLOWED"}

    if run_order_count >= cfg["max_orders_per_run"]:
        return {"ok": False, "reason": "MAX_ORDERS_PER_RUN",
                "max_orders_per_run": cfg["max_orders_per_run"]}

    table = table if table is not None else hl_meta.asset_table(env=env)
    plan = hl_meta.can_place(
        signal.get("symbol"), signal.get("entry"),
        notional_usd=cfg["notional_usd"], leverage=cfg["leverage"],
        free_collateral_usd=account_state.get("account_value_usd"), table=table)
    if not plan.get("ok"):
        return {"ok": False, "reason": plan.get("reason"), "plan": plan}

    coin = plan["coin"]
    # Reconcile: never open a second position on a coin we already hold.
    if any((p.get("coin") or "").upper() == coin for p in _open_positions(account_state)):
        return {"ok": False, "reason": "POSITION_EXISTS", "coin": coin}

    # Exposure cap across the whole account.
    if _current_exposure_usd(account_state) + plan["notional_usd"] > cfg["max_exposure_usd"] + 1e-9:
        return {"ok": False, "reason": "MAX_EXPOSURE", "coin": coin,
                "current_exposure_usd": round(_current_exposure_usd(account_state), 6),
                "max_exposure_usd": cfg["max_exposure_usd"]}

    sig_id, candle_ts = signal.get("id"), signal.get("candle_ts")
    cloid = hl_execution.client_order_id(sig_id, "open", candle_ts)
    claim = claim_fn or hl_execution.claim_order
    if not claim(sig_id, "open", candle_ts):
        return {"ok": False, "reason": "ALREADY_PLACED", "coin": coin, "cloid": cloid}

    is_buy = str(signal.get("direction", "")).upper() == "LONG"
    send = send_fn or send_market_open
    try:
        resp = send(coin, is_buy, plan["size"], cloid, env)
    except Exception as exc:                              # noqa: BLE001
        # Release the claim so a genuine retry can re-attempt (claim-before-send).
        (release_fn or _kv_release)(
            hl_execution.order_ledger_key(sig_id, "open", candle_ts))
        return {"ok": False, "reason": "SEND_FAILED", "coin": coin,
                "cloid": cloid, "error": str(exc)}

    return {"ok": True, "coin": coin, "asset_id": plan["asset_id"],
            "side": "buy" if is_buy else "sell", "size": plan["size"],
            "notional_usd": plan["notional_usd"], "leverage": plan["leverage"],
            "cloid": cloid, "env": e, "response": resp}
