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


def detect_cvd_divergence(spot_cvd: Dict, fut_cvd: Dict, candles: List[Dict]) -> Dict:
    """
    Compare price direction vs spot/futures CVD trend to detect who is actually
    driving the move — spot buyers/sellers or futures speculators.

    Returns a dict with: type, label, detail, signal ("bullish"|"bearish"|"neutral")
    """
    if not spot_cvd or not fut_cvd or not candles or len(candles) < 5:
        return {}

    closes = [c["close"] for c in candles[-5:]]
    price_chg = (closes[-1] - closes[0]) / (closes[0] + 1e-9)
    if price_chg > 0.005:
        price_trend = "up"
    elif price_chg < -0.005:
        price_trend = "down"
    else:
        return {"type": "neutral", "label": "Price ranging", "detail": "No clear price trend to compare CVDs against.", "signal": "neutral"}

    spot_trend = spot_cvd.get("trend", "neutral")
    fut_trend  = fut_cvd.get("trend",  "neutral")

    if price_trend == "up":
        if spot_trend == "bearish" and fut_trend == "bullish":
            return {
                "type":   "futures_led_up",
                "label":  "Futures-driven rally",
                "detail": "Price rising but spot CVD falling — futures buyers are pushing price up with no real spot demand. Rally may be unsustainable.",
                "signal": "bearish",
            }
        if spot_trend == "bullish" and fut_trend == "bearish":
            return {
                "type":   "spot_led_up",
                "label":  "Spot-driven rally",
                "detail": "Price rising with spot CVD confirming — genuine buying pressure. Futures are not chasing, suggesting a healthier, more sustained move.",
                "signal": "bullish",
            }
        if spot_trend == "bullish" and fut_trend == "bullish":
            return {
                "type":   "confirmed_up",
                "label":  "Confirmed rally",
                "detail": "Both spot and futures CVD rising with price — strong confluence of real and speculative buying.",
                "signal": "bullish",
            }

    if price_trend == "down":
        if spot_trend == "bullish" and fut_trend == "bearish":
            return {
                "type":   "futures_led_down",
                "label":  "Futures-driven selloff",
                "detail": "Price falling but spot CVD rising — futures sellers are pushing price down with no real spot selling. Short squeeze risk.",
                "signal": "bullish",
            }
        if spot_trend == "bearish" and fut_trend == "bullish":
            return {
                "type":   "spot_led_down",
                "label":  "Spot-driven selloff",
                "detail": "Price falling with spot CVD confirming — genuine distribution. Futures are not selling but spot sellers dominate.",
                "signal": "bearish",
            }
        if spot_trend == "bearish" and fut_trend == "bearish":
            return {
                "type":   "confirmed_down",
                "label":  "Confirmed selloff",
                "detail": "Both spot and futures CVD falling with price — strong confluence of real and speculative selling.",
                "signal": "bearish",
            }

    return {"type": "neutral", "label": "CVDs aligned with price", "detail": "No significant divergence between spot and futures CVD.", "signal": "neutral"}



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


def detect_engulfing(candles: List[Dict], lookback: int = 4) -> List[Dict]:
    """Detect confirmed bullish/bearish engulfing patterns.

    Only checks closed candles — candles[-1] (still forming) is excluded.
    """
    patterns: List[Dict] = []
    # Exclude the last (potentially forming) candle
    closed = candles[:-1]
    if len(closed) < 2:
        return patterns

    current_price = candles[-1]["close"]

    start = max(1, len(closed) - lookback)
    for i in range(start, len(closed)):
        prev = closed[i - 1]
        curr = closed[i]

        prev_body = abs(prev["close"] - prev["open"])
        curr_body = abs(curr["close"] - curr["open"])
        if prev_body < 1e-9 or curr_body < 1e-9:
            continue

        # Skip if the engulfing candle's price range is more than 20% away from
        # current price — avoids showing stale patterns from weeks/months ago
        # when price has already moved significantly
        candle_mid = (curr["high"] + curr["low"]) / 2.0
        if current_price > 0 and abs(candle_mid - current_price) / current_price > 0.20:
            continue

        candles_ago = len(closed) - i  # 1 = most recent confirmed candle

        base = {
            "timestamp":   curr["timestamp"],
            "confirmed":   True,
            "candles_ago": candles_ago,
            "body_ratio":  round(curr_body / prev_body, 2),
            "engulf_open": round(curr["open"],  8),
            "engulf_close": round(curr["close"], 8),
            "prev_open":   round(prev["open"],  8),
            "prev_close":  round(prev["close"], 8),
        }

        # Bullish engulfing: prev bearish, curr bullish, body engulfs prev body
        if (prev["close"] < prev["open"]
                and curr["close"] > curr["open"]
                and curr["open"]  <= prev["close"]
                and curr["close"] >= prev["open"]
                and curr_body > prev_body):
            patterns.append({**base, "type": "bullish_engulfing", "direction": "bullish"})

        # Bearish engulfing: prev bullish, curr bearish, body engulfs prev body
        elif (prev["close"] > prev["open"]
                and curr["close"] < curr["open"]
                and curr["open"]  >= prev["close"]
                and curr["close"] <= prev["open"]
                and curr_body > prev_body):
            patterns.append({**base, "type": "bearish_engulfing", "direction": "bearish"})

    # Keep only the strongest per direction (highest body_ratio, tie-broken by recency).
    # Two bearish engulfings in the same lookback window is noise — only the best matters.
    best: Dict[str, dict] = {}
    for p in patterns:
        d = p["direction"]
        if d not in best:
            best[d] = p
        else:
            existing = best[d]
            # Prefer more recent; if same recency prefer larger body ratio
            if p["candles_ago"] < existing["candles_ago"]:
                best[d] = p
            elif p["candles_ago"] == existing["candles_ago"] and p["body_ratio"] > existing["body_ratio"]:
                best[d] = p

    return list(best.values())


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


def calculate_macd(closes: List[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> Dict:
    """MACD = EMA(fast) - EMA(slow). Signal = EMA(signal_period) of MACD. Histogram = MACD - Signal."""
    def _ema(values: List[float], period: int) -> List[Optional[float]]:
        out: List[Optional[float]] = [None] * len(values)
        if len(values) < period:
            return out
        out[period - 1] = sum(values[:period]) / period
        k = 2.0 / (period + 1)
        for i in range(period, len(values)):
            out[i] = values[i] * k + out[i - 1] * (1 - k)
        return out

    if len(closes) < slow + signal_period:
        return {"macd": None, "signal_line": None, "histogram": None, "cross": None, "trend": "neutral"}

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    macd_line: List[Optional[float]] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]

    # Build signal line as EMA of macd_line values
    valid_pairs = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    if len(valid_pairs) < signal_period:
        return {"macd": None, "signal_line": None, "histogram": None, "cross": None, "trend": "neutral"}

    sig_line: List[Optional[float]] = [None] * len(macd_line)
    start_idx = valid_pairs[signal_period - 1][0]
    sig_line[start_idx] = sum(v for _, v in valid_pairs[:signal_period]) / signal_period
    k_sig = 2.0 / (signal_period + 1)
    for i in range(start_idx + 1, len(macd_line)):
        if macd_line[i] is not None and sig_line[i - 1] is not None:
            sig_line[i] = macd_line[i] * k_sig + sig_line[i - 1] * (1 - k_sig)

    cur_macd = macd_line[-1]
    cur_sig  = sig_line[-1]
    prev_macd = next((v for v in reversed(macd_line[:-1]) if v is not None), None)
    prev_sig  = next((v for v in reversed(sig_line[:-1])  if v is not None), None)

    histogram = round(cur_macd - cur_sig, 8) if cur_macd is not None and cur_sig is not None else None

    cross = None
    if all(v is not None for v in [cur_macd, cur_sig, prev_macd, prev_sig]):
        if prev_macd <= prev_sig and cur_macd > cur_sig:
            cross = "bullish"
        elif prev_macd >= prev_sig and cur_macd < cur_sig:
            cross = "bearish"

    # Also detect histogram zero-cross (more bars back)
    prev_hist = (prev_macd - prev_sig) if prev_macd is not None and prev_sig is not None else None
    zero_cross = None
    if histogram is not None and prev_hist is not None:
        if prev_hist <= 0 and histogram > 0:
            zero_cross = "bullish"
        elif prev_hist >= 0 and histogram < 0:
            zero_cross = "bearish"

    trend = "neutral"
    if histogram is not None:
        trend = "bullish" if histogram > 0 else "bearish"

    return {
        "macd":       round(cur_macd, 8) if cur_macd is not None else None,
        "signal_line": round(cur_sig,  8) if cur_sig  is not None else None,
        "histogram":  histogram,
        "cross":      cross,
        "zero_cross": zero_cross,
        "trend":      trend,
    }


def calculate_ema_trend(closes: List[float]) -> Dict:
    """Compute EMA20, EMA50, EMA200 and classify where price sits relative to each."""
    def _ema_val(values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    if not closes:
        return {}

    price  = closes[-1]
    ema20  = _ema_val(closes, 20)
    ema50  = _ema_val(closes, 50)
    ema200 = _ema_val(closes, 200)

    above, below = [], []
    for period, val in [(20, ema20), (50, ema50), (200, ema200)]:
        if val is None:
            continue
        (above if price > val else below).append(period)

    # Overall trend classification
    if 50 in above and (200 in above or 200 not in (above + below)):
        trend = "bullish"
    elif 50 in below and (200 in below or 200 not in (above + below)):
        trend = "bearish"
    elif 50 in above:
        trend = "mixed_bullish"
    elif 50 in below:
        trend = "mixed_bearish"
    else:
        trend = "neutral"

    return {
        "price":  round(price, 8),
        "ema20":  round(ema20,  8) if ema20  is not None else None,
        "ema50":  round(ema50,  8) if ema50  is not None else None,
        "ema200": round(ema200, 8) if ema200 is not None else None,
        "above":  above,
        "below":  below,
        "trend":  trend,
    }
