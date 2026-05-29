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

    Also computes the magnitude ratio (futures / spot) so callers know whether a
    "confirmed" move is genuinely organic or overwhelmingly futures-driven.

    Returns a dict with:
      type, label, detail, signal ("bullish"|"bearish"|"neutral"),
      futures_ratio (float), dominance ("futures" | "spot" | "balanced")
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
        return {"type": "neutral", "label": "Price ranging", "detail": "No clear price trend to compare CVDs against.", "signal": "neutral", "futures_ratio": None, "dominance": "balanced"}

    spot_trend = spot_cvd.get("trend", "neutral")
    fut_trend  = fut_cvd.get("trend",  "neutral")

    # Magnitude ratio: how much larger is futures CVD vs spot CVD (in absolute terms)?
    # Futures markets are naturally larger, but anything beyond ~10x signals speculative dominance.
    # Spot dominance (spot >> futures) = organic conviction; futures dominance = speculative.
    spot_abs    = abs(spot_cvd.get("current", 0) or 0)
    fut_abs     = abs(fut_cvd.get("current",  0) or 0)
    futures_ratio = round(fut_abs  / max(spot_abs, 1), 1)
    spot_ratio    = round(spot_abs / max(fut_abs,  1), 1)

    if futures_ratio > 50:
        dominance = "futures"
        dom_label = f"futures-dominated ({futures_ratio:.0f}× larger than spot)"
    elif futures_ratio > 10:
        dominance = "futures"
        dom_label = f"futures-heavy ({futures_ratio:.0f}× vs spot)"
    elif spot_ratio > 10:
        dominance = "spot"
        dom_label = f"spot-dominated ({spot_ratio:.0f}× larger than futures)"
    elif spot_ratio > 2:
        dominance = "spot"
        dom_label = f"spot-heavy ({spot_ratio:.1f}× vs futures)"
    else:
        dominance = "balanced"
        dom_label = f"balanced ({futures_ratio:.1f}× futures/spot)"

    def _result(type_, label, detail, signal):
        return {"type": type_, "label": label, "detail": detail, "signal": signal,
                "futures_ratio": futures_ratio, "spot_ratio": spot_ratio, "dominance": dominance}

    if price_trend == "up":
        if spot_trend == "bearish" and fut_trend == "bullish":
            return _result(
                "futures_led_up", "Futures-driven rally",
                "Price rising but spot CVD falling — futures buyers are pushing price up with no real spot demand. Rally may be unsustainable.",
                "bearish",
            )
        if spot_trend == "bullish" and fut_trend == "bearish":
            return _result(
                "spot_led_up", "Spot-driven rally",
                "Price rising with spot CVD confirming — genuine buying pressure. Futures are not chasing, suggesting a healthier, more sustained move.",
                "bullish",
            )
        if spot_trend == "bullish" and fut_trend == "bullish":
            if dominance == "futures" and futures_ratio > 50:
                return _result(
                    "futures_dominated_up", "Futures-dominated rally",
                    f"Both CVDs rising but futures ({dom_label}) — move is overwhelmingly speculative leverage, not organic. Elevated reversal risk.",
                    "bearish",
                )
            if dominance == "spot" and spot_ratio > 10:
                return _result(
                    "spot_dominated_up", "Spot-dominated rally",
                    f"Both CVDs rising but spot is {dom_label} — overwhelmingly organic buying with minimal leverage. Highest-conviction bullish signal.",
                    "bullish",
                )
            if dominance == "spot" and spot_ratio > 2:
                return _result(
                    "spot_heavy_up", "Spot-driven confirmed rally",
                    f"Both CVDs rising and spot is {dom_label} — real buyers leading, futures confirming. Strong organic conviction.",
                    "bullish",
                )
            return _result(
                "confirmed_up", "Confirmed rally",
                f"Both spot and futures CVD rising with price — strong confluence of real and speculative buying. {dom_label.capitalize()}.",
                "bullish",
            )

    if price_trend == "down":
        if spot_trend == "bullish" and fut_trend == "bearish":
            return _result(
                "futures_led_down", "Futures-driven selloff",
                "Price falling but spot CVD rising — futures sellers are pushing price down with no real spot selling. Short squeeze risk.",
                "bullish",
            )
        if spot_trend == "bearish" and fut_trend == "bullish":
            return _result(
                "spot_led_down", "Spot-driven selloff",
                "Price falling with spot CVD confirming — genuine distribution. Futures are not selling but spot sellers dominate.",
                "bearish",
            )
        if spot_trend == "bearish" and fut_trend == "bearish":
            if dominance == "futures" and futures_ratio > 50:
                return _result(
                    "futures_dominated_down", "Futures-dominated selloff",
                    f"Both CVDs falling but futures is {dom_label} — selling is overwhelmingly speculative shorts, not real holder distribution. Short squeeze risk elevated.",
                    "neutral",   # downgraded from bearish — unreliable organic signal
                )
            if dominance == "futures" and futures_ratio > 10:
                return _result(
                    "futures_heavy_down", "Futures-heavy selloff",
                    f"Both CVDs falling but futures is {dom_label} — speculative selling heavier than organic. Some squeeze risk.",
                    "bearish",   # still bearish but lower conviction
                )
            if dominance == "spot" and spot_ratio > 10:
                return _result(
                    "spot_dominated_down", "Spot-dominated selloff",
                    f"Both CVDs falling but spot is {dom_label} — overwhelmingly real holder distribution with minimal leverage. Highest-conviction bearish signal.",
                    "bearish",
                )
            if dominance == "spot" and spot_ratio > 2:
                return _result(
                    "spot_heavy_down", "Spot-driven confirmed selloff",
                    f"Both CVDs falling and spot is {dom_label} — real sellers leading, futures confirming. Strong organic distribution.",
                    "bearish",
                )
            return _result(
                "confirmed_down", "Confirmed selloff",
                f"Both spot and futures CVD falling with price — strong confluence of real and speculative selling. {dom_label.capitalize()}.",
                "bearish",
            )

    return _result("neutral", "CVDs aligned with price", "No significant divergence between spot and futures CVD.", "neutral")



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


def detect_whale_activity(candles: List[Dict], lookback: int = 20,
                           vol_threshold: float = 2.5,
                           taker_threshold: float = 0.65,
                           detect_window: int = 5) -> List[Dict]:
    """
    Detect candles with abnormally high volume — potential whale entries.
    Checks last detect_window closed candles against a lookback average.
    Direction is determined by taker buy/sell ratio + price action.
    """
    if len(candles) < lookback + 2:
        return []

    closed = candles[:-1]  # exclude current forming candle
    if len(closed) < lookback + 1:
        return []

    results = []
    start = max(lookback, len(closed) - detect_window)

    for i in range(start, len(closed)):
        c       = closed[i]
        vol     = c.get("volume", 0)
        if vol == 0:
            continue
        avg_vol = sum(x.get("volume", 0) for x in closed[i - lookback:i]) / lookback
        if avg_vol == 0:
            continue
        multiple = vol / avg_vol
        if multiple < vol_threshold:
            continue

        taker_buy   = c.get("taker_buy_volume", vol * 0.5)
        taker_ratio = taker_buy / vol

        open_p  = c.get("open",  0)
        close_p = c.get("close", 0)
        body_pct = (close_p - open_p) / open_p * 100 if open_p else 0

        bull_taker = taker_ratio >= taker_threshold
        bear_taker = taker_ratio <= (1 - taker_threshold)
        is_doji    = abs(body_pct) < 0.5  # tiny body = absorption / indecision

        if is_doji and bull_taker:
            direction = "absorption_bull"   # heavy buying but price pinned → defending resistance or absorbed by sellers
        elif is_doji and bear_taker:
            direction = "absorption_bear"   # heavy selling but price held → defending support or absorbed by buyers
        elif bull_taker and body_pct > 0:
            direction = "bullish"
        elif bear_taker and body_pct < 0:
            direction = "bearish"
        elif bull_taker and body_pct < 0:
            direction = "bearish_rejection" # bought hard but price rejected — failed breakout
        elif bear_taker and body_pct > 0:
            direction = "bullish_absorption"# sold hard but buyers absorbed it — bullish signal
        elif body_pct > 0:
            direction = "bullish"
        else:
            direction = "bearish"

        results.append({
            "timestamp":    c["timestamp"],
            "direction":    direction,
            "vol_multiple": round(multiple, 1),
            "taker_ratio":  round(taker_ratio * 100, 1),
            "price_impact": round(abs(body_pct), 2),
            "body_pct":     round(body_pct, 2),
            "candles_ago":  len(closed) - i,
            "close":        round(close_p, 8),
        })

    return sorted(results, key=lambda x: x["candles_ago"])


def calculate_supertrend(candles: List[Dict], period: int = 22, multiplier: float = 3.0) -> Dict:
    """SuperTrend indicator.  Returns current direction, value and whether a
    flip (new signal) occurred on the most recent closed candle."""
    if len(candles) < period + 1:
        return {"direction": None, "value": None, "signal": None, "flipped": False}

    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]

    # True Range
    trs = [highs[0] - lows[0]]
    for i in range(1, len(candles)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i]  - closes[i - 1])))

    # Wilder ATR (RMA)
    atr = [None] * len(candles)
    atr[period - 1] = sum(trs[:period]) / period
    for i in range(period, len(candles)):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period

    up  = [None] * len(candles)
    dn  = [None] * len(candles)
    trend = [None] * len(candles)   # 1 = bullish, -1 = bearish

    for i in range(period - 1, len(candles)):
        if atr[i] is None:
            continue
        mid  = (highs[i] + lows[i]) / 2
        b_up = mid + multiplier * atr[i]
        b_dn = mid - multiplier * atr[i]

        if i == period - 1:
            up[i]    = b_up
            dn[i]    = b_dn
            trend[i] = 1 if closes[i] > b_dn else -1
            continue

        # Carry-forward logic — upper band only lowers, lower band only rises
        up[i] = b_up if (b_up < up[i - 1] or closes[i - 1] > up[i - 1]) else up[i - 1]
        dn[i] = b_dn if (b_dn > dn[i - 1] or closes[i - 1] < dn[i - 1]) else dn[i - 1]

        if trend[i - 1] == -1 and closes[i] > up[i]:
            trend[i] = 1
        elif trend[i - 1] == 1 and closes[i] < dn[i]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

    last  = len(candles) - 1
    prev  = last - 1
    t_now = trend[last]
    t_pre = trend[prev] if prev >= 0 else t_now

    direction = "bullish" if t_now == 1 else "bearish"
    value     = round(dn[last] if t_now == 1 else up[last], 8)
    flipped   = t_now != t_pre
    signal    = ("BUY" if t_now == 1 else "SELL") if flipped else None

    return {"direction": direction, "value": value, "signal": signal, "flipped": flipped}


def calculate_ichimoku(candles: List[Dict],
                       tenkan_period: int = 9,
                       kijun_period: int  = 26,
                       senkou_b_period: int = 52,
                       displacement: int  = 26) -> Dict:
    """Ichimoku Cloud. Returns:
    - tenkan, kijun (current values)
    - span_a, span_b (current cloud boundaries — the displaced values at today's bar)
    - cloud_color: 'green' (bullish) | 'red' (bearish)
    - price_vs_cloud: 'above' | 'inside' | 'below'
    - tk_cross: 'bullish' | 'bearish' | 'neutral'
    """
    if len(candles) < senkou_b_period + displacement:
        return {
            "tenkan": None, "kijun": None,
            "span_a": None, "span_b": None,
            "cloud_color": None, "price_vs_cloud": None, "tk_cross": None,
        }

    def _mid(cs, period, idx):
        start = idx - period + 1
        if start < 0:
            return None
        window = cs[start: idx + 1]
        return (max(c["high"] for c in window) + min(c["low"] for c in window)) / 2

    n = len(candles)
    i = n - 1  # most recent bar index

    tenkan = _mid(candles, tenkan_period, i)
    kijun  = _mid(candles, kijun_period,  i)

    # Span A and B are calculated `displacement` bars back (so they appear at current bar)
    back = i - displacement
    if back < 0:
        span_a = span_b = None
    else:
        t_back = _mid(candles, tenkan_period, back)
        k_back = _mid(candles, kijun_period,  back)
        span_a = (t_back + k_back) / 2 if (t_back and k_back) else None
        span_b = _mid(candles, senkou_b_period, back)

    close = candles[i]["close"]

    cloud_color    = None
    price_vs_cloud = None
    if span_a is not None and span_b is not None:
        cloud_top = max(span_a, span_b)
        cloud_bot = min(span_a, span_b)
        cloud_color = "green" if span_a >= span_b else "red"
        if close > cloud_top:
            price_vs_cloud = "above"
        elif close < cloud_bot:
            price_vs_cloud = "below"
        else:
            price_vs_cloud = "inside"

    tk_cross = "neutral"
    if tenkan is not None and kijun is not None:
        prev_i  = i - 1
        p_tenkan = _mid(candles, tenkan_period, prev_i)
        p_kijun  = _mid(candles, kijun_period,  prev_i)
        if p_tenkan is not None and p_kijun is not None:
            if p_tenkan <= p_kijun and tenkan > kijun:
                tk_cross = "bullish"
            elif p_tenkan >= p_kijun and tenkan < kijun:
                tk_cross = "bearish"

    def _r(v):
        return round(v, 8) if v is not None else None

    return {
        "tenkan":        _r(tenkan),
        "kijun":         _r(kijun),
        "span_a":        _r(span_a),
        "span_b":        _r(span_b),
        "cloud_color":   cloud_color,
        "price_vs_cloud": price_vs_cloud,
        "tk_cross":      tk_cross,
    }


def calculate_bollinger_bands(candles: List[Dict], period: int = 20, std_dev: float = 2.0) -> Dict:
    """Bollinger Bands with squeeze and breakout detection.

    Squeeze: bandwidth is below its own 50-candle average — price compressed,
             explosive move likely imminent.
    Breakout: close above upper band after a squeeze = bullish momentum burst.
    Breakdown: close below lower band after a squeeze = bearish momentum burst.
    """
    if len(candles) < period:
        return {
            "upper": None, "middle": None, "lower": None,
            "bandwidth": None, "squeeze": False,
            "breakout": None, "pct_b": None,
        }

    closes = [c["close"] for c in candles]

    def _sma_std(window):
        sma = sum(window) / len(window)
        variance = sum((x - sma) ** 2 for x in window) / len(window)
        return sma, variance ** 0.5

    # Current band
    sma, sd = _sma_std(closes[-period:])
    upper  = sma + std_dev * sd
    lower  = sma - std_dev * sd
    bw     = (upper - lower) / sma if sma > 0 else 0.0   # normalised bandwidth

    # Rolling bandwidth history for squeeze detection (last 50 candles)
    bw_series = []
    for i in range(max(0, len(closes) - 50), len(closes) - period + 1):
        s, d_ = _sma_std(closes[i: i + period])
        if s > 0:
            bw_series.append((s + std_dev * d_ - (s - std_dev * d_)) / s)

    squeeze = bool(bw_series and bw < sum(bw_series) / len(bw_series) * 0.85)

    # %B: where current price sits within the band (0 = lower, 1 = upper)
    price  = closes[-1]
    pct_b  = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    # Breakout / breakdown on the last candle
    breakout = None
    if price > upper:
        breakout = "bullish"
    elif price < lower:
        breakout = "bearish"

    return {
        "upper":     round(upper, 8),
        "middle":    round(sma,   8),
        "lower":     round(lower, 8),
        "bandwidth": round(bw,    6),
        "squeeze":   squeeze,
        "breakout":  breakout,
        "pct_b":     round(pct_b, 4),
    }


def detect_rsi_divergence(candles: List[Dict], rsi_series: List[Optional[float]],
                           lookback: int = 30, pivot_window: int = 3) -> Dict:
    """Pivot-based RSI divergence. Finds actual swing lows/highs rather than
    splitting the window in half — more accurate and fewer false positives."""
    empty = {"type": None, "strength": None, "description": None}
    if len(candles) < lookback or len(rsi_series) < lookback:
        return empty

    pairs = [(c, r) for c, r in zip(candles[-lookback:], rsi_series[-lookback:]) if r is not None]
    if len(pairs) < pivot_window * 2 + 4:
        return empty

    lows  = [c["low"]  for c, _ in pairs]
    highs = [c["high"] for c, _ in pairs]
    rsi_v = [r         for _, r in pairs]
    n     = len(pairs)
    pw    = pivot_window

    swing_lows  = [(i, lows[i],  rsi_v[i]) for i in range(pw, n - pw)
                   if lows[i]  == min(lows[i - pw: i + pw + 1])]
    swing_highs = [(i, highs[i], rsi_v[i]) for i in range(pw, n - pw)
                   if highs[i] == max(highs[i - pw: i + pw + 1])]

    if len(swing_lows) >= 2:
        _, p_price, p_rsi = swing_lows[-2]
        _, c_price, c_rsi = swing_lows[-1]
        if (p_price - c_price) / (p_price + 1e-12) > 0.005 and c_rsi - p_rsi > 2:
            return {"type": "bullish", "strength": round(c_rsi - p_rsi, 1),
                    "description": f"Bullish RSI divergence — price lower low but RSI rising (+{c_rsi - p_rsi:.1f} pts), classic reversal setup"}

    if len(swing_highs) >= 2:
        _, p_price, p_rsi = swing_highs[-2]
        _, c_price, c_rsi = swing_highs[-1]
        if (c_price - p_price) / (p_price + 1e-12) > 0.005 and p_rsi - c_rsi > 2:
            return {"type": "bearish", "strength": round(p_rsi - c_rsi, 1),
                    "description": f"Bearish RSI divergence — price higher high but RSI falling (-{p_rsi - c_rsi:.1f} pts), classic reversal setup"}

    return empty


def calculate_vwap(candles: List[Dict], period: int = 50) -> Dict:
    """Rolling VWAP over `period` candles with slope and cross detection."""
    if len(candles) < 5:
        return {"vwap": None, "slope": None, "price_vs_vwap": None, "vwap_cross": None}

    window = candles[-period:] if len(candles) >= period else candles
    total_vol = sum(c["volume"] for c in window)
    if total_vol <= 0:
        return {"vwap": None, "slope": None, "price_vs_vwap": None, "vwap_cross": None}

    vwap = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in window) / total_vol

    # Slope: compare to VWAP calculated on the window shifted 5 candles back
    slope = "flat"
    if len(candles) >= period + 5:
        prev_w = candles[-(period + 5):-5]
        prev_vol = sum(c["volume"] for c in prev_w)
        if prev_vol > 0:
            prev_vwap = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in prev_w) / prev_vol
            if vwap > prev_vwap * 1.001:
                slope = "rising"
            elif vwap < prev_vwap * 0.999:
                slope = "falling"

    close     = candles[-1]["close"]
    prev_close = candles[-2]["close"] if len(candles) >= 2 else close
    price_vs_vwap = "above" if close > vwap else "below"

    vwap_cross = None
    if prev_close <= vwap and close > vwap:
        vwap_cross = "bullish"
    elif prev_close >= vwap and close < vwap:
        vwap_cross = "bearish"

    return {
        "vwap":          round(vwap, 8),
        "slope":         slope,
        "price_vs_vwap": price_vs_vwap,
        "vwap_cross":    vwap_cross,
    }


def calculate_stoch_rsi(closes: List[float], rsi_period: int = 14,
                         stoch_period: int = 14, k_period: int = 3,
                         d_period: int = 3) -> Dict:
    """Stochastic RSI — RSI of RSI, more sensitive for short-term momentum."""
    min_len = rsi_period + stoch_period + k_period + d_period + 2
    if len(closes) < min_len:
        return {"k": None, "d": None, "signal": None, "zone": None}

    rsi_vals = [r for r in calculate_rsi_series(closes, rsi_period) if r is not None]
    if len(rsi_vals) < stoch_period:
        return {"k": None, "d": None, "signal": None, "zone": None}

    raw_k = []
    for i in range(stoch_period - 1, len(rsi_vals)):
        w = rsi_vals[i - stoch_period + 1: i + 1]
        lo, hi = min(w), max(w)
        raw_k.append((rsi_vals[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0)

    def _sma(vals, n):
        return [sum(vals[i: i + n]) / n for i in range(len(vals) - n + 1)]

    k_line = _sma(raw_k, k_period)
    if len(k_line) < d_period:
        return {"k": None, "d": None, "signal": None, "zone": None}
    d_line = _sma(k_line, d_period)

    k, d           = round(k_line[-1], 2), round(d_line[-1], 2)
    prev_k, prev_d = (round(k_line[-2], 2), round(d_line[-2], 2)) if len(k_line) >= 2 and len(d_line) >= 2 else (k, d)

    if k < 20 and d < 20:
        signal = "bull_cross_oversold" if prev_k <= prev_d and k > d else "oversold"
    elif k > 80 and d > 80:
        signal = "bear_cross_overbought" if prev_k >= prev_d and k < d else "overbought"
    elif k < 30:
        signal = "near_oversold"
    elif k > 70:
        signal = "near_overbought"
    else:
        signal = "neutral"

    zone = "oversold" if k < 20 else ("overbought" if k > 80 else "neutral")
    return {"k": k, "d": d, "signal": signal, "zone": zone}


def calculate_volume_signal(candles: List[Dict], lookback: int = 20) -> Dict:
    """Volume confirmation — elevated volume on directional candles."""
    if len(candles) < lookback + 2:
        return {"signal": None, "ratio": None, "description": None}

    closed  = candles[:-1]
    avg_vol = sum(c["volume"] for c in closed[-lookback:]) / lookback
    if avg_vol <= 0:
        return {"signal": None, "ratio": None, "description": None}

    best_ratio, best_signal, best_desc = 0.0, None, None
    for c in reversed(closed[-3:]):
        ratio = c["volume"] / avg_vol
        if ratio < 1.3:
            continue
        body    = c["close"] - c["open"]
        rng     = c["high"]  - c["low"]
        body_pct = body / rng if rng > 0 else 0
        if abs(body_pct) < 0.4:
            continue          # indecision candle — skip
        direction = "bullish" if body_pct > 0 else "bearish"
        if ratio > best_ratio:
            best_ratio  = ratio
            best_signal = direction
            label = "Strong" if ratio >= 2.0 else "Elevated"
            best_desc = f"{label} volume ({ratio:.1f}× avg) on {direction} candle — confirms move"

    if not best_signal:
        return {"signal": None, "ratio": None, "description": None}
    return {"signal": best_signal, "ratio": round(best_ratio, 2), "description": best_desc}

