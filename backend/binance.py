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
    "ONDOUSDT": "ONDO_USDT",
    "AAVEUSDT": "AAVE_USDT",
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
    "ONDOUSDT": "ONDO-USDT",
    "AAVEUSDT": "AAVE-USDT",
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
    "XRPUSDT":  "XRPUSDT",
    "TONUSDT":  "TONUSDT",
    "SOLUSDT":  "SOLUSDT",
    "ONDOUSDT": "ONDOUSDT",
    "AAVEUSDT": "AAVEUSDT",
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
    "ONDOUSDT": "ONDO-USDT",
    "AAVEUSDT": "AAVE-USDT",
}



class BinanceClient:
    def __init__(self):
        self.data_source = "binance"
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": "Mozilla/5.0 CryptoBadshah/2.0"})
        self._mcap_cache: dict = {}   # symbol -> (value, fetched_at)
        self.last_binance_error = None
        self._mcap_ttl = 300          # 5-minute cache — avoids hammering CoinGecko

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

    def _okx_candles(self, symbol: str, interval: str, limit: int = 100) -> Optional[List[Dict]]:
        inst_id = OKX_PAIRS.get(symbol)
        if not inst_id:
            return None
        bar = "1M" if interval == "1M" else "1W"
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

    def _bybit_candles(self, symbol: str, interval: str, limit: int = 100) -> Optional[List[Dict]]:
        pair = BYBIT_PAIRS.get(symbol)
        if not pair:
            return None
        bybit_interval = "M" if interval == "1M" else "W"
        try:
            data = self._get(f"{BYBIT_BASE}/v5/market/kline", {
                "category": "spot",
                "symbol": pair,
                "interval": bybit_interval,
                "limit": min(limit, 1000),
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

    # ── Public interface ──────────────────────────────────────────────────────

    def get_spot_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        is_monthly = (interval == "1M")

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

        self.data_source = "demo"
        from mock_data import mock_spot_klines
        return mock_spot_klines(symbol, interval, limit)

    def get_futures_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        result = self._binance_futures_klines(symbol, interval, limit)
        if result:
            return result
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
        """Global long/short account ratio from Binance futures (free endpoint)."""
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
        return {}

    def get_market_cap(self, symbol: str) -> Optional[float]:
        return self._get_market_cap(symbol)

    def _get_market_cap(self, symbol: str) -> Optional[float]:
        cached = self._mcap_cache.get(symbol)
        if cached:
            value, fetched_at = cached
            if time.time() - fetched_at < self._mcap_ttl:
                return value

        cg_id = CG_IDS.get(symbol)
        if not cg_id:
            return None
        try:
            data = self._get(f"{CG_BASE}/simple/price", {
                "ids": cg_id,
                "vs_currencies": "usd",
                "include_market_cap": "true",
            })
            value = data.get(cg_id, {}).get("usd_market_cap")
            if value:
                self._mcap_cache[symbol] = (value, time.time())
            return value
        except Exception:
            return None

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
            bucket_size = current * 0.001

            def cluster_wall(orders):
                buckets: Dict[float, dict] = {}
                for price, qty in orders:
                    key = round(round(price / bucket_size) * bucket_size, 2)
                    if key not in buckets:
                        buckets[key] = {"price": key, "qty": 0.0, "usd": 0.0}
                    buckets[key]["qty"] += qty
                    buckets[key]["usd"] += price * qty
                return max(buckets.values(), key=lambda x: x["usd"]) if buckets else None

            big_bid = cluster_wall(bids)
            big_ask = cluster_wall(asks)

            near_bids = [(p, q) for p, q in bids if p >= current * 0.98]
            near_asks = [(p, q) for p, q in asks if p <= current * 1.02]
            bid_usd   = sum(p * q for p, q in near_bids)
            ask_usd   = sum(p * q for p, q in near_asks)

            mcap = market_cap if market_cap is not None else self._get_market_cap(symbol)

            def wall_dict(w):
                mcap_pct = round(w["usd"] / mcap * 100, 6) if mcap else None
                if mcap_pct is None:
                    sig = None
                elif mcap_pct >= 0.005:
                    sig = "high"
                elif mcap_pct >= 0.0005:
                    sig = "medium"
                else:
                    sig = "low"
                return {
                    "price":          round(w["price"], 2),
                    "qty":            round(w["qty"], 4),
                    "usd_value":      round(w["usd"], 2),
                    "distance_pct":   round((w["price"] - current) / current * 100, 3),
                    "mcap_pct":       mcap_pct,
                    "significance":   sig,
                }

            return {
                "biggest_bid":   wall_dict(big_bid) if big_bid else None,
                "biggest_ask":   wall_dict(big_ask) if big_ask else None,
                "bid_ask_ratio": round(bid_usd / (ask_usd + 1e-9), 3),
                "near_bid_usd":  round(bid_usd, 2),
                "near_ask_usd":  round(ask_usd, 2),
                "current_price": round(current, 2),
                "market_cap":    round(mcap, 0) if mcap else None,
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
