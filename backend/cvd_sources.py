"""CVD data fetchers for multiple exchange sources."""
import os
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

from indicators import calculate_cvd

TIMEOUT = 15
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 CryptoBadshah/2.0"})


def _get(url: str, params: dict = None, headers: dict = None) -> dict | list:
    r = _s.get(url, params=params or {}, headers=headers or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Symbol maps ───────────────────────────────────────────────────────────────

_OKX_CCY = {
    "BTCUSDT": "BTC",   "ETHUSDT": "ETH",  "LINKUSDT": "LINK",
    "TAOUSDT": "TAO",   "HYPEUSDT": "HYPE", "ONDOUSDT": "ONDO",
}
_KUCOIN = {
    "BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT",  "LINKUSDT": "LINK-USDT",
    "TAOUSDT": "TAO-USDT", "HYPEUSDT": "HYPE-USDT", "ONDOUSDT": "ONDO-USDT",
}
_GATE = {
    "BTCUSDT": "BTC_USDT", "ETHUSDT": "ETH_USDT",  "LINKUSDT": "LINK_USDT",
    "TAOUSDT": "TAO_USDT", "HYPEUSDT": "HYPE_USDT", "ONDOUSDT": "ONDO_USDT",
}
_LBANK = {
    "BTCUSDT": "btc_usdt", "ETHUSDT": "eth_usdt",  "LINKUSDT": "link_usdt",
    "TAOUSDT": "tao_usdt", "HYPEUSDT": "hype_usdt", "ONDOUSDT": "ondo_usdt",
}
_CG_IDS = {
    "BTCUSDT": "bitcoin",    "ETHUSDT": "ethereum",    "LINKUSDT": "chainlink",
    "TAOUSDT": "bittensor",  "HYPEUSDT": "hyperliquid", "ONDOUSDT": "ondo-finance",
}
_CMC_IDS = {
    "BTCUSDT": 1, "ETHUSDT": 1027, "LINKUSDT": 1975,
    "TAOUSDT": 22974, "HYPEUSDT": 32196, "ONDOUSDT": 21159,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_binance_kline(k: list) -> Dict:
    return {
        "timestamp":        int(k[0]),
        "open":             float(k[1]),
        "high":             float(k[2]),
        "low":              float(k[3]),
        "close":            float(k[4]),
        "volume":           float(k[5]),
        "taker_buy_volume": float(k[9]),
    }


# ── Binance ───────────────────────────────────────────────────────────────────

def fetch_binance_spot_cvd(symbol: str, interval: str, limit: int) -> Optional[Dict]:
    try:
        data = _get("https://api.binance.com/api/v3/klines",
                    {"symbol": symbol, "interval": interval, "limit": limit})
        return calculate_cvd([_parse_binance_kline(k) for k in data], "spot")
    except Exception:
        return None


def fetch_binance_futures_cvd(symbol: str, interval: str, limit: int) -> Optional[Dict]:
    try:
        data = _get("https://fapi.binance.com/fapi/v1/klines",
                    {"symbol": symbol, "interval": interval, "limit": limit})
        return calculate_cvd([_parse_binance_kline(k) for k in data], "futures")
    except Exception:
        return None


# ── MEXC ──────────────────────────────────────────────────────────────────────

def fetch_mexc_spot_cvd(symbol: str, interval: str, limit: int) -> Optional[Dict]:
    """MEXC spot API is Binance-compatible; index 9 = taker_buy_base_asset_volume."""
    try:
        data = _get("https://api.mexc.com/api/v3/klines",
                    {"symbol": symbol, "interval": interval, "limit": limit})
        return calculate_cvd([_parse_binance_kline(k) for k in data], "spot")
    except Exception:
        return None


def fetch_mexc_futures_cvd(symbol: str, interval: str, limit: int) -> Optional[Dict]:
    mexc_sym = symbol.replace("USDT", "_USDT")
    iv_map   = {"1w": "Week1", "1M": "Month1"}
    try:
        data = _get(f"https://futures.mexc.com/api/v1/contract/kline/{mexc_sym}",
                    {"interval": iv_map.get(interval, "Week1"), "limit": limit})
        d = data.get("data", {})
        times    = d.get("time",   [])
        opens    = d.get("open",   [])
        highs    = d.get("high",   [])
        lows     = d.get("low",    [])
        closes   = d.get("close",  [])
        vols     = d.get("vol",    [])
        buy_vols = d.get("buyVol", [])
        candles = []
        for i, ts in enumerate(times):
            total = float(vols[i])     if i < len(vols)     else 0.0
            buy   = float(buy_vols[i]) if i < len(buy_vols) else None
            candles.append({
                "timestamp":        int(ts) * 1000,
                "open":             float(opens[i])  if i < len(opens)  else 0,
                "high":             float(highs[i])  if i < len(highs)  else 0,
                "low":              float(lows[i])   if i < len(lows)   else 0,
                "close":            float(closes[i]) if i < len(closes) else 0,
                "volume":           total,
                "taker_buy_volume": buy,
            })
        return calculate_cvd(candles, "futures") if candles else None
    except Exception:
        return None


# ── OKX ───────────────────────────────────────────────────────────────────────

def _okx_taker_cvd(symbol: str, inst_type: str, period: str, label: str) -> Optional[Dict]:
    ccy = _OKX_CCY.get(symbol)
    if not ccy:
        return None
    try:
        data = _get("https://www.okx.com/api/v5/rubik/stat/taker-volume",
                    {"ccy": ccy, "instType": inst_type, "period": period, "limit": "100"})
        rows = data.get("data", []) if isinstance(data, dict) else []
        if not rows:
            return None
        rows = list(reversed(rows))  # chronological order
        cvd, series = 0.0, []
        for row in rows:
            ts    = int(row[0])
            sell  = float(row[1])
            buy   = float(row[2])
            delta = buy - sell
            cvd  += delta
            series.append({"timestamp": ts, "cvd": round(cvd, 4), "delta": round(delta, 4)})
        recent = [s["cvd"] for s in series[-5:]]
        pct    = (recent[-1] - recent[0]) / (abs(recent[0]) + 1e-9)
        trend  = "bullish" if pct > 0.01 else "bearish" if pct < -0.01 else "neutral"
        return {"current": round(cvd, 2), "trend": trend,
                "series": series[-30:], "label": label, "source": "okx"}
    except Exception:
        return None


def fetch_okx_spot_cvd(symbol: str, interval: str, limit: int) -> Optional[Dict]:
    period = "1W" if interval in ("1w", "1W") else "1M"
    return _okx_taker_cvd(symbol, "SPOT", period, "spot")


def fetch_okx_futures_cvd(symbol: str, interval: str, limit: int) -> Optional[Dict]:
    period = "1W" if interval in ("1w", "1W") else "1M"
    return _okx_taker_cvd(symbol, "CONTRACTS", period, "futures")


# ── KuCoin ────────────────────────────────────────────────────────────────────

def fetch_kucoin_cvd(symbol: str, interval: str, limit: int, label: str) -> Optional[Dict]:
    pair = _KUCOIN.get(symbol)
    if not pair:
        return None
    try:
        data = _get("https://api.kucoin.com/api/v1/market/candles",
                    {"type": "1week", "symbol": pair})
        raw = (data.get("data") or []) if isinstance(data, dict) else []
        candles = []
        for k in reversed(raw[-limit:]):
            vol = float(k[5])
            candles.append({
                "timestamp":        int(k[0]) * 1000,
                "open":             float(k[1]),
                "high":             float(k[3]),
                "low":              float(k[4]),
                "close":            float(k[2]),
                "volume":           vol,
                "taker_buy_volume": None,
            })
        return calculate_cvd(candles, label) if candles else None
    except Exception:
        return None


# ── Gate.io ───────────────────────────────────────────────────────────────────

def fetch_gate_cvd(symbol: str, interval: str, limit: int, label: str) -> Optional[Dict]:
    pair = _GATE.get(symbol)
    if not pair:
        return None
    try:
        data = _get("https://api.gateio.ws/api/v4/spot/candlesticks",
                    {"currency_pair": pair, "interval": "1w", "limit": limit})
        candles = []
        for k in data:
            candles.append({
                "timestamp":        int(k[0]) * 1000,
                "open":             float(k[5]),
                "high":             float(k[3]),
                "low":              float(k[4]),
                "close":            float(k[2]),
                "volume":           float(k[1]),
                "taker_buy_volume": None,
            })
        return calculate_cvd(candles, label) if candles else None
    except Exception:
        return None


# ── LBank ─────────────────────────────────────────────────────────────────────

def fetch_lbank_cvd(symbol: str, interval: str, limit: int, label: str) -> Optional[Dict]:
    pair = _LBANK.get(symbol)
    if not pair:
        return None
    try:
        data = _get("https://api.lbank.com/v2/kline.do",
                    {"symbol": pair, "size": min(limit, 100), "type": "week1"})
        rows = data.get("data", []) if isinstance(data, dict) else []
        candles = []
        for k in rows:
            candles.append({
                "timestamp":        int(k[0]),
                "open":             float(k[1]),
                "high":             float(k[2]),
                "low":              float(k[3]),
                "close":            float(k[4]),
                "volume":           float(k[5]),
                "taker_buy_volume": None,
            })
        return calculate_cvd(candles, label) if candles else None
    except Exception:
        return None


# ── CoinGecko ─────────────────────────────────────────────────────────────────

def fetch_coingecko_cvd(symbol: str, interval: str, limit: int, label: str) -> Optional[Dict]:
    cg_id = _CG_IDS.get(symbol)
    if not cg_id:
        return None
    try:
        days = min(limit * 7 + 60, 730)
        data = _get(f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
                    {"vs_currency": "usd", "days": str(days), "interval": "daily"})
        prices  = data.get("prices",         [])
        volumes = data.get("total_volumes",  [])
        if not prices:
            return None

        vol_map: Dict[str, float] = {}
        for ts, vol in volumes:
            dt  = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            key = f"{dt.isocalendar()[0]}-{dt.isocalendar()[1]:02d}"
            vol_map[key] = vol_map.get(key, 0.0) + vol

        weeks: Dict[str, dict] = {}
        week_order: List[str] = []
        for ts, price in prices:
            dt  = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            iso = dt.isocalendar()
            key = f"{iso[0]}-{iso[1]:02d}"
            if key not in weeks:
                monday = (dt - timedelta(days=dt.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                weeks[key] = {"timestamp": int(monday.timestamp() * 1000),
                              "open": price, "high": price, "low": price, "close": price}
                week_order.append(key)
            else:
                weeks[key]["high"]  = max(weeks[key]["high"],  price)
                weeks[key]["low"]   = min(weeks[key]["low"],   price)
                weeks[key]["close"] = price

        candles = []
        for key in week_order[-limit:]:
            w   = weeks[key]
            vol = vol_map.get(key, w["close"] * 500)
            candles.append({
                "timestamp": w["timestamp"], "open": round(w["open"], 8),
                "high": round(w["high"], 8), "low": round(w["low"], 8),
                "close": round(w["close"], 8), "volume": round(vol, 2),
                "taker_buy_volume": None,
            })
        return calculate_cvd(candles, label) if candles else None
    except Exception:
        return None


# ── CoinMarketCap ─────────────────────────────────────────────────────────────

def fetch_coinmarketcap_cvd(symbol: str, interval: str, limit: int, label: str) -> Optional[Dict]:
    cmc_id  = _CMC_IDS.get(symbol)
    api_key = os.getenv("CMC_API_KEY", "")
    if not cmc_id or not api_key:
        return None
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(weeks=limit)
        data  = _get(
            "https://pro-api.coinmarketcap.com/v2/cryptocurrency/ohlcv/historical",
            {"id": cmc_id, "time_start": start.strftime("%Y-%m-%d"),
             "time_end": end.strftime("%Y-%m-%d"), "interval": "weekly", "count": limit},
            headers={"X-CMC_PRO_API_KEY": api_key},
        )
        quotes = (data.get("data", {}).get("quotes", [])
                  if isinstance(data, dict) else [])
        candles = []
        for q in quotes:
            ohlcv  = q.get("quote", {}).get("USD", {})
            ts_str = q.get("time_open", "")
            ts = int(datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")).timestamp() * 1000) if ts_str else 0
            candles.append({
                "timestamp":        ts,
                "open":             float(ohlcv.get("open",   0)),
                "high":             float(ohlcv.get("high",   0)),
                "low":              float(ohlcv.get("low",    0)),
                "close":            float(ohlcv.get("close",  0)),
                "volume":           float(ohlcv.get("volume", 0)),
                "taker_buy_volume": None,
            })
        return calculate_cvd(candles, label) if candles else None
    except Exception:
        return None


# ── CoinGlass ─────────────────────────────────────────────────────────────────

def fetch_coinglass_cvd(symbol: str, cg_client) -> Optional[Dict]:
    if not cg_client or not cg_client.enabled:
        return None
    return cg_client.get_aggregated_cvd(symbol)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def fetch_cvd_from_source(
    symbol: str,
    source: str,
    cvd_type: str,
    interval: str,
    limit: int,
    cg_client=None,
) -> Optional[Dict]:
    label = cvd_type  # "spot" or "futures"

    dispatch = {
        "binance":       (fetch_binance_futures_cvd if cvd_type == "futures" else fetch_binance_spot_cvd,
                          [symbol, interval, limit]),
        "mexc":          (fetch_mexc_futures_cvd    if cvd_type == "futures" else fetch_mexc_spot_cvd,
                          [symbol, interval, limit]),
        "okx":           (fetch_okx_futures_cvd     if cvd_type == "futures" else fetch_okx_spot_cvd,
                          [symbol, interval, limit]),
        "kucoin":        (fetch_kucoin_cvd,    [symbol, interval, limit, label]),
        "gateio":        (fetch_gate_cvd,      [symbol, interval, limit, label]),
        "lbank":         (fetch_lbank_cvd,     [symbol, interval, limit, label]),
        "coingecko":     (fetch_coingecko_cvd, [symbol, interval, limit, label]),
        "coinmarketcap": (fetch_coinmarketcap_cvd, [symbol, interval, limit, label]),
        "coinglass":     (fetch_coinglass_cvd, [symbol, cg_client]),
    }

    entry = dispatch.get(source)
    if not entry:
        return None
    fn, args = entry
    result = fn(*args)
    if result:
        result["source"] = source
    return result
