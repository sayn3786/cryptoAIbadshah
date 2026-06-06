"""
Market data client — tries Binance → CoinGecko → Kraken → Gate.io → demo.
Pure Python (requests), no compilation required.
"""
import time
import requests
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

SPOT_BASE    = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
CG_BASE      = "https://api.coingecko.com/api/v3"
KRAKEN_BASE  = "https://api.kraken.com/0/public"
GATE_BASE    = "https://api.gateio.ws/api/v4"
KUCOIN_BASE  = "https://api.kucoin.com"
OKX_BASE     = "https://www.okx.com"
BYBIT_BASE   = "https://api.bybit.com"
TIMEOUT      = 15
BINANCE_RETRIES = int(os.getenv("BINANCE_RETRIES", "1"))

# Approximate market caps (USD) used as fallback when CoinGecko is unavailable.
# Values are intentionally rounded — only need to be accurate enough for tier bucketing.
# Update periodically; wrong by 2× still gives correct tier in most cases.
MCAP_FALLBACK = {
    "BTCUSDT":    1_400_000_000_000,
    "ETHUSDT":      220_000_000_000,
    "BNBUSDT":       80_000_000_000,
    "XRPUSDT":      120_000_000_000,
    "SOLUSDT":       70_000_000_000,
    "ADAUSDT":       28_000_000_000,
    "TRXUSDT":       20_000_000_000,
    "AVAXUSDT":      15_000_000_000,
    "XLMUSDT":       12_000_000_000,
    "SUIUSDT":       12_000_000_000,
    "TONUSDT":       22_000_000_000,
    "LINKUSDT":      10_000_000_000,
    "HBARUSDT":       8_000_000_000,
    "KASUSDT":        4_000_000_000,
    "RENDERUSDT":     4_000_000_000,
    "HYPEUSDT":       5_000_000_000,
    "TAOUSDT":        3_000_000_000,
    "ONDOUSDT":       3_000_000_000,
    "AAVEUSDT":       3_000_000_000,
    "XMRUSDT":        3_000_000_000,
    "INJUSDT":        2_500_000_000,
    "QNTUSDT":        2_000_000_000,
    "ALGOUSDT":       1_800_000_000,
    "FETUSDT":        1_200_000_000,
    "ZECUSDT":          300_000_000,
    "BLURUSDT":         200_000_000,
}

# CoinGecko IDs
CG_IDS = {
    "BTCUSDT":  "bitcoin",
    "ETHUSDT":  "ethereum",
    "LINKUSDT": "chainlink",
    "SUIUSDT":  "sui",
    "TAOUSDT":  "bittensor",
    "HYPEUSDT": "hyperliquid",
    "KASUSDT":  "kaspa",
    "ALGOUSDT": "algorand",
    "XMRUSDT":  "monero",
    "XRPUSDT":  "ripple",
    "TONUSDT":  "the-open-network",
    "SOLUSDT":  "solana",
    "ONDOUSDT": "ondo-finance",
    "AAVEUSDT": "aave",
    "RENDERUSDT": "render-token",
    "BNBUSDT":    "binancecoin",
    "BLURUSDT":   "blur",
    "ZECUSDT":    "zcash",
    "TRXUSDT":    "tron",
    "ADAUSDT":    "cardano",
    "XLMUSDT":    "stellar",
    "AVAXUSDT":   "avalanche-2",
    "HBARUSDT":   "hedera-hashgraph",
    "QNTUSDT":    "quant-network",
    "INJUSDT":    "injective-protocol",
    "FETUSDT":    "fetch-ai",
}

# Kraken pairs (weekly interval = 10080 min)
# Note: XMR is delisted from Binance/OKX — Kraken is the primary fallback
KRAKEN_PAIRS = {
    "BTCUSDT":  "XBTUSD",
    "ETHUSDT":  "ETHUSD",
    "LINKUSDT": "LINKUSD",
    "SUIUSDT":  "SUIUSD",
    "TAOUSDT":  "TAOUSD",
    "ALGOUSDT": "ALGOUSD",
    "XMRUSDT":  "XMRUSD",
    "XRPUSDT":  "XRPUSD",
    "SOLUSDT":  "SOLUSD",
    "AAVEUSDT": "AAVEUSD",
    "RENDERUSDT": "RENDERUSD",
    "BNBUSDT":    "BNBUSD",
    "BLURUSDT":   "BLURUSD",
    "ZECUSDT":    "ZECUSD",
    "TRXUSDT":    "TRXUSD",
    "ADAUSDT":    "ADAUSD",
    "XLMUSDT":    "XLMUSD",
    "AVAXUSDT":   "AVAXUSD",
    "HBARUSDT":   "HBARUSD",
    "QNTUSDT":    "QNTUSD",
    "INJUSDT":    "INJUSD",
    "FETUSDT":    "FETUSD",
}

# Gate.io currency pairs
GATE_PAIRS = {
    "BTCUSDT":  "BTC_USDT",
    "ETHUSDT":  "ETH_USDT",
    "LINKUSDT": "LINK_USDT",
    "SUIUSDT":  "SUI_USDT",
    "TAOUSDT":  "TAO_USDT",
    "HYPEUSDT": "HYPE_USDT",
    "KASUSDT":  "KAS_USDT",
    "ALGOUSDT": "ALGO_USDT",
    "XMRUSDT":  "XMR_USDT",
    "XRPUSDT":  "XRP_USDT",
    "TONUSDT":  "TON_USDT",
    "SOLUSDT":  "SOL_USDT",
    "ONDOUSDT":   "ONDO_USDT",
    "AAVEUSDT":   "AAVE_USDT",
    "RENDERUSDT": "RENDER_USDT",
    "BNBUSDT":    "BNB_USDT",
    "BLURUSDT":   "BLUR_USDT",
    "ZECUSDT":    "ZEC_USDT",
    "TRXUSDT":    "TRX_USDT",
    "ADAUSDT":    "ADA_USDT",
    "XLMUSDT":    "XLM_USDT",
    "AVAXUSDT":   "AVAX_USDT",
    "HBARUSDT":   "HBAR_USDT",
    "QNTUSDT":    "QNT_USDT",
    "INJUSDT":    "INJ_USDT",
    "FETUSDT":    "FET_USDT",
}

OKX_PAIRS = {
    "BTCUSDT":  "BTC-USDT",
    "ETHUSDT":  "ETH-USDT",
    "LINKUSDT": "LINK-USDT",
    "SUIUSDT":  "SUI-USDT",
    "TAOUSDT":  "TAO-USDT",
    "HYPEUSDT": "HYPE-USDT",
    "KASUSDT":  "KAS-USDT",
    "ALGOUSDT": "ALGO-USDT",
    "XRPUSDT":  "XRP-USDT",
    "TONUSDT":  "TON-USDT",
    "SOLUSDT":  "SOL-USDT",
    "ONDOUSDT":   "ONDO-USDT",
    "AAVEUSDT":   "AAVE-USDT",
    "RENDERUSDT": "RENDER-USDT",
    "BNBUSDT":    "BNB-USDT",
    "BLURUSDT":   "BLUR-USDT",
    "ZECUSDT":    "ZEC-USDT",
    "TRXUSDT":    "TRX-USDT",
    "ADAUSDT":    "ADA-USDT",
    "XLMUSDT":    "XLM-USDT",
    "AVAXUSDT":   "AVAX-USDT",
    "HBARUSDT":   "HBAR-USDT",
    "QNTUSDT":    "QNT-USDT",
    "INJUSDT":    "INJ-USDT",
    "FETUSDT":    "FET-USDT",
}

BYBIT_PAIRS = {
    "BTCUSDT":  "BTCUSDT",
    "ETHUSDT":  "ETHUSDT",
    "LINKUSDT": "LINKUSDT",
    "SUIUSDT":  "SUIUSDT",
    "TAOUSDT":  "TAOUSDT",
    "HYPEUSDT": "HYPEUSDT",
    "KASUSDT":  "KASUSDT",
    "ALGOUSDT": "ALGOUSDT",
    "XMRUSDT":  "XMRUSDT",
    "XRPUSDT":  "XRPUSDT",
    "TONUSDT":  "TONUSDT",
    "SOLUSDT":  "SOLUSDT",
    "ONDOUSDT":   "ONDOUSDT",
    "AAVEUSDT":   "AAVEUSDT",
    "RENDERUSDT": "RENDERUSDT",
    "BNBUSDT":    "BNBUSDT",
    "BLURUSDT":   "BLURUSDT",
    "ZECUSDT":    "ZECUSDT",
    "TRXUSDT":    "TRXUSDT",
    "ADAUSDT":    "ADAUSDT",
    "XLMUSDT":    "XLMUSDT",
    "AVAXUSDT":   "AVAXUSDT",
    "HBARUSDT":   "HBARUSDT",
    "QNTUSDT":    "QNTUSDT",
    "INJUSDT":    "INJUSDT",
    "FETUSDT":    "FETUSDT",
}

# KuCoin trading pairs
KUCOIN_PAIRS = {
    "BTCUSDT":  "BTC-USDT",
    "ETHUSDT":  "ETH-USDT",
    "LINKUSDT": "LINK-USDT",
    "SUIUSDT":  "SUI-USDT",
    "TAOUSDT":  "TAO-USDT",
    "HYPEUSDT": "HYPE-USDT",
    "KASUSDT":  "KAS-USDT",
    "ALGOUSDT": "ALGO-USDT",
    "XMRUSDT":  "XMR-USDT",
    "XRPUSDT":  "XRP-USDT",
    "TONUSDT":  "TON-USDT",
    "SOLUSDT":  "SOL-USDT",
    "ONDOUSDT":   "ONDO-USDT",
    "AAVEUSDT":   "AAVE-USDT",
    "RENDERUSDT": "RENDER-USDT",
    "BNBUSDT":    "BNB-USDT",
    "BLURUSDT":   "BLUR-USDT",
}


class BinanceClient:
    def __init__(self):
        self.data_source = "binance"
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": "Mozilla/5.0 CryptoBadshah/2.0"})
        self._mcap_cache: dict = {}   # symbol -> (value, fetched_at)
        self._mcap_batch_fetched_at: float = 0
        self.last_binance_error = None
        self._mcap_ttl = 3600         # 1-hour cache — batch fetch, not per-token
        self.futures_real = True      # False when futures fell back to spot klines

    def _refresh_mcap_batch(self):
        """Fetch all market caps in one CoinGecko call to avoid rate limits."""
        now = time.time()
        if now - self._mcap_batch_fetched_at < self._mcap_ttl:
            return  # Still fresh
        try:
            all_ids   = list(set(CG_IDS.values()))
            id_to_sym = {v: k for k, v in CG_IDS.items()}
            data = self._get(f"{CG_BASE}/simple/price", {
                "ids":               ",".join(all_ids),
                "vs_currencies":     "usd",
                "include_market_cap": "true",
            })
            for cg_id, info in data.items():
                mcap = info.get("usd_market_cap")
                sym  = id_to_sym.get(cg_id)
                if sym and mcap:
                    self._mcap_cache[sym] = (mcap, now)
            self._mcap_batch_fetched_at = now
        except Exception:
            pass

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict = None) -> list | dict:
        r = self._s.get(url, params=params or {}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _parse_kline(self, k: list) -> Dict:
        return {
            "timestamp":        int(k[0]),
            "open":             float(k[1]),
            "high":             float(k[2]),
            "low":              float(k[3]),
            "close":            float(k[4]),
            "volume":           float(k[5]),
            "taker_buy_volume": float(k[9]),
        }

    # ── Binance ───────────────────────────────────────────────────────────────

    def _binance_klines(self, symbol, interval, limit) -> Optional[List[Dict]]:
        return self._binance_request(f"{SPOT_BASE}/api/v3/klines",
                                     {"symbol": symbol, "interval": interval, "limit": limit})

    def _binance_futures_klines(self, symbol, interval, limit) -> Optional[List[Dict]]:
        return self._binance_request(f"{FUTURES_BASE}/fapi/v1/klines",
                                     {"symbol": symbol, "interval": interval, "limit": limit})

    def _binance_request(self, url: str, params: dict) -> Optional[List[Dict]]:
        self.last_binance_error = None
        for attempt in range(BINANCE_RETRIES):
            try:
                data = self._get(url, params)
                return [self._parse_kline(k) for k in data]
            except Exception as e:
                self.last_binance_error = f"{type(e).__name__}: {e}"
                if attempt < BINANCE_RETRIES - 1:
                    time.sleep(0.4 * (attempt + 1))
        return None

    def binance_ping(self) -> str:
        try:
            self._get(f"{SPOT_BASE}/api/v3/ping")
            return "ok"
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    # ── CoinGecko ─────────────────────────────────────────────────────────────

    def _cg_daily_data(self, symbol: str, days: int):
        cg_id = CG_IDS.get(symbol)
        if not cg_id:
            return None, None
        try:
            data = self._get(f"{CG_BASE}/coins/{cg_id}/market_chart",
                             {"vs_currency": "usd", "days": str(days), "interval": "daily"})
            return data.get("prices", []), data.get("total_volumes", [])
        except Exception:
            return None, None

    def _cg_weekly_candles(self, symbol: str, limit: int = 100) -> Optional[List[Dict]]:
        prices, volumes = self._cg_daily_data(symbol, min(limit * 7 + 60, 730))
        if not prices:
            return None
        return self._group_by_week(prices, volumes, limit)

    def _cg_monthly_candles(self, symbol: str, limit: int = 24) -> Optional[List[Dict]]:
        prices, volumes = self._cg_daily_data(symbol, min(limit * 31 + 60, 730))
        if not prices:
            return None
        return self._group_by_month(prices, volumes, limit)

    def _cg_daily_as_candles(self, symbol: str, interval: str, limit: int = 100) -> Optional[List[Dict]]:
        """
        For intraday/daily requests that have no CEX candle data: fetch CoinGecko
        daily data and group it by the requested timeframe so price_roc, RSI etc.
        are computed on the correct time resolution.
        Supports: 1h, 2h, 4h, 8h, 12h, 1d (groups per N days where N < 7).
        Returns None for weekly/monthly (use dedicated methods instead).
        """
        _HOURS = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "12h": 12, "1d": 24}
        hours = _HOURS.get(interval.lower())
        if not hours:
            return None
        days_per_candle = hours / 24.0
        # Fetch enough daily data to cover the requested limit
        fetch_days = min(int(limit * days_per_candle) + 10, 730)
        prices, volumes = self._cg_daily_data(symbol, fetch_days)
        if not prices:
            return None
        if days_per_candle >= 1.0:
            # Group daily data into N-day candles
            n = int(days_per_candle)
            return self._group_by_n_days(prices, volumes, n, limit)
        else:
            # Sub-day: CoinGecko only provides daily resolution, so use daily
            # candles as the best available approximation (still much better
            # than weekly data for a 2H request).
            return self._group_by_n_days(prices, volumes, 1, limit)

    # ── Kraken ────────────────────────────────────────────────────────────────

    def _kraken_weekly_candles(self, symbol: str, limit: int = 100) -> Optional[List[Dict]]:
        pair = KRAKEN_PAIRS.get(symbol)
        if not pair:
            return None
        try:
            data = self._get(f"{KRAKEN_BASE}/OHLC",
                             {"pair": pair, "interval": 10080})  # 10080 min = 1 week
            result_key = list(data.get("result", {}).keys())
            result_key = [k for k in result_key if k != "last"]
            if not result_key:
                return None
            candles = data["result"][result_key[0]]
            out = []
            for k in candles[-limit:]:
                # Kraken: [time, open, high, low, close, vwap, volume, count]
                out.append({
                    "timestamp":        int(k[0]) * 1000,
                    "open":             float(k[1]),
                    "high":             float(k[2]),
                    "low":              float(k[3]),
                    "close":            float(k[4]),
                    "volume":           float(k[6]),
                    "taker_buy_volume": float(k[6]) * 0.5,
                })
            return out if out else None
        except Exception:
            return None

    # ── Gate.io ───────────────────────────────────────────────────────────────

    def _gate_weekly_candles(self, symbol: str, limit: int = 100) -> Optional[List[Dict]]:
        pair = GATE_PAIRS.get(symbol)
        if not pair:
            return None
        try:
            # Gate.io candlestick: interval 1w = weekly
            data = self._get(f"{GATE_BASE}/spot/candlesticks",
                             {"currency_pair": pair, "interval": "1w", "limit": limit})
            out = []
            for k in data:
                # Gate.io: [timestamp, volume, close, high, low, open, ...]
                out.append({
                    "timestamp":        int(k[0]) * 1000,
                    "open":             float(k[5]),
                    "high":             float(k[3]),
                    "low":              float(k[4]),
                    "close":            float(k[2]),
                    "volume":           float(k[1]),
                    "taker_buy_volume": float(k[1]) * 0.5,
                })
            return out if out else None
        except Exception:
            return None

    # ── KuCoin ────────────────────────────────────────────────────────────────

    _OKX_BAR_SPOT = {
        "1h": "1H", "2h": "2H", "4h": "4H", "8h": "8H",
        "12h": "12H", "1d": "1D", "1w": "1W", "1W": "1W", "1M": "1M",
    }

    def _okx_candles(self, symbol: str, interval: str, limit: int = 100) -> Optional[List[Dict]]:
        inst_id = OKX_PAIRS.get(symbol)
        if not inst_id:
            return None
        bar = self._OKX_BAR_SPOT.get(interval, "1W")
        try:
            data = self._get(f"{OKX_BASE}/api/v5/market/candles",
                             {"instId": inst_id, "bar": bar, "limit": min(limit, 300)})
            rows = data.get("data") if isinstance(data, dict) else None
            if not rows:
                return None
            out = []
            for k in reversed(rows):
                vol = float(k[5])
                out.append({
                    "timestamp":        int(k[0]),
                    "open":             float(k[1]),
                    "high":             float(k[2]),
                    "low":              float(k[3]),
                    "close":            float(k[4]),
                    "volume":           vol,
                    "taker_buy_volume": vol * 0.5,
                })
            return out[-limit:] if out else None
        except Exception:
            return None

    # Bybit interval codes for kline endpoint
    _BYBIT_IV = {
        "1h": "60",  "2h": "120", "4h": "240",  "8h": "480",
        "12h": "720", "1d": "D",  "1w": "W",    "1W": "W", "1M": "M",
    }

    def _bybit_candles(self, symbol: str, interval: str, limit: int = 100,
                       category: str = "spot") -> Optional[List[Dict]]:
        pair = BYBIT_PAIRS.get(symbol)
        if not pair:
            return None
        bybit_interval = self._BYBIT_IV.get(interval, "W")
        try:
            data = self._get(f"{BYBIT_BASE}/v5/market/kline", {
                "category": category,
                "symbol":   pair,
                "interval": bybit_interval,
                "limit":    min(limit, 1000),
            })
            rows = data.get("result", {}).get("list") if isinstance(data, dict) else None
            if not rows:
                return None
            out = []
            for k in reversed(rows):
                vol = float(k[5])
                out.append({
                    "timestamp":        int(k[0]),
                    "open":             float(k[1]),
                    "high":             float(k[2]),
                    "low":              float(k[3]),
                    "close":            float(k[4]),
                    "volume":           vol,
                    "taker_buy_volume": vol * 0.5,
                })
            return out[-limit:] if out else None
        except Exception:
            return None

    def _okx_futures_candles(self, symbol: str, interval: str, limit: int = 100) -> Optional[List[Dict]]:
        """OKX perpetual swap candles — instId = BTC-USDT-SWAP format."""
        spot_id = OKX_PAIRS.get(symbol)
        if not spot_id:
            return None
        inst_id = spot_id + "-SWAP"   # BTC-USDT → BTC-USDT-SWAP
        _OKX_BAR = {
            "1h": "1H", "2h": "2H", "4h": "4H",  "8h": "8H",
            "12h": "12H", "1d": "1D", "1w": "1W", "1W": "1W", "1M": "1M",
        }
        bar = _OKX_BAR.get(interval, "1W")
        try:
            data = self._get(f"{OKX_BASE}/api/v5/market/candles",
                             {"instId": inst_id, "bar": bar, "limit": min(limit, 300)})
            rows = data.get("data") if isinstance(data, dict) else None
            if not rows:
                return None
            out = []
            for k in reversed(rows):
                vol = float(k[5])
                out.append({
                    "timestamp":        int(k[0]),
                    "open":             float(k[1]),
                    "high":             float(k[2]),
                    "low":              float(k[3]),
                    "close":            float(k[4]),
                    "volume":           vol,
                    "taker_buy_volume": vol * 0.5,
                })
            return out[-limit:] if out else None
        except Exception:
            return None

    def _kucoin_weekly_candles(self, symbol: str, limit: int = 100) -> Optional[List[Dict]]:
        pair = KUCOIN_PAIRS.get(symbol)
        if not pair:
            return None
        try:
            # KuCoin: type=1week, returns newest-first [time, open, close, high, low, volume, turnover]
            data = self._get(f"{KUCOIN_BASE}/api/v1/market/candles",
                             {"type": "1week", "symbol": pair})
            raw = (data.get("data") or []) if isinstance(data, dict) else []
            out = []
            for k in reversed(raw[-limit:]):
                out.append({
                    "timestamp":        int(k[0]) * 1000,
                    "open":             float(k[1]),
                    "high":             float(k[3]),
                    "low":              float(k[4]),
                    "close":            float(k[2]),
                    "volume":           float(k[5]),
                    "taker_buy_volume": float(k[5]) * 0.5,
                })
            return out if out else None
        except Exception:
            return None

    # ── Aggregation helpers ───────────────────────────────────────────────────

    def _group_by_week(self, prices, volumes, limit) -> List[Dict]:
        vol_map: Dict[str, float] = {}
        for ts, vol in (volumes or []):
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

        result = []
        for key in week_order:
            w   = weeks[key]
            vol = vol_map.get(key, w["close"] * 500)
            result.append({
                "timestamp": w["timestamp"], "open": round(w["open"], 8),
                "high": round(w["high"], 8), "low": round(w["low"], 8),
                "close": round(w["close"], 8), "volume": round(vol, 2),
                "taker_buy_volume": round(vol * 0.5, 2),
            })
        return result[-limit:]

    def _group_by_month(self, prices, volumes, limit) -> List[Dict]:
        vol_map: Dict[str, float] = {}
        for ts, vol in (volumes or []):
            dt  = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            key = f"{dt.year}-{dt.month:02d}"
            vol_map[key] = vol_map.get(key, 0.0) + vol

        months: Dict[str, dict] = {}
        month_order: List[str] = []
        for ts, price in prices:
            dt  = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            key = f"{dt.year}-{dt.month:02d}"
            if key not in months:
                start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
                months[key] = {"timestamp": int(start.timestamp() * 1000),
                               "open": price, "high": price, "low": price, "close": price}
                month_order.append(key)
            else:
                months[key]["high"]  = max(months[key]["high"],  price)
                months[key]["low"]   = min(months[key]["low"],   price)
                months[key]["close"] = price

        result = []
        for key in month_order:
            m   = months[key]
            vol = vol_map.get(key, m["close"] * 2000)
            result.append({
                "timestamp": m["timestamp"], "open": round(m["open"], 8),
                "high": round(m["high"], 8), "low": round(m["low"], 8),
                "close": round(m["close"], 8), "volume": round(vol, 2),
                "taker_buy_volume": round(vol * 0.5, 2),
            })
        return result[-limit:]

    def _group_by_n_days(self, prices, volumes, n: int, limit: int) -> List[Dict]:
        """Group daily CoinGecko data into n-day candles."""
        vol_map: Dict[int, float] = {}
        for ts, vol in (volumes or []):
            day_idx = int(ts / 1000 / 86400)
            vol_map[day_idx] = vol_map.get(day_idx, 0.0) + vol

        # Sort price points by timestamp
        sorted_prices = sorted(prices, key=lambda x: x[0])
        buckets: List[dict] = []
        bucket: Optional[dict] = None
        bucket_start_idx = 0

        for ts, price in sorted_prices:
            day_idx = int(ts / 1000 / 86400)
            candle_idx = day_idx // n
            if bucket is None or candle_idx != bucket_start_idx:
                if bucket:
                    buckets.append(bucket)
                bucket = {"timestamp": ts, "open": price, "high": price,
                          "low": price, "close": price, "volume": 0.0}
                bucket_start_idx = candle_idx
            else:
                bucket["high"]  = max(bucket["high"], price)
                bucket["low"]   = min(bucket["low"],  price)
                bucket["close"] = price
            bucket["volume"] += vol_map.get(day_idx, 0.0)

        if bucket:
            buckets.append(bucket)

        result = []
        for b in buckets[-limit:]:
            vol = round(b["volume"], 2)
            result.append({
                "timestamp":        int(b["timestamp"]),
                "open":             round(b["open"],  8),
                "high":             round(b["high"],  8),
                "low":              round(b["low"],   8),
                "close":            round(b["close"], 8),
                "volume":           vol,
                "taker_buy_volume": round(vol * 0.5, 2),
            })
        return result if result else None

    # ── Public interface ──────────────────────────────────────────────────────

    def get_spot_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        is_monthly = (interval == "1M")
        is_weekly  = (interval in ("1w", "1W"))
        # Weekly/monthly fallback sources return weekly candles regardless of the
        # requested interval — only use them when a weekly/monthly view is intended.
        # For intraday/daily intervals, stop at Bybit to avoid silently serving
        # weekly data as if it were 2H/4H/1D data (causes wrong ROC, RSI, etc.)
        use_weekly_fallbacks = is_weekly or is_monthly

        # Always try each source in order — never skip based on shared state
        result = self._binance_klines(symbol, interval, limit)
        if result:
            self.data_source = "binance"
            return result

        result = self._okx_candles(symbol, interval, limit)
        if result:
            self.data_source = "okx"
            return result

        result = self._bybit_candles(symbol, interval, limit)
        if result:
            self.data_source = "bybit"
            return result

        if use_weekly_fallbacks:
            result = self._kucoin_weekly_candles(symbol, limit)
            if result:
                self.data_source = "kucoin"
                return result

            result = self._gate_weekly_candles(symbol, limit)
            if result:
                self.data_source = "gateio"
                return result

            result = self._cg_monthly_candles(symbol, limit) if is_monthly \
                     else self._cg_weekly_candles(symbol, limit)
            if result:
                self.data_source = "coingecko"
                return result

            result = self._kraken_weekly_candles(symbol, limit)
            if result:
                self.data_source = "kraken"
                return result
        else:
            # For intraday requests: try progressively larger real-exchange
            # intervals (4H → 8H → 1D) before falling back to aggregated daily.
            # A 4H candle is far more accurate than a daily candle grouped to "2H".
            _FALLBACK_CHAIN = {
                "1h":  ["2h",  "4h", "8h",  "12h", "1d"],
                "2h":  ["4h",  "8h", "12h", "1d"],
                "4h":  ["8h",  "12h", "1d"],
                "8h":  ["12h", "1d"],
                "12h": ["1d"],
            }
            for fb_interval in _FALLBACK_CHAIN.get(interval.lower(), []):
                result = self._binance_klines(symbol, fb_interval, limit)
                if result:
                    self.data_source = "binance"
                    return result
                result = self._okx_candles(symbol, fb_interval, limit)
                if result:
                    self.data_source = "okx"
                    return result
                result = self._bybit_candles(symbol, fb_interval, limit)
                if result:
                    self.data_source = "bybit"
                    return result

            # Last intraday resort: CoinGecko daily aggregated
            result = self._cg_daily_as_candles(symbol, interval, limit)
            if result:
                self.data_source = "coingecko"
                return result

        self.data_source = "demo"
        from mock_data import mock_spot_klines
        return mock_spot_klines(symbol, interval, limit)

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Lightweight current price fetch — uses ticker endpoints, never falls back
        to weekly/monthly candles. Used by /api/prices so live rec card prices
        are always accurate regardless of which exchange serves the data.
        """
        # 1. Binance spot ticker (fastest)
        try:
            data = self._get(f"{SPOT_BASE}/api/v3/ticker/price", {"symbol": symbol})
            price = float(data.get("price", 0))
            if price > 0:
                return price
        except Exception:
            pass

        # 2. OKX ticker
        try:
            inst = symbol.replace("USDT", "-USDT")
            data = self._get(f"{OKX_BASE}/api/v5/market/ticker", {"instId": inst})
            price = float((data.get("data") or [{}])[0].get("last", 0))
            if price > 0:
                return price
        except Exception:
            pass

        # 3. Bybit ticker
        try:
            data = self._get(f"{BYBIT_BASE}/v5/market/tickers",
                             {"category": "spot", "symbol": symbol})
            price = float(((data.get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0))
            if price > 0:
                return price
        except Exception:
            pass

        # 4. CoinGecko simple price (last resort — no weekly candle confusion)
        try:
            cg_id = CG_IDS.get(symbol)
            if cg_id:
                data  = self._get(f"{CG_BASE}/simple/price",
                                  {"ids": cg_id, "vs_currencies": "usd"})
                price = float((data.get(cg_id) or {}).get("usd", 0))
                if price > 0:
                    return price
        except Exception:
            pass

        return None

    def get_futures_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        # 1. Binance perpetual futures
        result = self._binance_futures_klines(symbol, interval, limit)
        if result:
            self.futures_real = True
            return result

        # 2. Bybit linear perpetuals (USDT-margined)
        result = self._bybit_candles(symbol, interval, limit, category="linear")
        if result:
            self.futures_real = True
            return result

        # 3. OKX perpetual swaps (BTC-USDT-SWAP)
        result = self._okx_futures_candles(symbol, interval, limit)
        if result:
            self.futures_real = True
            return result

        # 4. Fall back to spot candles — flag so callers skip futures-only metrics
        self.futures_real = False
        current_source = self.data_source
        result = self.get_spot_klines(symbol, interval, limit)
        self.data_source = current_source
        return result

    def get_funding_rate(self, symbol: str, limit: int = 10) -> Dict:
        try:
            data = self._get(f"{FUTURES_BASE}/fapi/v1/fundingRate",
                             {"symbol": symbol, "limit": limit})
            if data:
                rates = [float(d["fundingRate"]) * 100 for d in data]
                return {
                    "current": round(rates[-1], 4),
                    "average": round(sum(rates) / len(rates), 4),
                    "history": [{"timestamp": int(d["fundingTime"]),
                                 "rate": round(float(d["fundingRate"]) * 100, 4)}
                                for d in data],
                }
        except Exception:
            pass
        from mock_data import mock_funding_rate
        return mock_funding_rate(symbol, limit)

    def get_open_interest(self, symbol: str) -> Dict:
        try:
            cur = float(self._get(f"{FUTURES_BASE}/fapi/v1/openInterest",
                                  {"symbol": symbol})["openInterest"])
            hist = self._get(f"{FUTURES_BASE}/futures/data/openInterestHist",
                             {"symbol": symbol, "period": "1d", "limit": 14})
            history = [{"timestamp": int(d["timestamp"]),
                        "oi": float(d["sumOpenInterest"])} for d in hist]
            chg = (cur - history[0]["oi"]) / history[0]["oi"] * 100 if history else 0.0
            return {"value": round(cur, 2), "change_pct": round(chg, 2), "history": history}
        except Exception:
            pass
        from mock_data import mock_open_interest
        return mock_open_interest(symbol)

    def get_liquidations(self, symbol: str) -> Dict:
        try:
            data   = self._get(f"{FUTURES_BASE}/fapi/v1/allForceOrders",
                               {"symbol": symbol, "limit": 100})
            longs  = sum(float(d["origQty"]) * float(d["price"])
                         for d in data if d.get("side") == "SELL")
            shorts = sum(float(d["origQty"]) * float(d["price"])
                         for d in data if d.get("side") == "BUY")
            return {
                "longs_liquidated": round(longs, 2),
                "shorts_liquidated": round(shorts, 2),
                "total": round(longs + shorts, 2),
                "recent": [{"side": "LONG" if d.get("side") == "SELL" else "SHORT",
                            "qty": float(d["origQty"]), "price": float(d["price"]),
                            "timestamp": int(d["time"])} for d in data[:20]],
            }
        except Exception:
            pass
        from mock_data import mock_liquidations
        return mock_liquidations(symbol)

    def get_long_short_ratio(self, symbol: str) -> Dict:
        """Global long/short account ratio — Binance fapi → Bybit → OKX fallback."""
        # ── Binance fapi ─────────────────────────────────────────────────────
        try:
            data = self._get(f"{FUTURES_BASE}/futures/data/globalLongShortAccountRatio",
                             {"symbol": symbol, "period": "1h", "limit": 1})
            if data:
                d = data[-1] if isinstance(data, list) else data
                ratio     = float(d["longShortRatio"])
                long_pct  = float(d["longAccount"])  * 100
                short_pct = float(d["shortAccount"]) * 100
                return {
                    "ratio":     round(ratio,     4),
                    "long_pct":  round(long_pct,  2),
                    "short_pct": round(short_pct, 2),
                }
        except Exception:
            pass

        # ── Bybit linear ─────────────────────────────────────────────────────
        bybit_sym = BYBIT_PAIRS.get(symbol)
        if bybit_sym:
            try:
                data = self._get(f"{BYBIT_BASE}/v5/market/account-ratio", {
                    "category": "linear",
                    "symbol":   bybit_sym,
                    "period":   "1h",
                    "limit":    1,
                })
                rows = (data or {}).get("result", {}).get("list", [])
                if rows:
                    d          = rows[0]
                    buy_ratio  = float(d.get("buyRatio",  0))
                    sell_ratio = float(d.get("sellRatio", 0))
                    total      = buy_ratio + sell_ratio or 1
                    ratio      = round(buy_ratio / sell_ratio, 4) if sell_ratio else 0
                    return {
                        "ratio":     ratio,
                        "long_pct":  round(buy_ratio  / total * 100, 2),
                        "short_pct": round(sell_ratio / total * 100, 2),
                    }
            except Exception:
                pass

        # ── OKX swap ─────────────────────────────────────────────────────────
        # OKX returns {"data": [["timestamp", "ratio"], ...]} — index 1 is the ratio
        okx_base = symbol.replace("USDT", "")
        try:
            data = self._get(f"{OKX_BASE}/api/v5/rubik/stat/contracts/long-short-account-ratio", {
                "ccy":    okx_base,
                "period": "1H",
            })
            rows = (data or {}).get("data", [])
            if rows:
                ratio = float(rows[0][1])
                if ratio > 0:
                    long_pct  = round(ratio / (1 + ratio) * 100, 2)
                    short_pct = round(100 - long_pct, 2)
                    return {
                        "ratio":     round(ratio, 4),
                        "long_pct":  long_pct,
                        "short_pct": short_pct,
                    }
        except Exception:
            pass

        return {}

    def get_market_cap(self, symbol: str) -> Optional[float]:
        return self._get_market_cap(symbol)

    def _get_market_cap(self, symbol: str) -> Optional[float]:
        # Refresh the whole batch if stale (one call covers all tokens)
        self._refresh_mcap_batch()
        cached = self._mcap_cache.get(symbol)
        if cached:
            return cached[0]
        # CoinGecko unavailable — use hardcoded approximate fallback for tier classification
        return MCAP_FALLBACK.get(symbol)

    def get_order_book_walls(self, symbol: str, market_cap: Optional[float] = None) -> Dict:
        """
        Largest bid/ask walls from the futures order book.
        Clusters price levels into 0.1% buckets, then sizes each wall
        relative to the coin's live market cap so impact is comparable
        across coins (BTC vs ONDO, etc.).
        """
        def build_walls(bids, asks):
            if not bids or not asks:
                return {}

            current     = bids[0][0]
            bucket_pct  = 0.001          # 0.1% buckets for fine clustering
            bucket_size = current * bucket_pct
            zone_size   = current * 0.005  # 0.5% zones for grouping nearby walls

            mcap = market_cap if market_cap is not None else self._get_market_cap(symbol)

            def cluster_side(orders, top_n=5):
                """Cluster into 0.1% buckets, merge into 0.5% zones, return top N by USD."""
                buckets: Dict[float, dict] = {}
                for price, qty in orders:
                    key = round(round(price / bucket_size) * bucket_size, 8)
                    if key not in buckets:
                        buckets[key] = {"price": key, "qty": 0.0, "usd": 0.0}
                    buckets[key]["qty"] += qty
                    buckets[key]["usd"] += price * qty

                # Merge adjacent buckets within 0.5% into zones
                sorted_prices = sorted(buckets.keys())
                zones = []
                for p in sorted_prices:
                    b = buckets[p]
                    if zones and abs(p - zones[-1]["price"]) <= zone_size:
                        zones[-1]["usd"] += b["usd"]
                        zones[-1]["qty"] += b["qty"]
                        # Keep price of heaviest bucket in zone
                        if b["usd"] > zones[-1]["_peak_usd"]:
                            zones[-1]["price"]     = b["price"]
                            zones[-1]["_peak_usd"] = b["usd"]
                    else:
                        zones.append({"price": b["price"], "qty": b["qty"],
                                      "usd": b["usd"], "_peak_usd": b["usd"]})

                zones.sort(key=lambda x: x["usd"], reverse=True)
                return zones[:top_n]

            def air_pocket(zones, side):
                """Largest price gap between consecutive wall zones within 5% of current."""
                limit = current * (0.95 if side == "bid" else 1.05)
                nearby = [z for z in sorted(zones, key=lambda x: x["price"])
                          if (side == "bid" and z["price"] >= limit) or
                             (side == "ask" and z["price"] <= limit)]
                if len(nearby) < 2:
                    return None
                max_gap = 0.0
                gap_info = None
                for i in range(len(nearby) - 1):
                    gap = abs(nearby[i+1]["price"] - nearby[i]["price"])
                    gap_pct = gap / current * 100
                    if gap_pct > max_gap:
                        max_gap = gap_pct
                        gap_info = {
                            "gap_pct":    round(gap_pct, 2),
                            "price_from": round(nearby[i]["price"],   8),
                            "price_to":   round(nearby[i+1]["price"], 8),
                        }
                return gap_info if (gap_info and gap_info["gap_pct"] >= 1.5) else None

            def wall_dict(w):
                dist_pct = (w["price"] - current) / current * 100
                mcap_pct = round(w["usd"] / mcap * 100, 6) if mcap else None
                if mcap_pct is None:          sig = None
                elif mcap_pct >= 0.01:        sig = "high"
                elif mcap_pct >= 0.001:       sig = "medium"
                else:                         sig = "low"
                abs_dist = abs(dist_pct)
                if abs_dist < 1.0:            dist_label = "Immediate"
                elif abs_dist < 3.0:          dist_label = "Near"
                else:                         dist_label = "Far"
                return {
                    "price":        w["price"],
                    "qty":          round(w["qty"], 4),
                    "usd_value":    round(w["usd"], 2),
                    "distance_pct": round(dist_pct, 3),
                    "dist_label":   dist_label,
                    "mcap_pct":     mcap_pct,
                    "significance": sig,
                }

            bid_zones = cluster_side(bids)
            ask_zones = cluster_side(asks)

            # All clustered buckets for air pocket detection
            all_bid_buckets: Dict[float, dict] = {}
            for price, qty in bids:
                key = round(round(price / bucket_size) * bucket_size, 8)
                if key not in all_bid_buckets:
                    all_bid_buckets[key] = {"price": key, "qty": 0.0, "usd": 0.0}
                all_bid_buckets[key]["qty"] += qty
                all_bid_buckets[key]["usd"] += price * qty
            all_ask_buckets: Dict[float, dict] = {}
            for price, qty in asks:
                key = round(round(price / bucket_size) * bucket_size, 8)
                if key not in all_ask_buckets:
                    all_ask_buckets[key] = {"price": key, "qty": 0.0, "usd": 0.0}
                all_ask_buckets[key]["qty"] += qty
                all_ask_buckets[key]["usd"] += price * qty

            near_bids = [(p, q) for p, q in bids if p >= current * 0.98]
            near_asks = [(p, q) for p, q in asks if p <= current * 1.02]
            bid_usd   = sum(p * q for p, q in near_bids)
            ask_usd   = sum(p * q for p, q in near_asks)

            # Imbalance signal
            ratio = bid_usd / (ask_usd + 1e-9)
            if   ratio >= 2.0:  imbalance = "strong_bid"
            elif ratio >= 1.5:  imbalance = "bid_heavy"
            elif ratio <= 0.5:  imbalance = "strong_ask"
            elif ratio <= 0.67: imbalance = "ask_heavy"
            else:               imbalance = "balanced"

            top_bids = [wall_dict(z) for z in bid_zones]
            top_asks = [wall_dict(z) for z in ask_zones]

            return {
                "biggest_bid":      top_bids[0] if top_bids else None,
                "biggest_ask":      top_asks[0] if top_asks else None,
                "top_bids":         top_bids,
                "top_asks":         top_asks,
                "bid_ask_ratio":    round(ratio, 3),
                "imbalance":        imbalance,
                "near_bid_usd":     round(bid_usd, 2),
                "near_ask_usd":     round(ask_usd, 2),
                "air_pocket_below": air_pocket(list(all_bid_buckets.values()), "bid"),
                "air_pocket_above": air_pocket(list(all_ask_buckets.values()), "ask"),
                "current_price":    current,
                "market_cap":       round(mcap, 0) if mcap else None,
            }

        # 1. Binance futures
        try:
            data = self._get(f"{FUTURES_BASE}/fapi/v1/depth",
                             {"symbol": symbol, "limit": 1000})
            bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
            asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
            walls = build_walls(bids, asks)
            if walls:
                walls["source"] = "binance_futures"
                return walls
        except Exception:
            pass

        # 2. Binance spot
        try:
            data = self._get(f"{SPOT_BASE}/api/v3/depth",
                             {"symbol": symbol, "limit": 1000})
            bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
            asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
            walls = build_walls(bids, asks)
            if walls:
                walls["source"] = "binance"
                return walls
        except Exception:
            pass

        # 3. OKX spot
        try:
            inst_id = OKX_PAIRS.get(symbol)
            if inst_id:
                data = self._get(f"{OKX_BASE}/api/v5/market/books",
                                 {"instId": inst_id, "sz": "400"})
                book = (data.get("data") or [{}])[0]
                bids = [(float(p), float(q)) for p, q, *_ in book.get("bids", [])]
                asks = [(float(p), float(q)) for p, q, *_ in book.get("asks", [])]
                walls = build_walls(bids, asks)
                if walls:
                    walls["source"] = "okx"
                    return walls
        except Exception:
            pass

        # 4. Bybit spot
        try:
            pair = BYBIT_PAIRS.get(symbol)
            if pair:
                data = self._get(f"{BYBIT_BASE}/v5/market/orderbook",
                                 {"category": "spot", "symbol": pair, "limit": 200})
                result = (data.get("result") or {}) if isinstance(data, dict) else {}
                bids = [(float(p), float(q)) for p, q in result.get("b", [])]
                asks = [(float(p), float(q)) for p, q in result.get("a", [])]
                walls = build_walls(bids, asks)
                if walls:
                    walls["source"] = "bybit"
                    return walls
        except Exception:
            pass

        # 5. KuCoin spot
        try:
            pair = KUCOIN_PAIRS.get(symbol)
            if pair:
                data = self._get(f"{KUCOIN_BASE}/api/v1/market/orderbook/level2_100",
                                 {"symbol": pair})
                book = (data.get("data") or {}) if isinstance(data, dict) else {}
                bids = [(float(p), float(q)) for p, q in book.get("bids", [])]
                asks = [(float(p), float(q)) for p, q in book.get("asks", [])]
                walls = build_walls(bids, asks)
                if walls:
                    walls["source"] = "kucoin"
                    return walls
        except Exception:
            pass

        # 6. Gate.io spot
        try:
            pair = GATE_PAIRS.get(symbol)
            if pair:
                data = self._get(f"{GATE_BASE}/spot/order_book",
                                 {"currency_pair": pair, "limit": 100})
                bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
                asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
                walls = build_walls(bids, asks)
                if walls:
                    walls["source"] = "gateio"
                    return walls
        except Exception:
            pass

        # 7. MEXC spot (Binance-compatible)
        try:
            data = self._get(f"https://api.mexc.com/api/v3/depth",
                             {"symbol": symbol, "limit": 1000})
            bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
            asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
            walls = build_walls(bids, asks)
            if walls:
                walls["source"] = "mexc"
                return walls
        except Exception:
            pass

        return {}

    @staticmethod
    def aggregate_candles(candles: List[Dict], n: int) -> List[Dict]:
        if not candles or n < 2:
            return candles
        result = []
        num_complete = len(candles) // n
        for i in range(num_complete):
            chunk = candles[i * n: (i + 1) * n]
            result.append({
                "timestamp":        chunk[0]["timestamp"],
                "open":             chunk[0]["open"],
                "high":             max(c["high"] for c in chunk),
                "low":              min(c["low"]  for c in chunk),
                "close":            chunk[-1]["close"],
                "volume":           sum(c["volume"] for c in chunk),
                "taker_buy_volume": sum(c.get("taker_buy_volume", 0) for c in chunk),
            })
        # Always include the partial (forming) period at the end so the current
        # price is never dropped due to an odd/misaligned candle count.
        remainder = candles[num_complete * n:]
        if remainder:
            result.append({
                "timestamp":        remainder[0]["timestamp"],
                "open":             remainder[0]["open"],
                "high":             max(c["high"] for c in remainder),
                "low":              min(c["low"]  for c in remainder),
                "close":            remainder[-1]["close"],
                "volume":           sum(c["volume"] for c in remainder),
                "taker_buy_volume": sum(c.get("taker_buy_volume", 0) for c in remainder),
            })
        return result
