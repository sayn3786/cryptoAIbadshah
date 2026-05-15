import numpy as np
from typing import List, Dict, Optional, Tuple


def calculate_rsi_series(closes: List[float], period: int = 14) -> List[Optional[float]]:
    if len(closes) < period + 1:
        return [None] * len(closes)

    result: List[Optional[float]] = [None] * len(closes)
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

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
        total = c.get("volume", 0.0)
        buy = c.get("taker_buy_volume", total / 2.0)
        sell = total - buy
        delta = buy - sell
        cvd += delta
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

    return {
        "current": round(cvd, 2),
        "trend": trend,
        "series": series[-30:],
        "label": label,
    }


def detect_fvg(candles: List[Dict], min_size_pct: float = 0.05) -> List[Dict]:
    fvgs: List[Dict] = []
    if len(candles) < 3:
        return fvgs

    current_price = candles[-1]["close"]

    for i in range(1, len(candles) - 1):
        prev = candles[i - 1]
        curr = candles[i]
        nxt = candles[i + 1]

        if prev["high"] < nxt["low"]:
            gap = (nxt["low"] - prev["high"]) / prev["high"] * 100
            if gap >= min_size_pct:
                mid = (nxt["low"] + prev["high"]) / 2
                fvgs.append(
                    {
                        "type": "bullish",
                        "top": round(nxt["low"], 8),
                        "bottom": round(prev["high"], 8),
                        "midpoint": round(mid, 8),
                        "size_pct": round(gap, 4),
                        "timestamp": curr["timestamp"],
                        "filled": current_price < prev["high"],
                        "distance_pct": round((current_price - mid) / current_price * 100, 2),
                    }
                )

        elif prev["low"] > nxt["high"]:
            gap = (prev["low"] - nxt["high"]) / prev["low"] * 100
            if gap >= min_size_pct:
                mid = (prev["low"] + nxt["high"]) / 2
                fvgs.append(
                    {
                        "type": "bearish",
                        "top": round(prev["low"], 8),
                        "bottom": round(nxt["high"], 8),
                        "midpoint": round(mid, 8),
                        "size_pct": round(gap, 4),
                        "timestamp": curr["timestamp"],
                        "filled": current_price > prev["low"],
                        "distance_pct": round((current_price - mid) / current_price * 100, 2),
                    }
                )

    fvgs.sort(key=lambda x: abs(x["distance_pct"]))
    return fvgs


def find_pivots(
    candles: List[Dict], window: int = 3
) -> Tuple[List[Dict], List[Dict]]:
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    ph, pl = [], []

    for i in range(window, len(candles) - window):
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, window + 1)
        ):
            ph.append({"index": i, "price": highs[i], "timestamp": candles[i]["timestamp"]})

        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, window + 1)
        ):
            pl.append({"index": i, "price": lows[i], "timestamp": candles[i]["timestamp"]})

    return ph, pl
