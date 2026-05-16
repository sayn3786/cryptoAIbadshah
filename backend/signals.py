from typing import Dict, List


def generate_signal(analysis: Dict) -> Dict:
    score = 0
    bull_reasons: List[str] = []
    bear_reasons: List[str] = []

    rsi = analysis.get("rsi")
    spot_cvd    = analysis.get("spot_cvd") or {}
    futures_cvd = analysis.get("futures_cvd") or {}
    funding     = analysis.get("funding_rate") or {}
    oi          = analysis.get("open_interest") or {}
    fvgs        = analysis.get("fvgs") or []
    flags       = analysis.get("flags") or []
    elliott     = analysis.get("elliott_wave") or {}
    candles     = analysis.get("candles") or []

    current_price = candles[-1]["close"] if candles else 0.0

    # ── RSI ──────────────────────────────────────────────────────────────────
    if rsi is not None:
        if rsi < 25:
            score += 30
            bull_reasons.append(f"RSI extremely oversold ({rsi})")
        elif rsi < 35:
            score += 18
            bull_reasons.append(f"RSI oversold ({rsi})")
        elif rsi < 45:
            score += 8
            bull_reasons.append(f"RSI approaching oversold ({rsi})")
        elif rsi > 75:
            score -= 30
            bear_reasons.append(f"RSI extremely overbought ({rsi})")
        elif rsi > 65:
            score -= 18
            bear_reasons.append(f"RSI overbought ({rsi})")
        elif rsi > 55:
            score -= 8
            bear_reasons.append(f"RSI elevated ({rsi})")

    # ── Spot CVD ─────────────────────────────────────────────────────────────
    cvd_trend = spot_cvd.get("trend", "neutral")
    if cvd_trend == "bullish":
        score += 15
        bull_reasons.append("Spot CVD trending up — net buying pressure")
    elif cvd_trend == "bearish":
        score -= 15
        bear_reasons.append("Spot CVD trending down — net selling pressure")

    # ── Futures CVD ──────────────────────────────────────────────────────────
    f_cvd_trend = futures_cvd.get("trend", "neutral")
    if f_cvd_trend == "bullish":
        score += 10
        bull_reasons.append("Futures CVD bullish — institutional demand")
    elif f_cvd_trend == "bearish":
        score -= 10
        bear_reasons.append("Futures CVD bearish — institutional supply")

    # ── Funding Rate ─────────────────────────────────────────────────────────
    fr = funding.get("current", 0.0) or 0.0
    if fr < -0.02:
        score += 25
        bull_reasons.append(f"Very negative funding ({fr:.4f}%) — shorts overextended")
    elif fr < -0.005:
        score += 12
        bull_reasons.append(f"Negative funding ({fr:.4f}%) — favours longs")
    elif fr > 0.04:
        score -= 25
        bear_reasons.append(f"Very high funding ({fr:.4f}%) — longs overextended")
    elif fr > 0.015:
        score -= 12
        bear_reasons.append(f"Elevated funding ({fr:.4f}%) — caution for longs")

    # ── Open Interest ─────────────────────────────────────────────────────────
    oi_change = oi.get("change_pct", 0.0) or 0.0
    if len(candles) >= 5:
        prev_price = candles[-5]["close"]
        price_up = current_price > prev_price
        if oi_change > 5:
            if price_up:
                score += 12
                bull_reasons.append(f"OI +{oi_change:.1f}% with rising price — bullish")
            else:
                score -= 12
                bear_reasons.append(f"OI +{oi_change:.1f}% with falling price — bearish")
        elif oi_change < -5:
            if price_up:
                score += 8
                bull_reasons.append(f"OI declining ({oi_change:.1f}%) with rising price — shorts being squeezed out")
            else:
                score -= 8
                bear_reasons.append(f"OI declining ({oi_change:.1f}%) with falling price — longs capitulating")

    # ── Fair Value Gaps ───────────────────────────────────────────────────────
    unfilled = [f for f in fvgs if not f["filled"]]
    below = [f for f in unfilled if f["type"] == "bullish" and f["midpoint"] < current_price]
    above = [f for f in unfilled if f["type"] == "bearish" and f["midpoint"] > current_price]

    if below:
        score += min(len(below) * 8, 20)
        bull_reasons.append(
            f"{len(below)} bullish FVG(s) acting as support (nearest: ${below[0]['midpoint']:,.4f})"
        )
    if above:
        score -= min(len(above) * 8, 20)
        bear_reasons.append(
            f"{len(above)} bearish FVG(s) as resistance (nearest: ${above[0]['midpoint']:,.4f})"
        )

    # ── Flag Patterns ─────────────────────────────────────────────────────────
    for f in flags:
        if not f.get("is_active"):
            continue
        bonus = 20 if f.get("dominant") else 10
        if f["direction"] == "bullish":
            score += bonus
            bull_reasons.append(
                f"{'Dominant b' if f.get('dominant') else 'B'}ullish flag active on {f['timeframe']} "
                f"(+{f['pole_pct']}% pole, target ${f['target']:,.4f})"
            )
        else:
            score -= bonus
            bear_reasons.append(
                f"{'Dominant b' if f.get('dominant') else 'B'}earish flag active on {f['timeframe']} "
                f"({f['pole_pct']}% pole, target ${f['target']:,.4f})"
            )

    # ── Engulfing Patterns ────────────────────────────────────────────────────
    engulfing = analysis.get("engulfing") or []
    for e in engulfing:
        if not e.get("confirmed"):
            continue
        ago = e.get("candles_ago", 99)
        # Only score if within last 2 confirmed candles; most recent gets higher weight
        if ago > 2:
            continue
        pts = 25 if ago == 1 else 15
        ratio = e.get("body_ratio", 1.0)
        label = f"{'Bearish' if e['direction'] == 'bearish' else 'Bullish'} engulfing confirmed " \
                f"({ago} candle ago, {ratio}× body) — high-TF reversal signal"
        if e["direction"] == "bullish":
            score += pts
            bull_reasons.append(label)
        else:
            score -= pts
            bear_reasons.append(label)

    # ── Elliott Wave ──────────────────────────────────────────────────────────
    wave_bias = elliott.get("bias", "neutral")
    wave_label = elliott.get("wave_count", "")
    if wave_bias == "bullish":
        score += 15
        bull_reasons.append(f"Elliott Wave: {wave_label} (bullish phase)")
    elif wave_bias == "bearish":
        score -= 15
        bear_reasons.append(f"Elliott Wave: {wave_label} (bearish phase)")

    # ── Final direction ───────────────────────────────────────────────────────
    MAX_SCORE = 130.0
    if score >= 30:
        direction = "LONG"
        strength = min(int(score / MAX_SCORE * 100), 100)
    elif score <= -30:
        direction = "SHORT"
        strength = min(int(abs(score) / MAX_SCORE * 100), 100)
    else:
        direction = "NEUTRAL"
        strength = int(abs(score) / 30 * 50)

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry = sl = None
    tp_targets: List[float] = []
    rr_ratio = None

    timeframe = analysis.get("timeframe", "1W")
    # SL/TP multipliers scale with timeframe so levels feel proportional.
    # Shorter TFs use tighter stops; longer TFs allow wider swings.
    TF_MULT = {
        "4H":  (1.0, 1.5, 2.5, 4.0),
        "8H":  (1.0, 1.8, 3.0, 4.5),
        "12H": (1.2, 2.0, 3.5, 5.0),
        "1D":  (1.3, 2.0, 3.5, 5.5),
        "1W":  (1.5, 2.0, 3.5, 5.5),
        "2W":  (1.5, 2.5, 4.0, 6.0),
        "3W":  (1.5, 2.5, 4.0, 6.5),
        "1M":  (1.5, 2.5, 4.5, 7.0),
    }
    sl_m, tp1_m, tp2_m, tp3_m = TF_MULT.get(timeframe, (1.5, 2.0, 3.5, 5.5))

    if candles and len(candles) >= 14 and current_price > 0:
        atr = sum(c["high"] - c["low"] for c in candles[-14:]) / 14
        entry = round(current_price, 8)
        if direction == "LONG":
            sl = round(max(current_price * 0.001, current_price - atr * sl_m), 8)
            tp_targets = [
                round(current_price + atr * tp1_m, 8),
                round(current_price + atr * tp2_m, 8),
                round(current_price + atr * tp3_m, 8),
            ]
        elif direction == "SHORT":
            sl = round(current_price + atr * sl_m, 8)
            tp_targets = [
                round(max(current_price * 0.001, current_price - atr * tp1_m), 8),
                round(max(current_price * 0.001, current_price - atr * tp2_m), 8),
                round(max(current_price * 0.001, current_price - atr * tp3_m), 8),
            ]

        if sl and sl != entry and tp_targets:
            rr_ratio = round(abs(tp_targets[1] - entry) / abs(sl - entry), 2)

    return {
        "direction": direction,
        "score": score,
        "strength": strength,
        "bullish_reasons": bull_reasons,
        "bearish_reasons": bear_reasons,
        "entry": entry,
        "sl": sl,
        "tp_targets": tp_targets,
        "rr_ratio": rr_ratio,
    }
