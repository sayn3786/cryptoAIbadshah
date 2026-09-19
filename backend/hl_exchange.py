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
# Market orders fill near the LIVE price, not the signal's entry. Size and check
# exposure against a worst-case fill = live_mid * (1 + this), and reject an entry
# that has drifted more than the max deviation from live (stale / mistyped).
MARKET_SLIPPAGE = 0.05
DEFAULT_MAX_ENTRY_DEVIATION = 0.15


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def caps() -> Dict[str, float]:
    return {
        "notional_usd": hl_execution._num_env("HL_TRADE_NOTIONAL_USD",
                                              hl_execution.DEFAULT_NOTIONAL_USD),
        "leverage": max(1, int(hl_execution._num_env("HL_LEVERAGE",
                                                     hl_execution.DEFAULT_LEVERAGE))),
        "max_orders_per_run": int(hl_execution._num_env("HL_MAX_ORDERS_PER_RUN",
                                                        DEFAULT_MAX_ORDERS_PER_RUN)),
        "max_exposure_usd": hl_execution._num_env("HL_MAX_EXPOSURE_USD",
                                                  DEFAULT_MAX_EXPOSURE_USD),
        "max_entry_deviation": hl_execution._num_env("HL_MAX_ENTRY_DEVIATION",
                                                     DEFAULT_MAX_ENTRY_DEVIATION),
    }


def action_ok(resp: Any):
    """(ok, detail) for a Hyperliquid ACTION response (updateLeverage, etc.).
    The exchange returns application-level rejections inside a 200 as
    status != 'ok', never as an exception."""
    if not isinstance(resp, dict):
        return False, "non-dict response"
    if resp.get("status") != "ok":
        return False, str(resp.get("response") or resp.get("status") or resp)[:200]
    return True, None


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
                     env: Optional[str] = None, leverage: Optional[float] = None) -> Any:
    """Set the coin's leverage to the PLANNED value, then market-open. Without
    the leverage update the order would inherit whatever per-coin leverage the
    account already has (e.g. a leftover 20x), so the realised risk would not
    match what was planned/sized. The leverage update can be REJECTED inside a
    200 (e.g. cross margin on an isolated-only coin) — abort if so, rather than
    place under the old leverage."""
    from hyperliquid.utils.signing import Cloid
    ex = _exchange(env)
    if leverage:
        ok, detail = action_ok(ex.update_leverage(int(leverage), coin, True))
        if not ok:
            raise RuntimeError(f"leverage update rejected: {detail}")
    return ex.market_open(coin, is_buy, size, cloid=Cloid.from_str(cloid_str))


def send_market_close(coin: str, cloid_str: str, env: Optional[str] = None) -> Any:
    from hyperliquid.utils.signing import Cloid
    return _exchange(env).market_close(coin, cloid=Cloid.from_str(cloid_str))


def order_accepted(resp: Any):
    """(accepted, detail) for a Hyperliquid order response. The exchange returns
    order-level rejections — IOC non-fills, insufficient margin — INSIDE a 200
    JSON (status 'ok' with a statuses list carrying an 'error'), never as an
    exception. Treat any non-'ok' status or any per-order 'error' as a rejection
    so the caller can release the claim instead of holding it forever."""
    if not isinstance(resp, dict):
        return False, "non-dict response"
    if resp.get("status") != "ok":
        return False, str(resp.get("status") or resp)[:200]
    data = ((resp.get("response") or {}).get("data")) or {}
    for s in data.get("statuses") or []:
        if isinstance(s, dict) and s.get("error"):
            return False, str(s["error"])[:200]
    return True, None


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
                  mark_px: Optional[float] = None,
                  run_order_count: int = 0,
                  send_fn: Optional[Callable] = None,
                  claim_fn: Optional[Callable] = None,
                  release_fn: Optional[Callable] = None,
                  mark_fn: Optional[Callable] = None,
                  cfg: Optional[Dict[str, Any]] = None,
                  env: Optional[str] = None) -> Dict[str, Any]:
    """Attempt one live OPEN for a signal, through every guard. Returns a result
    dict and NEVER raises for a business reject. `signal` needs: symbol, entry,
    direction (LONG/SHORT), id, candle_ts.

    Sizing and exposure use the LIVE mid (what a market order fills near), not
    the caller's `entry`; `entry` is only a staleness sanity-check. Reason codes:
    DISARMED, MAINNET_NOT_ALLOWED, MAX_ORDERS_PER_RUN, SYMBOL_NOT_ON_HYPERLIQUID,
    NO_MARK_PRICE, STALE_ENTRY, <can_place reason>, POSITION_EXISTS, MAX_EXPOSURE,
    ALREADY_PLACED, SEND_FAILED, SEND_REJECTED.
    """
    cfg = cfg or caps()
    e = hl_account._env(env)

    st = hl_execution.arm_status()
    if not st["armed"]:
        return {"ok": False, "reason": "DISARMED", "detail": st["reasons"]}
    if e == "mainnet" and not _flag("HL_ALLOW_MAINNET"):
        return {"ok": False, "reason": "MAINNET_NOT_ALLOWED"}

    if run_order_count >= cfg["max_orders_per_run"]:
        return {"ok": False, "reason": "MAX_ORDERS_PER_RUN",
                "max_orders_per_run": cfg["max_orders_per_run"]}

    table = table if table is not None else hl_meta.asset_table(env=env)
    coin = hl_meta.resolve_coin(signal.get("symbol"), table)
    if coin is None:
        return {"ok": False, "reason": "SYMBOL_NOT_ON_HYPERLIQUID"}

    # A MARKET order fills at the live price — size & cap against that, not entry.
    mark = mark_px if mark_px is not None else (mark_fn or hl_account.mid_price)(coin, env=env)
    if not mark or mark <= 0:
        return {"ok": False, "reason": "NO_MARK_PRICE", "coin": coin}
    entry = signal.get("entry")
    if entry and abs(float(entry) - mark) / mark > cfg["max_entry_deviation"]:
        return {"ok": False, "reason": "STALE_ENTRY", "coin": coin,
                "entry": float(entry), "mark_px": mark,
                "max_entry_deviation": cfg["max_entry_deviation"]}

    plan = hl_meta.can_place(
        signal.get("symbol"), mark,
        notional_usd=cfg["notional_usd"], leverage=cfg["leverage"],
        free_collateral_usd=account_state.get("account_value_usd"), table=table)
    if not plan.get("ok"):
        return {"ok": False, "reason": plan.get("reason"), "plan": plan}

    if any((p.get("coin") or "").upper() == coin for p in _open_positions(account_state)):
        return {"ok": False, "reason": "POSITION_EXISTS", "coin": coin}

    # Worst-case exposure including slippage on the market fill.
    worst_notional = plan["notional_usd"] * (1 + MARKET_SLIPPAGE)
    if _current_exposure_usd(account_state) + worst_notional > cfg["max_exposure_usd"] + 1e-9:
        return {"ok": False, "reason": "MAX_EXPOSURE", "coin": coin,
                "current_exposure_usd": round(_current_exposure_usd(account_state), 6),
                "worst_case_notional_usd": round(worst_notional, 6),
                "max_exposure_usd": cfg["max_exposure_usd"]}

    sig_id, candle_ts = signal.get("id"), signal.get("candle_ts")
    cloid = hl_execution.client_order_id(sig_id, "open", candle_ts)
    claim = claim_fn or hl_execution.claim_order
    release = release_fn or _kv_release
    if not claim(sig_id, "open", candle_ts):
        return {"ok": False, "reason": "ALREADY_PLACED", "coin": coin, "cloid": cloid}

    is_buy = str(signal.get("direction", "")).upper() == "LONG"
    send = send_fn or send_market_open
    try:
        resp = send(coin, is_buy, plan["size"], cloid, env, plan["leverage"])
    except Exception as exc:                              # noqa: BLE001
        release(hl_execution.order_ledger_key(sig_id, "open", candle_ts))
        return {"ok": False, "reason": "SEND_FAILED", "coin": coin,
                "cloid": cloid, "error": str(exc)}

    accepted, detail = order_accepted(resp)
    if not accepted:
        # The exchange rejected it inside a 200 — free the claim so a real retry
        # can re-attempt, and report the rejection rather than a false success.
        release(hl_execution.order_ledger_key(sig_id, "open", candle_ts))
        return {"ok": False, "reason": "SEND_REJECTED", "coin": coin,
                "cloid": cloid, "detail": detail, "response": resp}

    return {"ok": True, "coin": coin, "asset_id": plan["asset_id"],
            "side": "buy" if is_buy else "sell", "size": plan["size"],
            "mark_px": mark, "notional_usd": plan["notional_usd"],
            "leverage": plan["leverage"], "cloid": cloid, "env": e,
            "response": resp}
