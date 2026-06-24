"""
Whale Alert — on-chain large transfer detection.

Fetches large transfers TO exchanges (CEX deposits = potential sell pressure).
Free tier at whale-alert.io — no credit card required, 10 req/min.
Requires WHALE_ALERT_API_KEY env var.

Logic:
  transfer to exchange = whale depositing to sell → bearish signal
  transfer from exchange to unknown = withdrawal (HODLing) → not tracked here
"""
import os
import time
import threading
import urllib.request
import urllib.parse
import json
from typing import Dict, List

_BASE   = "https://api.whale-alert.io/v1"
_CACHE: Dict = {}
_LOCK   = threading.Lock()
_TTL    = 300   # 5-min cache — whale alert free tier is 10 req/min

# Whale Alert blockchain identifiers for each trading pair
_CHAIN_MAP = {
    "BTCUSDT":    "bitcoin",
    "ETHUSDT":    "ethereum",
    "XRPUSDT":    "ripple",
    "SOLUSDT":    "solana",
    "BNBUSDT":    "ethereum",   # BEP-20 wraps exist; native BNB on BSC
    "ADAUSDT":    "cardano",
    "TRXUSDT":    "tron",
    "XLMUSDT":    "stellar",
    "AVAXUSDT":   "avalanche",
    "HBARUSDT":   "hedera",
    "TONUSDT":    "ton",
    "LINKUSDT":   "ethereum",
    "AAVEUSDT":   "ethereum",
    "INJUSDT":    "ethereum",
    "FETUSDT":    "ethereum",
    "ONDOUSDT":   "ethereum",
    "RENDERUSDT": "ethereum",
    "BLURUSDT":   "ethereum",
}

# ERC-20 token symbols on shared chains (to filter within ethereum results)
_ERC20_SYMBOLS = {
    "LINKUSDT": "link", "AAVEUSDT": "aave", "INJUSDT": "inj",
    "FETUSDT": "fet", "ONDOUSDT": "ondo", "RENDERUSDT": "rndr",
    "BLURUSDT": "blur",
}

_MIN_USD = 500_000   # $500k minimum — Whale Alert free tier floor


def get_whale_sells(symbol: str) -> Dict:
    """
    Fetch large on-chain transfers TO exchanges for the given trading pair.

    Returns:
      enabled       bool  — False when no API key configured
      flows         list  — transfers: from_entity, to_entity, amount, usd_value, ago_min
      total_usd     int   — combined USD deposited to exchanges in last 1h
      sell_pressure str   — "high" / "medium" / "low" / "none"
      signal_pts    int   — negative score adjustment for signals.py (-15 to 0)
      error         str   — set when API call failed
    """
    api_key = os.getenv("WHALE_ALERT_API_KEY", "").strip()
    if not api_key:
        return {"enabled": False}

    chain = _CHAIN_MAP.get(symbol)
    if not chain:
        return {"enabled": True, "flows": [], "sell_pressure": "none",
                "signal_pts": 0, "total_usd": 0}

    with _LOCK:
        cached = _CACHE.get(symbol)
        if cached and time.time() - cached["ts"] < _TTL:
            return cached["data"]

    result = _fetch(symbol, chain, api_key)

    with _LOCK:
        _CACHE[symbol] = {"ts": time.time(), "data": result}
    return result


def _fetch(symbol: str, chain: str, api_key: str) -> Dict:
    erc20_sym = _ERC20_SYMBOLS.get(symbol)   # None for native tokens
    now_s     = time.time()
    start_ts  = int(now_s - 3600)            # last 1 hour

    try:
        params = {
            "api_key":   api_key,
            "min_value": _MIN_USD,
            "start":     start_ts,
            "limit":     100,
        }
        if chain not in ("ethereum",):
            # Non-Ethereum chains: filter by blockchain directly
            params["blockchain"] = chain

        url = f"{_BASE}/transactions?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoSTARS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read())

        if raw.get("result") != "success":
            raise ValueError(raw.get("message", "API error"))

        transactions = raw.get("transactions", [])

        flows: List[Dict] = []
        total_usd = 0.0

        for t in transactions:
            # Filter: only transfers TO a known exchange
            to_info   = t.get("to") or {}
            from_info = t.get("from") or {}
            to_type   = to_info.get("owner_type", "")
            if to_type != "exchange":
                continue

            # For shared chains (ethereum) filter by token symbol
            if erc20_sym:
                t_sym = (t.get("symbol") or "").lower()
                if t_sym != erc20_sym:
                    continue

            usd = float(t.get("amount_usd") or 0)
            if usd < _MIN_USD:
                continue

            from_owner = from_info.get("owner") or from_info.get("address", "")[:12] + "…"
            to_owner   = to_info.get("owner", "Exchange")
            token_sym  = (t.get("symbol") or "").upper()
            amount     = float(t.get("amount") or 0)
            ts_raw     = float(t.get("timestamp") or 0)
            ago_min    = round((now_s - ts_raw) / 60) if ts_raw else None

            flows.append({
                "from_entity": from_owner.title(),
                "to_entity":   to_owner.title(),
                "symbol":      token_sym,
                "amount":      amount,
                "usd_value":   usd,
                "ago_min":     ago_min,
                "tx_hash":     (t.get("hash") or "")[:16],
                "blockchain":  t.get("blockchain", chain),
            })
            total_usd += usd

        flows.sort(key=lambda x: x["usd_value"], reverse=True)

        # Classify sell pressure
        if total_usd >= 50_000_000:
            pressure, pts = "high",   -15
        elif total_usd >= 10_000_000:
            pressure, pts = "medium", -8
        elif total_usd >= 1_000_000:
            pressure, pts = "low",    -3
        else:
            pressure, pts = "none",    0

        return {
            "enabled":       True,
            "flows":         flows[:10],
            "total_usd":     int(total_usd),
            "sell_pressure": pressure,
            "signal_pts":    pts,
            "window_hours":  1,
            "min_usd":       _MIN_USD,
        }

    except Exception as e:
        return {
            "enabled":       True,
            "flows":         [],
            "total_usd":     0,
            "sell_pressure": "none",
            "signal_pts":    0,
            "error":         str(e)[:160],
        }
