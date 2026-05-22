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

    # ── CVD Divergence ───────────────────────────────────────────────────────
    cvd_div = analysis.get("cvd_divergence") or {}
    div_type = cvd_div.get("type", "neutral")
    if div_type == "futures_led_up":
        score -= 15
        bear_reasons.append("Futures-driven rally — spot CVD falling, no real demand; move may fade")
    elif div_type == "spot_led_up":
        score += 20
        bull_reasons.append("Spot-driven rally — genuine buying, futures not chasing; healthier move")
    elif div_type == "confirmed_up":
        score += 25
        bull_reasons.append("Confirmed rally — both spot and futures CVD bullish")
    elif div_type == "futures_led_down":
        score += 15
        bull_reasons.append("Futures-driven selloff — spot CVD rising, no real selling; short squeeze risk")
    elif div_type == "spot_led_down":
        score -= 20
        bear_reasons.append("Spot-driven selloff — genuine distribution, spot sellers dominate")
    elif div_type == "confirmed_down":
        score -= 25
        bear_reasons.append("Confirmed selloff — both spot and futures CVD bearish")

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

    # ── Flag Patterns — one strongest per direction ───────────────────────────
    # flags are already deduplicated per (direction × timeframe) by pick_dominant_flags;
    # here we further limit to the single strongest bull and single strongest bear
    # so the confluence list stays concise.
    scored_dirs: set = set()
    for f in flags:
        if not f.get("is_active"):
            continue
        d = f["direction"]
        if d in scored_dirs:
            continue          # already scored a flag for this direction
        scored_dirs.add(d)
        bonus = 20 if f.get("dominant") else 10
        if d == "bullish":
            score += bonus
            bull_reasons.append(
                f"{'Dominant b' if f.get('dominant') else 'B'}ullish flag on {f['timeframe']} "
                f"(+{f['pole_pct']}% pole, target ${f['target']:,.4f})"
            )
        else:
            score -= bonus
            bear_reasons.append(
                f"{'Dominant b' if f.get('dominant') else 'B'}earish flag on {f['timeframe']} "
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

    # SL ATR multiplier per timeframe — wider candles need more breathing room.
    TF_SL_MULT = {
        "4H":  1.0, "8H":  1.0, "12H": 1.2,
        "1D":  1.3, "1W":  1.5, "2W":  1.5,
        "3W":  1.5, "1M":  1.5,
    }
    sl_m = TF_SL_MULT.get(timeframe, 1.5)

    # TPs are derived from SL distance as R:R multiples — consistent across all TFs.
    # Gaps increase deliberately: TP1→TP2 = +1R, TP2→TP3 = +1.5R.
    #   TP1 = 1.5R  easy first target; enables moving SL to breakeven
    #   TP2 = 2.5R  core profit target; main R:R justification
    #   TP3 = 4.0R  runner; only hit on strong trend continuation
    TP1_RR, TP2_RR, TP3_RR = 1.5, 2.5, 4.0
    tp1_m = sl_m * TP1_RR
    tp2_m = sl_m * TP2_RR
    tp3_m = sl_m * TP3_RR

    # Dynamic ATR cap: clamp effective ATR to at most X% of price so
    # high-volatility assets (HYPE, TAO) get proportionally tighter distances
    # without any per-symbol rules. All levels scale from the same eff_atr.
    # Implied max SL  = max_atr_pct × sl_m
    # Implied max TP3 = max_atr_pct × tp3_m  (always ≤ 60%)
    TF_MAX_ATR_PCT = {
        "4H":  0.030,   # max SL  3%,  max TP3 18%
        "8H":  0.040,   # max SL  4%,  max TP3 24%
        "12H": 0.050,   # max SL  6%,  max TP3 30%
        "1D":  0.065,   # max SL  8.5%, max TP3 39%
        "1W":  0.090,   # max SL 13.5%, max TP3 54%
        "2W":  0.100,   # max SL 15%,  max TP3 60%
        "3W":  0.100,   # max SL 15%,  max TP3 60%
        "1M":  0.100,   # max SL 15%,  max TP3 60%
    }
    max_atr_abs = current_price * TF_MAX_ATR_PCT.get(timeframe, 0.09)

    if candles and len(candles) >= 14 and current_price > 0:
        atr = sum(c["high"] - c["low"] for c in candles[-14:]) / 14
        entry = round(current_price, 8)

        eff_atr  = min(atr, max_atr_abs)
        sl_dist  = eff_atr * sl_m
        tp1_dist = eff_atr * tp1_m
        tp2_dist = eff_atr * tp2_m
        tp3_dist = eff_atr * tp3_m

        if direction == "LONG":
            sl = round(max(current_price * 0.001, current_price - sl_dist), 8)
            tp_targets = [
                round(current_price + tp1_dist, 8),
                round(current_price + tp2_dist, 8),
                round(current_price + tp3_dist, 8),
            ]
        elif direction == "SHORT":
            sl = round(current_price + sl_dist, 8)
            tp_targets = [
                round(max(current_price * 0.001, current_price - tp1_dist), 8),
                round(max(current_price * 0.001, current_price - tp2_dist), 8),
                round(max(current_price * 0.001, current_price - tp3_dist), 8),
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
