"""
Smart-money positioning from Hyperliquid — where tracked whale wallets sit.

Hyperliquid exposes any address's open perpetual positions through a free, public
info endpoint (no key). We read a WATCHLIST of wallets, pull their BTC/ETH perp
positions, and aggregate them into one net-long/short bias per coin — the "smart
money is X% long BTC, avg entry $Y" read behind the Hyperdash-style trackers.

Reporting only. Nothing here feeds the signal score; it is context, like the
Fear & Greed or ETF-flow panels. Degrades to None on any network/parse problem
and when the watchlist is empty, so it can never break an analysis request.

Watchlist:
  * ``HYPERLIQUID_WATCHLIST`` env — comma-separated ``0xaddr:Label`` (label
    optional). This is the primary, no-deploy way to change who is tracked.
  * ``DEFAULT_WHALES`` below — a code fallback. Left EMPTY on purpose: an address
    that cannot be verified would silently show the wrong wallet's book as
    "smart money", so real addresses are added deliberately, not guessed.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence, Tuple

import requests

HL_API = "https://api.hyperliquid.xyz/info"
TIMEOUT = 8
_TTL = 300                          # 5 min — whale books do not move every second

# (address, label). Populate with VERIFIED Hyperliquid wallet addresses, or set
# the HYPERLIQUID_WATCHLIST env instead. Empty by default (see module docstring).
DEFAULT_WHALES: List[Tuple[str, str]] = []

# Net notional beyond this share of gross flips the bias off "neutral".
_BIAS_THRESHOLD_PCT = 15.0

_cache: Dict[str, tuple] = {}


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _short(addr: str) -> str:
    a = addr or ""
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def watchlist() -> List[Tuple[str, str]]:
    """(address, label) pairs from the env override, else DEFAULT_WHALES."""
    raw = os.getenv("HYPERLIQUID_WATCHLIST", "").strip()
    if not raw:
        return list(DEFAULT_WHALES)
    out: List[Tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            addr, label = part.split(":", 1)
            addr, label = addr.strip(), label.strip()
        else:
            addr, label = part, ""
        if addr:
            out.append((addr, label or _short(addr)))
    return out


def _fetch_state(addr: str, *, timeout: int = TIMEOUT) -> Optional[Dict]:
    try:
        r = requests.post(HL_API, json={"type": "clearinghouseState", "user": addr},
                          timeout=timeout)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _positions(state: Optional[Dict], coins: Sequence[str]) -> List[Dict]:
    """Open perp positions in ``coins`` from one clearinghouse state."""
    out: List[Dict] = []
    for ap in (state or {}).get("assetPositions", []) or []:
        p = (ap or {}).get("position") or {}
        coin = p.get("coin")
        szi = _f(p.get("szi"))
        if coin not in coins or szi in (None, 0.0):
            continue
        out.append({
            "coin": coin,
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "notional": _f(p.get("positionValue")),
            "entry": _f(p.get("entryPx")),
            "liq": _f(p.get("liquidationPx")),
            "upnl": _f(p.get("unrealizedPnl")),
            "leverage": _f((p.get("leverage") or {}).get("value")),
        })
    return out


def aggregate(wallet_positions: List[Tuple[str, List[Dict]]],
              coins: Sequence[str]) -> Dict[str, Dict]:
    """
    Per-coin net long/short aggregate from ``[(label, [positions]), ...]``. Pure.
    """
    result: Dict[str, Dict] = {}
    for coin in coins:
        long_notional = short_notional = 0.0
        long_entry_w = short_entry_w = 0.0
        long_wallets = short_wallets = 0
        total_upnl = 0.0
        rows: List[Dict] = []
        for label, positions in wallet_positions:
            for p in positions:
                if p.get("coin") != coin:
                    continue
                notl = p.get("notional") or 0.0
                total_upnl += p.get("upnl") or 0.0
                if p["side"] == "long":
                    long_notional += notl
                    long_wallets += 1
                    if p.get("entry") is not None:
                        long_entry_w += p["entry"] * notl
                else:
                    short_notional += notl
                    short_wallets += 1
                    if p.get("entry") is not None:
                        short_entry_w += p["entry"] * notl
                rows.append({"wallet": label, **p})
        gross = long_notional + short_notional
        if gross <= 0:
            result[coin] = {"has_positions": False, "long_wallets": 0,
                            "short_wallets": 0}
            continue
        net = long_notional - short_notional
        net_pct = round(net / gross * 100, 1)
        bias = ("long" if net_pct >= _BIAS_THRESHOLD_PCT
                else "short" if net_pct <= -_BIAS_THRESHOLD_PCT else "neutral")
        result[coin] = {
            "has_positions": True,
            "bias": bias,
            "net_long_pct": net_pct,                 # −100 (all short) … +100 (all long)
            "long_notional": round(long_notional),
            "short_notional": round(short_notional),
            "net_notional": round(net),
            "long_wallets": long_wallets,
            "short_wallets": short_wallets,
            "avg_long_entry": round(long_entry_w / long_notional, 2) if long_notional else None,
            "avg_short_entry": round(short_entry_w / short_notional, 2) if short_notional else None,
            "total_upnl": round(total_upnl),
            "positions": sorted(rows, key=lambda r: (r.get("notional") or 0), reverse=True)[:12],
        }
    return result


def get_smart_money(coins: Sequence[str] = ("BTC", "ETH")) -> Optional[Dict]:
    """
    Aggregated whale positioning for ``coins``, or None when the watchlist is
    empty or nothing could be fetched. Cached ``_TTL`` seconds.
    """
    wl = watchlist()
    if not wl:
        return None
    ck = "sm:" + ",".join(a for a, _l in wl) + "|" + ",".join(coins)
    cached = _cache.get(ck)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]

    with ThreadPoolExecutor(max_workers=min(8, len(wl))) as ex:
        states = list(ex.map(lambda al: (al[1], _fetch_state(al[0])), wl))
    ok = [(label, st) for label, st in states if st is not None]
    if not ok:
        return None
    wallet_positions = [(label, _positions(st, coins)) for label, st in ok]
    out = {
        "coins": aggregate(wallet_positions, coins),
        "wallets_tracked": len(wl),
        "wallets_ok": len(ok),
        "source": "hyperliquid",
    }
    _cache[ck] = (out, time.time())
    return out


def get_smart_money_for(symbol: str) -> Optional[Dict]:
    """The single-coin aggregate for a symbol (BTC/ETH), or None."""
    if symbol not in ("BTC", "ETH"):
        return None
    data = get_smart_money(("BTC", "ETH"))
    if not data:
        return None
    coin = (data.get("coins") or {}).get(symbol)
    if not coin or not coin.get("has_positions"):
        return None
    return {**coin, "wallets_tracked": data.get("wallets_tracked"),
            "wallets_ok": data.get("wallets_ok"), "source": "hyperliquid"}
