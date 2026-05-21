from typing import List, Dict, Optional, Tuple


def _line_slope(values: list) -> float:
    """Linear regression slope (units per bar)."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


# ── Flag pattern detection ─────────────────────────────────────────────────────

def detect_flags(candles: List[Dict], tf_label: str, tf_weight: float = 1.0,
                 min_pole_pct: float = 4.0) -> List[Dict]:
    """
    Detect bullish and bearish flag patterns in a candle list.

    A flag has two parts:
      Pole  — a sharp directional move (≥ min_pole_pct%) over 2-8 bars.
      Flag  — a consolidation channel (3-20 bars) that retraces 15-62% of the pole.

    Strength = pole_pct × (1 – retrace_fraction) × recency_bonus × tf_weight.
    The highest-strength flag per unique pole start is returned (max 6 total).
    """
    n = len(candles)
    if n < 10:
        return []

    candidates: List[Dict] = []

    for ps in range(n - 4):                       # pole start index
        for pe in range(ps + 2, min(ps + 9, n)):  # pole end (exclusive)
            pole_open  = candles[ps]["open"]
            pole_close = candles[pe - 1]["close"]
            pole_move  = (pole_close - pole_open) / (pole_open + 1e-12)

            if abs(pole_move) * 100 < min_pole_pct:
                continue

            pole_high  = max(c["high"] for c in candles[ps:pe])
            pole_low   = min(c["low"]  for c in candles[ps:pe])
            pole_height = pole_high - pole_low
            if pole_height < 1e-12:
                continue

            is_bull = pole_move > 0
            remaining = candles[pe:]
            if len(remaining) < 3:
                continue

            best: Optional[Dict] = None
            best_strength = 0.0

            for fl in range(3, min(21, len(remaining) + 1)):  # flag length
                flag = remaining[:fl]
                fh   = max(c["high"] for c in flag)
                fl_  = min(c["low"]  for c in flag)

                if is_bull:
                    retrace = (pole_close - fl_) / pole_height
                else:
                    retrace = (fh - pole_close) / pole_height

                if not (0.10 <= retrace <= 0.65):
                    continue

                # Recency bonus: patterns ending closer to the last candle score higher
                recency = 1.0 + (pe + fl) / n * 0.5

                direction = "bullish" if is_bull else "bearish"
                pole_pct  = round(abs(pole_move) * 100, 2)

                if is_bull:
                    target = round(fh + pole_height, 8)
                else:
                    target = round(max(fl_ - pole_height, pole_low * 0.5), 8)

                # ── Channel slope classification ──────────────────────────
                flag_highs = [c["high"] for c in flag]
                flag_lows  = [c["low"]  for c in flag]
                h_slope    = _line_slope(flag_highs)
                l_slope    = _line_slope(flag_lows)
                mid_slope  = (h_slope + l_slope) / 2.0
                mid_price  = (fh + fl_) / 2.0
                thresh     = mid_price * 0.001   # 0.1% per bar
                if mid_slope > thresh:
                    flag_slope = "ascending"
                elif mid_slope < -thresh:
                    flag_slope = "descending"
                else:
                    flag_slope = "neutral"
                slope_pct_per_bar = round(mid_slope / mid_price * 100, 4) if mid_price > 0 else 0.0

                # ── Confirmation: post-flag candle closed beyond flag boundary
                post = candles[pe + fl:]
                confirmed    = False
                breakout_dir = None
                if post:
                    if is_bull:
                        if any(c["close"] > fh  for c in post):
                            confirmed = True; breakout_dir = "up"
                        elif any(c["close"] < fl_ for c in post):
                            confirmed = True; breakout_dir = "down"
                    else:
                        if any(c["close"] < fl_ for c in post):
                            confirmed = True; breakout_dir = "down"
                        elif any(c["close"] > fh  for c in post):
                            confirmed = True; breakout_dir = "up"

                strength = pole_pct * (1.0 - retrace) * recency * tf_weight

                if strength > best_strength:
                    best_strength = strength
                    best = {
                        "direction":          direction,
                        "timeframe":          tf_label,
                        "tf_weight":          tf_weight,
                        "pole_pct":           pole_pct,
                        "pole_high":          round(pole_high, 8),
                        "pole_low":           round(pole_low,  8),
                        "pole_start_price":   round(pole_open,  8),
                        "pole_end_price":     round(pole_close, 8),
                        "flag_high":          round(fh,  8),
                        "flag_low":           round(fl_, 8),
                        "retrace_pct":        round(retrace * 100, 2),
                        "target":             target,
                        "strength":           round(strength, 3),
                        "consolidation_bars": fl,
                        "flag_slope":         flag_slope,
                        "slope_pct_per_bar":  slope_pct_per_bar,
                        "confirmed":          confirmed,
                        "breakout_dir":       breakout_dir,
                        "pole_start_ts":      candles[ps]["timestamp"],
                        "flag_end_ts":        flag[-1]["timestamp"],
                        "is_active":          (pe + fl) >= n - 3,
                    }

            if best:
                candidates.append(best)

    # Deduplicate by pole start — keep strongest per unique pole origin
    seen: Dict[int, Dict] = {}
    for f in candidates:
        key = f["pole_start_ts"]
        if key not in seen or f["strength"] > seen[key]["strength"]:
            seen[key] = f

    result = sorted(seen.values(), key=lambda f: f["strength"], reverse=True)
    return result[:6]


def pick_dominant_flags(all_flags: List[Dict]) -> List[Dict]:
    """
    From multi-timeframe flags, keep only the STRONGEST flag per
    (direction × timeframe) pair, pick the dominant direction at the highest
    tf_weight tier, then return sorted by (tf_weight × strength).
    """
    if not all_flags:
        return []

    # ── Deduplicate: keep strongest per (direction, timeframe) ───────────────
    best: Dict[tuple, Dict] = {}
    for f in all_flags:
        key = (f["direction"], f["timeframe"])
        if key not in best or f["strength"] > best[key]["strength"]:
            best[key] = f
    deduped = list(best.values())

    # ── Dominant direction at the highest tf_weight tier ─────────────────────
    max_weight = max(f["tf_weight"] for f in deduped)
    top_tier   = [f for f in deduped if f["tf_weight"] == max_weight]

    bull_score = sum(f["strength"] for f in top_tier if f["direction"] == "bullish")
    bear_score = sum(f["strength"] for f in top_tier if f["direction"] == "bearish")
    dominant   = "bullish" if bull_score >= bear_score else "bearish"

    for f in deduped:
        f["dominant"] = (f["tf_weight"] == max_weight and f["direction"] == dominant)

    return sorted(deduped, key=lambda f: f["tf_weight"] * f["strength"], reverse=True)


# ── Elliott Wave (unchanged) ───────────────────────────────────────────────────

def analyze_elliott_wave(
    candles: List[Dict],
    pivot_highs: List[Dict],
    pivot_lows: List[Dict],
) -> Dict:
    all_pivots = sorted(
        [{"type": "H", **p} for p in pivot_highs] + [{"type": "L", **p} for p in pivot_lows],
        key=lambda p: p["index"],
    )

    if len(all_pivots) < 5:
        return {
            "wave_count": "Insufficient data",
            "current_wave": None,
            "bias": "neutral",
            "trend": "neutral",
            "description": "Need more pivot data for wave analysis.",
            "targets": [],
        }

    recent = all_pivots[-12:]
    prices = [p["price"] for p in recent]
    trend  = "bullish" if prices[-1] > prices[0] else "bearish"

    swings = sum(
        1 for i in range(1, len(recent))
        if recent[i]["type"] != recent[i - 1]["type"]
    )

    _wave_labels = {
        1: ("Wave 1", "Impulse start — early entry for smart money"),
        2: ("Wave 2", "Corrective pullback — watch for reversal"),
        3: ("Wave 3", "Strongest impulse — ideal trend-following entry"),
        4: ("Wave 4", "Consolidation — prepare for Wave 5"),
        5: ("Wave 5", "Final push — consider taking profits"),
        6: ("Wave A", "Correction starts — reduce longs"),
        7: ("Wave B", "Dead-cat bounce — potential short entry"),
        8: ("Wave C", "Final corrective leg — accumulation zone"),
    }

    pos = (swings % 8) + 1
    label, desc = _wave_labels.get(pos, ("Unknown", "Unclear wave structure"))

    bullish_waves = {1, 3, 5, 7} if trend == "bullish" else {2, 4, 6, 8}
    bias = "bullish" if pos in bullish_waves else "bearish"

    current_price = candles[-1]["close"] if candles else prices[-1]
    targets = []
    if len(prices) >= 2:
        last_swing = min(abs(prices[-1] - prices[-2]), current_price * 0.25)
        for m in [0.618, 1.000, 1.618]:
            if bias == "bullish":
                t = round(current_price + last_swing * m, 6)
                if t > current_price:
                    targets.append(t)
            else:
                t = round(max(current_price * 0.001, current_price - last_swing * m), 6)
                if t < current_price:
                    targets.append(t)

    return {
        "wave_count":   label,
        "current_wave": pos,
        "bias":         bias,
        "trend":        trend,
        "description":  desc,
        "targets":      targets,
        "pivot_count":  len(all_pivots),
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
