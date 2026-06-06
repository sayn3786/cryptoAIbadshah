"""Realistic synthetic market data for demo / offline mode."""
import math
import random
from datetime import datetime, timezone
from typing import Dict, List

# Approximate current prices (May 2025)
MOCK_PRICES = {
    "BTCUSDT":  102500.0,
    "ETHUSDT":   2580.0,
    "LINKUSDT":    14.8,
    "TAOUSDT":    420.0,
    "HYPEUSDT":    24.5,
    "ONDOUSDT":     1.12,
}

# Weekly volatility (approx annual vol / sqrt(52))
MOCK_VOL = {
    "BTCUSDT":  0.048,
    "ETHUSDT":  0.062,
    "LINKUSDT": 0.078,
    "TAOUSDT":  0.095,
    "HYPEUSDT": 0.11,
    "ONDOUSDT": 0.085,
}

MOCK_OI = {          # Open interest in coin units
    "BTCUSDT":  85000.0,
    "ETHUSDT":  1200000.0,
    "LINKUSDT": 18000000.0,
    "TAOUSDT":  420000.0,
    "HYPEUSDT": 9000000.0,
    "ONDOUSDT": 45000000.0,
}

MOCK_FUNDING = {     # Current funding rate %
    "BTCUSDT":   0.0085,
    "ETHUSDT":   0.0062,
    "LINKUSDT":  0.0110,
    "TAOUSDT":  -0.0034,
    "HYPEUSDT":  0.0210,
    "ONDOUSDT":  0.0048,
}


def _candles(symbol: str, interval: str, n: int = 120, seed: int = 0) -> List[Dict]:
    """Generate synthetic candles at the correct interval spacing."""
    _INTERVAL_MS = {
        "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
        "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
        "1w": 604_800_000, "1M": 2_592_000_000,
    }
    candle_ms = _INTERVAL_MS.get(interval.lower(), 604_800_000)  # default weekly

    # Scale volatility to the candle size (weekly vol as baseline)
    week_ms  = 604_800_000
    vol_wk   = MOCK_VOL.get(symbol, 0.06)
    vol_candle = vol_wk * math.sqrt(candle_ms / week_ms)

    rng   = random.Random(seed + hash(symbol) % 999)
    price = MOCK_PRICES.get(symbol, 100.0)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    closes = [price]
    for _ in range(n - 1):
        drift = rng.gauss(0.001, vol_candle)
        closes.append(closes[-1] * (1 + drift))
    closes.reverse()

    candles = []
    for i, close in enumerate(closes):
        ts    = now_ms - (n - 1 - i) * candle_ms
        open_ = closes[i - 1] if i > 0 else close * (1 - rng.gauss(0, 0.005))
        high  = max(open_, close) * (1 + abs(rng.gauss(0, vol_candle * 0.5)))
        low   = min(open_, close) * (1 - abs(rng.gauss(0, vol_candle * 0.4)))
        volume   = rng.uniform(0.6, 1.4) * _base_volume(symbol)
        buy_frac = rng.uniform(0.38, 0.62)
        candles.append({
            "timestamp": ts,
            "open":  round(open_, 6),
            "high":  round(high,  6),
            "low":   round(low,   6),
            "close": round(close, 6),
            "volume": round(volume, 2),
            "taker_buy_volume": round(volume * buy_frac, 2),
        })
    return candles


def _weekly_candles(symbol: str, n: int = 120, seed: int = 0) -> List[Dict]:
    return _candles(symbol, "1w", n, seed)


def _base_volume(symbol: str) -> float:
    vols = {
        "BTCUSDT": 12000.0,
        "ETHUSDT": 200000.0,
        "LINKUSDT": 4000000.0,
        "TAOUSDT": 80000.0,
        "HYPEUSDT": 1500000.0,
        "ONDOUSDT": 15000000.0,
    }
    return vols.get(symbol, 100000.0)


def mock_spot_klines(symbol: str, interval: str, limit: int) -> List[Dict]:
    return _candles(symbol, interval, n=limit, seed=1)


def mock_futures_klines(symbol: str, interval: str, limit: int) -> List[Dict]:
    return _candles(symbol, interval, n=limit, seed=2)


def mock_funding_rate(symbol: str, limit: int = 10) -> Dict:
    base = MOCK_FUNDING.get(symbol, 0.005)
    rng = random.Random(hash(symbol))
    history = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for i in range(limit):
        ts = now_ms - (limit - i) * 8 * 3600 * 1000
        rate = round(base + rng.gauss(0, 0.002), 4)
        history.append({"timestamp": ts, "rate": rate})
    return {
        "current": round(base, 4),
        "average": round(sum(h["rate"] for h in history) / len(history), 4),
        "history": history,
    }


def mock_open_interest(symbol: str) -> Dict:
    base_oi = MOCK_OI.get(symbol, 100000.0)
    rng = random.Random(hash(symbol) + 7)
    history = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    oi = base_oi * 0.85
    for i in range(14):
        ts = now_ms - (14 - i) * 24 * 3600 * 1000
        oi *= 1 + rng.gauss(0.005, 0.02)
        history.append({"timestamp": ts, "oi": round(oi, 2)})
    change_pct = (base_oi - history[0]["oi"]) / history[0]["oi"] * 100
    return {
        "value": round(base_oi, 2),
        "change_pct": round(change_pct, 2),
        "history": history,
    }


def mock_liquidations(symbol: str) -> Dict:
    price = MOCK_PRICES.get(symbol, 100.0)
    rng = random.Random(hash(symbol) + 13)
    longs  = price * rng.uniform(80, 400)
    shorts = price * rng.uniform(50, 250)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    recent = []
    for i in range(20):
        side = rng.choice(["LONG", "SHORT"])
        qty  = rng.uniform(0.01, 2.0)
        recent.append({
            "side": side, "qty": round(qty, 4),
            "price": round(price * rng.uniform(0.98, 1.02), 2),
            "timestamp": now_ms - i * 3600 * 1000,
        })
    return {
        "longs_liquidated":  round(longs,  2),
        "shorts_liquidated": round(shorts, 2),
        "total": round(longs + shorts, 2),
        "recent": recent,
    }
