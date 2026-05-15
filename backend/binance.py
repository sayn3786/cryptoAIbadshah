"""
Market data client — tries Binance → CoinGecko → Kraken → Gate.io → demo.
Pure Python (requests), no compilation required.
"""
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

SPOT_BASE    = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
CG_BASE      = "https://api.coingecko.com/api/v3"
KRAKEN_BASE  = "https://api.kraken.com/0/public"
GATE_BASE    = "https://api.gateio.ws/api/v4"
TIMEOUT      = 15

# CoinGecko IDs
CG_IDS = {
    "BTCUSDT":  "bitcoin",
    "ETHUSDT":  "ethereum",
    "LINKUSDT": "chainlink",
    "TAOUSDT":  "bittensor",
    "HYPEUSDT": "hyperliquid",
    "ONDOUSDT": "ondo-finance",
}

# Kraken pairs (weekly interval = 10080 min)
KRAKEN_PAIRS = {
    "BTCUSDT":  "XBTUSD",
    "ETHUSDT":  "ETHUSD",
    "LINKUSDT": "LINKUSD",
    "TAOUSDT":  "TAOUSD",
}

# Gate.io currency pairs
GATE_PAIRS = {
    "BTCUSDT":  "BTC_USDT",
    "ETHUSDT":  "ETH_USDT",
    "LINKUSDT": "LINK_USDT",
    "TAOUSDT":  "TAO_USDT",
    "HYPEUSDT": "HYPE_USDT",
    "ONDOUSDT": "ONDO_USDT",
}

DATA_SOURCES = ["binance", "coingecko", "kraken", "gateio", "demo"]


class BinanceClient:
    def __init__(self):
        self.data_source = "binance"
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": "Mozilla/5.0 CryptoBadshah/2.0"})

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

    def _next_source(self):
        idx = DATA_SOURCES.index(self.data_source)
        if idx + 1 < len(DATA_SOURCES):
            self.data_source = DATA_SOURCES[idx + 1]

    # ── Binance ───────────────────────────────────────────────────────────────

    def _binance_klines(self, symbol, interval, limit) -> Optional[List[Dict]]:
        try:
            data = self._get(f"{SPOT_BASE}/api/v3/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit})
            return [self._parse_kline(k) for k in data]
        except Exception:
            return None

    def _binance_futures_klines(self, symbol, interval, limit) -> Optional[List[Dict]]:
        try:
            data = self._get(f"{FUTURES_BASE}/fapi/v1/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit})
            return [self._parse_kline(k) for k in data]
        except Exception:
            return None

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

        # 1. Binance
        if self.data_source == "binance":
            result = self._binance_klines(symbol, interval, limit)
            if result:
                return result
            self._next_source()

        # 2. CoinGecko
        if self.data_source == "coingecko":
            result = self._cg_monthly_candles(symbol, limit) if is_monthly \
                     else self._cg_weekly_candles(symbol, limit)
            if result:
                return result
            self._next_source()

        # 3. Kraken
        if self.data_source == "kraken":
            result = self._kraken_weekly_candles(symbol, limit)
            if result:
                return result
            self._next_source()

        # 4. Gate.io
        if self.data_source == "gateio":
            result = self._gate_weekly_candles(symbol, limit)
            if result:
                return result
            self._next_source()

        # 5. Demo
        from mock_data import mock_spot_klines
        return mock_spot_klines(symbol, interval, limit)

    def get_futures_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        if self.data_source == "binance":
            result = self._binance_futures_klines(symbol, interval, limit)
            if result:
                return result
            # Don't advance source — spot klines call will do that
        return self.get_spot_klines(symbol, interval, limit)

    def get_funding_rate(self, symbol: str, limit: int = 10) -> Dict:
        if self.data_source == "binance":
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
        if self.data_source == "binance":
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
        if self.data_source == "binance":
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

    @staticmethod
    def aggregate_candles(candles: List[Dict], n: int) -> List[Dict]:
        result = []
        for i in range(0, len(candles) - n + 1, n):
            chunk = candles[i: i + n]
            result.append({
                "timestamp":        chunk[0]["timestamp"],
                "open":             chunk[0]["open"],
                "high":             max(c["high"] for c in chunk),
                "low":              min(c["low"]  for c in chunk),
                "close":            chunk[-1]["close"],
                "volume":           sum(c["volume"] for c in chunk),
                "taker_buy_volume": sum(c["taker_buy_volume"] for c in chunk),
            })
        return result
