"""Binance public API client — sync, pure Python (requests)."""
import requests
from typing import List, Dict

SPOT_BASE    = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
TIMEOUT      = 12


class BinanceClient:
    def __init__(self):
        self._demo = False
        self._s = requests.Session()

    def _get(self, url: str, params: dict) -> list | dict:
        r = self._s.get(url, params=params, timeout=TIMEOUT)
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

    def get_spot_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        if self._demo:
            from mock_data import mock_spot_klines
            return mock_spot_klines(symbol, interval, limit)
        try:
            data = self._get(f"{SPOT_BASE}/api/v3/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit})
            return [self._parse_kline(k) for k in data]
        except Exception:
            self._demo = True
            from mock_data import mock_spot_klines
            return mock_spot_klines(symbol, interval, limit)

    def get_futures_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        if self._demo:
            from mock_data import mock_futures_klines
            return mock_futures_klines(symbol, interval, limit)
        try:
            data = self._get(f"{FUTURES_BASE}/fapi/v1/klines",
                             {"symbol": symbol, "interval": interval, "limit": limit})
            return [self._parse_kline(k) for k in data]
        except Exception:
            return self.get_spot_klines(symbol, interval, limit)

    def get_funding_rate(self, symbol: str, limit: int = 10) -> Dict:
        if self._demo:
            from mock_data import mock_funding_rate
            return mock_funding_rate(symbol, limit)
        try:
            data = self._get(f"{FUTURES_BASE}/fapi/v1/fundingRate",
                             {"symbol": symbol, "limit": limit})
            if not data:
                return {"current": 0.0, "average": 0.0, "history": []}
            rates = [float(d["fundingRate"]) * 100 for d in data]
            return {
                "current": round(rates[-1], 4),
                "average": round(sum(rates) / len(rates), 4),
                "history": [
                    {"timestamp": int(d["fundingTime"]),
                     "rate": round(float(d["fundingRate"]) * 100, 4)}
                    for d in data
                ],
            }
        except Exception:
            return {"current": 0.0, "average": 0.0, "history": []}

    def get_open_interest(self, symbol: str) -> Dict:
        if self._demo:
            from mock_data import mock_open_interest
            return mock_open_interest(symbol)
        try:
            cur  = float(self._get(f"{FUTURES_BASE}/fapi/v1/openInterest",
                                   {"symbol": symbol})["openInterest"])
            hist_data = self._get(f"{FUTURES_BASE}/futures/data/openInterestHist",
                                  {"symbol": symbol, "period": "1d", "limit": 14})
            history = [{"timestamp": int(d["timestamp"]),
                        "oi": float(d["sumOpenInterest"])} for d in hist_data]
            chg = (cur - history[0]["oi"]) / history[0]["oi"] * 100 if history else 0.0
            return {"value": round(cur, 2), "change_pct": round(chg, 2), "history": history[-14:]}
        except Exception:
            return {"value": 0.0, "change_pct": 0.0, "history": []}

    def get_liquidations(self, symbol: str) -> Dict:
        if self._demo:
            from mock_data import mock_liquidations
            return mock_liquidations(symbol)
        try:
            data   = self._get(f"{FUTURES_BASE}/fapi/v1/allForceOrders",
                               {"symbol": symbol, "limit": 100})
            longs  = sum(float(d["origQty"]) * float(d["price"])
                         for d in data if d.get("side") == "SELL")
            shorts = sum(float(d["origQty"]) * float(d["price"])
                         for d in data if d.get("side") == "BUY")
            return {
                "longs_liquidated":  round(longs,  2),
                "shorts_liquidated": round(shorts, 2),
                "total": round(longs + shorts, 2),
                "recent": [
                    {"side": "LONG" if d.get("side") == "SELL" else "SHORT",
                     "qty":   float(d["origQty"]),
                     "price": float(d["price"]),
                     "timestamp": int(d["time"])}
                    for d in data[:20]
                ],
            }
        except Exception:
            return {"longs_liquidated": 0, "shorts_liquidated": 0, "total": 0, "recent": []}

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
