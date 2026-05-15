"""Technical indicators — pure Python, no numpy dependency."""
from typing import List, Dict, Optional, Tuple


def calculate_rsi_series(closes: List[float], period: int = 14) -> List[Optional[float]]:
    if len(closes) < period + 1:
        return [None] * len(closes)

    result: List[Optional[float]] = [None] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return round(100.0 - 100.0 / (1.0 + g / l), 2)

    result[period] = _rsi(avg_gain, avg_loss)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result[i + 1] = _rsi(avg_gain, avg_loss)

    return result


def calculate_cvd(candles: List[Dict], label: str = "spot") -> Dict:
    cvd = 0.0
    series = []

    for c in candles:
        total   = c.get("volume", 0.0)
        raw_buy = c.get("taker_buy_volume")

        # When taker_buy_volume is absent or set to exactly half (fallback sources),
        # estimate buying pressure from the candle's close position within its range.
        if raw_buy is None or (total > 1e-9 and abs(raw_buy / total - 0.5) < 1e-6):
            high  = c.get("high",  0.0)
            low   = c.get("low",   0.0)
            close = c.get("close", 0.0)
            rng   = high - low
            buy_frac = (close - low) / (rng + 1e-9) if rng > 1e-12 else 0.5
            buy = total * buy_frac
        else:
            buy = raw_buy

        sell  = total - buy
        delta = buy - sell
        cvd  += delta
        series.append(
            {"timestamp": c["timestamp"], "cvd": round(cvd, 4), "delta": round(delta, 4)}
        )

    trend = "neutral"
    if len(series) >= 5:
        recent = [s["cvd"] for s in series[-5:]]
        pct = (recent[-1] - recent[0]) / (abs(recent[0]) + 1e-9)
        if pct > 0.01:
            trend = "bullish"
        elif pct < -0.01:
            trend = "bearish"

    return {"current": round(cvd, 2), "trend": trend, "series": series[-30:], "label": label}


def detect_fvg(candles: List[Dict], min_size_pct: float = 1.5) -> List[Dict]:
    fvgs: List[Dict] = []
    if len(candles) < 3:
        return fvgs

    current_price = candles[-1]["close"]

    for i in range(1, len(candles) - 1):
        prev = candles[i - 1]
        curr = candles[i]
        nxt  = candles[i + 1]

        if prev["high"] < nxt["low"]:
            gap = (nxt["low"] - prev["high"]) / prev["high"] * 100
            if gap >= min_size_pct:
                mid = (nxt["low"] + prev["high"]) / 2
                fvgs.append({
                    "type": "bullish",
                    "top":      round(nxt["low"],  8),
                    "bottom":   round(prev["high"], 8),
                    "midpoint": round(mid, 8),
                    "size_pct": round(gap, 4),
                    "timestamp": curr["timestamp"],
                    "filled":   current_price < prev["high"],
                    "distance_pct": round((current_price - mid) / current_price * 100, 2),
                })

        elif prev["low"] > nxt["high"]:
            gap = (prev["low"] - nxt["high"]) / prev["low"] * 100
            if gap >= min_size_pct:
                mid = (prev["low"] + nxt["high"]) / 2
                fvgs.append({
                    "type": "bearish",
                    "top":      round(prev["low"],  8),
                    "bottom":   round(nxt["high"],  8),
                    "midpoint": round(mid, 8),
                    "size_pct": round(gap, 4),
                    "timestamp": curr["timestamp"],
                    "filled":   current_price > prev["low"],
                    "distance_pct": round((current_price - mid) / current_price * 100, 2),
                })

    fvgs.sort(key=lambda x: abs(x["distance_pct"]))
    return fvgs


def find_volume_spikes(candles: List[Dict]) -> Dict:
    """Find the biggest buy and sell candles by taker volume."""
    if len(candles) < 5:
        return {}

    buy_vols  = [c.get("taker_buy_volume", c["volume"] * 0.5) for c in candles]
    sell_vols = [c["volume"] - b for c, b in zip(candles, buy_vols)]
    avg_vol   = sum(c["volume"] for c in candles) / len(candles)

    bi = max(range(len(candles)), key=lambda i: buy_vols[i])
    si = max(range(len(candles)), key=lambda i: sell_vols[i])

    def _entry(idx: int, vol: float, kind: str) -> Dict:
        c = candles[idx]
        return {
            "timestamp":    c["timestamp"],
            "price":        round(c["close"], 8),
            "volume":       round(vol, 2),
            "avg_volume":   round(avg_vol, 2),
            "volume_ratio": round(vol / avg_vol, 2) if avg_vol > 0 else 0,
            "type":         kind,
            "candle_dir":   "bullish" if c["close"] >= c["open"] else "bearish",
        }

    return {
        "biggest_buy":  _entry(bi, buy_vols[bi],  "BUY"),
        "biggest_sell": _entry(si, sell_vols[si], "SELL"),
        "avg_volume":   round(avg_vol, 2),
    }


def find_pivots(
    candles: List[Dict], window: int = 3
) -> Tuple[List[Dict], List[Dict]]:
    highs = [c["high"] for c in candles]
    lows  = [c["low"]  for c in candles]
    ph, pl = [], []

    for i in range(window, len(candles) - window):
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, window + 1)):
            ph.append({"index": i, "price": highs[i], "timestamp": candles[i]["timestamp"]})

        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, window + 1)):
            pl.append({"index": i, "price": lows[i], "timestamp": candles[i]["timestamp"]})

    return ph, pl
