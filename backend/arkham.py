"""
Arkham Intelligence — on-chain whale sell detection.

Fetches large transfers TO exchanges (CEX deposits = potential sell pressure).
Requires ARKHAM_API_KEY env var (free tier at arkhamintelligence.com).
Without a key, all calls return {"enabled": False} and have zero impact.

Cache: 5 minutes — Arkham data is near-real-time on-chain.
"""
import os
import time
import threading
import urllib.request
import urllib.parse
import json
from typing import Dict, List

_BASE = "https://api.arkhamintelligence.com"
_CACHE: Dict = {}
_LOCK = threading.Lock()
_TTL = 300  # 5 min

# Chain identifiers used by Arkham for each trading pair
_CHAIN_MAP = {
    "BTCUSDT":    "bitcoin",
    "ETHUSDT":    "ethereum",
    "SOLUSDT":    "solana",
    "BNBUSDT":    "bsc",
    "XRPUSDT":    "ripple",
    "ADAUSDT":    "cardano",
    "AVAXUSDT":   "avalanche",
    "TRXUSDT":    "tron",
    "TONUSDT":    "ton",
    "XLMUSDT":    "stellar",
    "HBARUSDT":   "hedera",
    # ERC-20 tokens on Ethereum
    "LINKUSDT":   "ethereum",
    "INJUSDT":    "ethereum",
    "FETUSDT":    "ethereum",
    "AAVEUSDT":   "ethereum",
    "ONDOUSDT":   "ethereum",
    "RENDERUSDT": "ethereum",
    "BLURUSDT":   "ethereum",
    # Native symbol used to filter within a shared chain (e.g. Ethereum ERC-20s)
}

# Arkham token symbols that differ from the trading pair prefix
_SYM_OVERRIDE = {
    "RENDERUSDT": "RNDR",
}

_MIN_USD = 500_000  # $500k — "big" threshold


def _trading_sym(symbol: str) -> str:
    """Return the token symbol Arkham uses (strips USDT, applies overrides)."""
    return _SYM_OVERRIDE.get(symbol, symbol.replace("USDT", ""))


def get_whale_sells(symbol: str) -> Dict:
    """
    Fetch large on-chain transfers TO exchanges for the given trading pair.

    Returns:
      enabled      bool   — False when no API key is configured
      flows        list   — top transfers (from_entity, to_entity, amount, usd_value, ago_min)
      total_usd    int    — combined USD volume of exchange deposits in last 1h
      sell_pressure str   — "high" / "medium" / "low" / "none"
      signal_pts   int    — negative score adjustment (-15 to 0) for signals.py
      error        str    — set when the API call failed (data still returns "none" pressure)
    """
    api_key = os.getenv("ARKHAM_API_KEY", "").strip()
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
    tok_sym = _trading_sym(symbol)
    try:
        params = urllib.parse.urlencode({
            "base":      chain,
            "usdGte":    _MIN_USD,
            "timeLast":  "1h",
            "toType":    "cex",   # destination = centralized exchange (deposit = sell)
            "limit":     25,
        })
        url = f"{_BASE}/transfers?{params}"
        req = urllib.request.Request(url, headers={"API-Key": api_key,
                                                    "User-Agent": "CryptoSTARS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read())

        transfers = raw.get("transfers", [])

        flows: List[Dict] = []
        total_usd = 0.0
        now_s = time.time()

        for t in transfers:
            # Filter to our specific token on shared chains (e.g. ETH vs ERC-20s)
            t_sym = (t.get("tokenSymbol") or t.get("asset") or "").upper()
            if chain not in ("bitcoin", "solana", "ripple", "cardano",
                             "ton", "stellar", "hedera"):
                # Multi-token chain — must match symbol
                if t_sym and t_sym != tok_sym:
                    continue

            usd = float(t.get("usdValue") or t.get("usdAmount") or 0)
            if usd < _MIN_USD:
                continue

            # Arkham response shape: fromAddress.arkhamEntity.name
            from_addr = t.get("fromAddress") or {}
            to_addr   = t.get("toAddress")   or {}
            from_ent  = (from_addr.get("arkhamEntity") or {}).get("name", "") or \
                        from_addr.get("name", "Unknown")
            to_ent    = (to_addr.get("arkhamEntity")   or {}).get("name", "") or \
                        to_addr.get("name", "Exchange")

            # timestamp: Arkham returns Unix seconds or ISO string
            ts_raw = t.get("blockTimestamp") or t.get("timestamp") or 0
            if isinstance(ts_raw, str):
                # ISO 8601 → unix seconds (basic parse)
                try:
                    import datetime
                    ts_raw = datetime.datetime.fromisoformat(
                        ts_raw.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts_raw = 0
            ago_min = round((now_s - float(ts_raw)) / 60) if ts_raw else None

            flows.append({
                "from_entity": from_ent or "Unknown",
                "to_entity":   to_ent   or "Exchange",
                "symbol":      t_sym or tok_sym,
                "amount":      float(t.get("unitValue") or t.get("tokenAmount") or 0),
                "usd_value":   usd,
                "ago_min":     ago_min,
                "tx_hash":     (t.get("transactionHash") or "")[:16],
            })
            total_usd += usd

        # Sort by USD value descending
        flows.sort(key=lambda x: x["usd_value"], reverse=True)

        # Sell pressure thresholds
        if total_usd >= 50_000_000:       # $50M+ → high pressure
            pressure = "high"
            pts = -15
        elif total_usd >= 10_000_000:     # $10M+ → medium
            pressure = "medium"
            pts = -8
        elif total_usd >= 1_000_000:      # $1M+ → low
            pressure = "low"
            pts = -3
        else:
            pressure = "none"
            pts = 0

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
