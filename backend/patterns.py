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
      Pole  — a sharp directional move (≥ min_pole_pct%) over 2-8 bars,
              starting within the most-recent 50% of candles.
      Flag  — a consolidation channel (3-20 bars) that retraces 15-62% of the pole.

    Target projection is scaled by timeframe so shorter TFs don't inherit
    multi-month pole distances (4H→38%, up to 1W+→100%).

    Strength = pole_pct × (1 – retrace_fraction) × recency_bonus × tf_weight.
    The highest-strength flag per unique pole start is returned (max 6 total).
    """
    # Only work with fully closed candles — the last candle is still forming and
    # must never be used for pole, flag, or post-flag confirmation logic.
    closed = candles[:-1]
    n = len(closed)
    if n < 10:
        return []

    current_price = candles[-1]["close"]  # live price for proximity checks

    # How much of the pole height to project for the target, per TF.
    # Shorter TFs use Fibonacci fractions so the target stays in a realistic range.
    proj_frac = {
        "4H": 0.382, "8H": 0.50, "12H": 0.618,
        "1D": 0.75,  "1W": 1.0,  "2W": 1.0, "3W": 1.0, "1M": 1.0,
    }.get(tf_label, 1.0)

    candidates: List[Dict] = []

    # Pole must start in the second half of the candle set — prevents ancient
    # history poles (e.g. a 60% rally from 18 months ago) appearing on short TFs.
    earliest_pole_start = n // 2

    for ps in range(earliest_pole_start, n - 4):       # pole start index
        for pe in range(ps + 2, min(ps + 9, n)):        # pole end (exclusive)
            pole_open  = closed[ps]["open"]
            pole_close = closed[pe - 1]["close"]
            pole_move  = (pole_close - pole_open) / (pole_open + 1e-12)

            if abs(pole_move) * 100 < min_pole_pct:
                continue

            pole_high   = max(c["high"] for c in closed[ps:pe])
            pole_low    = min(c["low"]  for c in closed[ps:pe])
            pole_height = pole_high - pole_low
            if pole_height < 1e-12:
                continue

            is_bull   = pole_move > 0
            remaining = closed[pe:]
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

                proj = pole_height * proj_frac
                if is_bull:
                    raw_target = fh + proj
                    target = round(min(raw_target, current_price * 2.0), 8)
                else:
                    raw_target = fl_ - proj
                    target = round(max(raw_target, current_price * 0.20, pole_low * 0.5), 8)

                # ── Channel slope classification ──────────────────────────
                flag_highs = [c["high"] for c in flag]
                flag_lows  = [c["low"]  for c in flag]
                h_slope    = _line_slope(flag_highs)
                l_slope    = _line_slope(flag_lows)
                mid_slope  = (h_slope + l_slope) / 2.0
                mid_price  = (fh + fl_) / 2.0
                thresh     = mid_price * 0.001
                if mid_slope > thresh:
                    flag_slope = "ascending"
                elif mid_slope < -thresh:
                    flag_slope = "descending"
                else:
                    flag_slope = "neutral"
                slope_pct_per_bar = round(mid_slope / mid_price * 100, 4) if mid_price > 0 else 0.0

                # ── Confirmation: post-flag candle closed beyond flag boundary ──
                post = closed[pe + fl:]
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

                # is_active: price must still be INSIDE or just outside the flag zone.
                # A bullish flag is only relevant while price is above the flag low.
                # A bearish flag is only relevant while price is below the flag high.
                # 3% buffer covers minor wicks; >3% means price has genuinely exited
                # the zone in the wrong direction and the pattern is invalidated.
                flag_ended_recently = (pe + fl) >= n - 3
                if is_bull:
                    # Price must not have crashed more than 3% below flag low
                    price_near_flag = current_price >= fl_ * 0.97
                    # Target already hit: price has already reached or exceeded the target
                    target_hit = current_price >= target
                else:
                    # Price must not have surged more than 3% above flag high
                    price_near_flag = current_price <= fh * 1.03
                    # Target already hit: price has already reached or gone below the target
                    target_hit = current_price <= target
                is_active = flag_ended_recently and price_near_flag and not target_hit

                # Skip invalidated patterns — price has already moved through the
                # zone in the wrong direction, or has already reached the target.
                if not price_near_flag or target_hit:
                    continue

                # Also skip a confirmed flag whose breakout went the wrong way:
                # a bearish flag that broke UP is invalidated; a bullish that broke DOWN too.
                wrong_breakout = (confirmed and (
                    (is_bull and breakout_dir == "down") or
                    (not is_bull and breakout_dir == "up")
                ))
                if wrong_breakout:
                    continue

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
                        "proj_frac":          proj_frac,
                        "strength":           round(strength, 3),
                        "consolidation_bars": fl,
                        "flag_slope":         flag_slope,
                        "slope_pct_per_bar":  slope_pct_per_bar,
                        "confirmed":          confirmed,
                        "breakout_dir":       breakout_dir,
                        "pole_start_ts":      closed[ps]["timestamp"],
                        "flag_end_ts":        flag[-1]["timestamp"],
                        "is_active":          is_active,
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

    # Expose the last 10 pivots (with timestamps) so the frontend can
    # draw numbered wave markers on the candlestick chart.
    pivot_markers = [
        {"time": p["timestamp"], "type": p["type"], "price": p["price"]}
        for p in all_pivots[-10:]
    ]

    return {
        "wave_count":   label,
        "current_wave": pos,
        "bias":         bias,
        "trend":        trend,
        "description":  desc,
        "targets":      targets,
        "pivot_count":  len(all_pivots),
        "pivots":       pivot_markers,
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


def detect_choch(candles: List[Dict], window: int = 3) -> Dict:
    """
    Change of Character (CHoCH) — SMC market structure shift.

    Bullish CHoCH: price was making lower highs/lower lows (downtrend),
                   then breaks ABOVE the most recent swing high → structure flipped bullish.
    Bearish CHoCH: price was making higher highs/higher lows (uptrend),
                   then breaks BELOW the most recent swing low → structure flipped bearish.

    Returns: {
        signal:    'bullish' | 'bearish' | 'none'
        level:     price level that was broken
        candles_ago: how many candles ago the break occurred (freshness)
        broken_high: for bullish — the swing high that was broken
        broken_low:  for bearish — the swing low that was broken
    }
    """
    if len(candles) < window * 2 + 5:
        return {"signal": "none"}

    ph, pl = find_pivots(candles[:-1], window=window)  # exclude current candle for pivots
    if not ph or not pl:
        return {"signal": "none"}

    current = candles[-1]
    cur_close = current["close"]

    # Need at least 2 pivot highs and 2 pivot lows to establish trend
    # Bearish CHoCH: was uptrend (HH, HL) → price breaks below last swing low
    if len(pl) >= 2:
        last_low  = pl[-1]
        prev_low  = pl[-2]
        if last_low["price"] > prev_low["price"]:  # higher lows = uptrend
            if cur_close < last_low["price"]:
                candles_ago = len(candles) - 1 - last_low["index"]
                return {
                    "signal":     "bearish",
                    "level":      round(last_low["price"], 8),
                    "candles_ago": candles_ago,
                    "label":      f"Broke below swing low ${last_low['price']:,.4f}",
                }

    # Bullish CHoCH: was downtrend (LH, LL) → price breaks above last swing high
    if len(ph) >= 2:
        last_high = ph[-1]
        prev_high = ph[-2]
        if last_high["price"] < prev_high["price"]:  # lower highs = downtrend
            if cur_close > last_high["price"]:
                candles_ago = len(candles) - 1 - last_high["index"]
                return {
                    "signal":     "bullish",
                    "level":      round(last_high["price"], 8),
                    "candles_ago": candles_ago,
                    "label":      f"Broke above swing high ${last_high['price']:,.4f}",
                }

    return {"signal": "none"}


def detect_liquidity_grab(candles: List[Dict], window: int = 3, lookback: int = 5) -> Dict:
    """
    Liquidity Grab — wick sweeps a key swing level then closes back.

    Bearish grab: recent candle wick exceeded a swing HIGH but CLOSED below it
                  → stop hunt above highs, likely reversal down.
    Bullish grab: recent candle wick exceeded a swing LOW but CLOSED above it
                  → stop hunt below lows, likely reversal up.

    Returns: {
        signal:    'bullish' | 'bearish' | 'none'
        level:     the swing level that was swept
        wick_pct:  how far the wick exceeded the level (%)
        candles_ago: how recent (0 = current candle)
        label:     human-readable description
    }
    """
    if len(candles) < window * 2 + lookback + 2:
        return {"signal": "none"}

    # Find pivots on candles BEFORE the recent lookback window
    base_candles = candles[:-(lookback)]
    ph, pl = find_pivots(base_candles, window=window)
    if not ph and not pl:
        return {"signal": "none"}

    recent       = candles[-lookback:]
    current_price = candles[-1]["close"]
    best: Dict   = {"signal": "none"}
    best_wick    = 0.0
    # If price has moved >1.5% past the swept level AFTER the grab, the setup
    # is invalidated — bulls/bears won and the grab was a fakeout continuation.
    INVALIDATION_PCT = 1.5

    for i, c in enumerate(recent):
        candles_ago = lookback - 1 - i

        # Bearish grab: wick above swing high, closes below it
        for pivot in ph[-3:]:
            lvl = pivot["price"]
            if c["high"] > lvl and c["close"] < lvl:
                # Invalidated if current price is now clearly above the swept level
                if (current_price - lvl) / lvl * 100 > INVALIDATION_PCT:
                    continue
                wick_pct = (c["high"] - lvl) / lvl * 100
                if wick_pct > best_wick:
                    best_wick = wick_pct
                    best = {
                        "signal":      "bearish",
                        "level":       round(lvl, 8),
                        "wick_pct":    round(wick_pct, 3),
                        "candles_ago": candles_ago,
                        "label":       f"Wick swept high ${lvl:,.4f} (+{wick_pct:.2f}%), closed below",
                    }

        # Bullish grab: wick below swing low, closes above it
        for pivot in pl[-3:]:
            lvl = pivot["price"]
            if c["low"] < lvl and c["close"] > lvl:
                # Invalidated if current price has since fallen clearly below the level
                if (lvl - current_price) / lvl * 100 > INVALIDATION_PCT:
                    continue
                wick_pct = (lvl - c["low"]) / lvl * 100
                if wick_pct > best_wick:
                    best_wick = wick_pct
                    best = {
                        "signal":      "bullish",
                        "level":       round(lvl, 8),
                        "wick_pct":    round(wick_pct, 3),
                        "candles_ago": candles_ago,
                        "label":       f"Wick swept low ${lvl:,.4f} (-{wick_pct:.2f}%), closed above",
                    }

    return best
