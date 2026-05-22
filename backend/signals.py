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
    # RSI alone has ~55% accuracy in crypto (barely above random in trending
    # markets). Only extreme readings carry real edge; mid-range RSI is noise.
    # Source: Aronson "Evidence-Based Technical Analysis"; crypto quant backtests.
    if rsi is not None:
        if rsi < 25:
            score += 22
            bull_reasons.append(f"RSI extremely oversold ({rsi}) — historically rare, high mean-reversion probability")
        elif rsi < 35:
            score += 12
            bull_reasons.append(f"RSI oversold ({rsi}) — selling pressure elevated, watch for reversal")
        elif rsi < 45:
            score += 4
            bull_reasons.append(f"RSI below midline ({rsi}) — mild bearish lean, low conviction alone")
        elif rsi > 75:
            score -= 22
            bear_reasons.append(f"RSI extremely overbought ({rsi}) — historically rare, high mean-reversion probability")
        elif rsi > 65:
            score -= 12
            bear_reasons.append(f"RSI overbought ({rsi}) — buying pressure elevated, watch for reversal")
        elif rsi > 55:
            score -= 4
            bear_reasons.append(f"RSI above midline ({rsi}) — mild bullish lean, low conviction alone")

    # ── Spot CVD ─────────────────────────────────────────────────────────────
    # One of the highest-quality short-term signals — directly measures real
    # buying vs selling pressure from actual spot transactions, not price-derived.
    # Rated highly by Willy Woo, Glassnode, Laevitas for its leading quality.
    cvd_trend = spot_cvd.get("trend", "neutral")
    if cvd_trend == "bullish":
        score += 18
        bull_reasons.append("Spot CVD rising — real buying pressure confirmed in spot market")
    elif cvd_trend == "bearish":
        score -= 18
        bear_reasons.append("Spot CVD falling — real selling pressure confirmed in spot market")

    # ── Futures CVD ──────────────────────────────────────────────────────────
    # Noisier than spot — perp market dominated by speculators and hedgers.
    # Useful as context/confirmation but lower standalone reliability.
    f_cvd_trend = futures_cvd.get("trend", "neutral")
    if f_cvd_trend == "bullish":
        score += 8
        bull_reasons.append("Futures CVD bullish — speculative demand increasing")
    elif f_cvd_trend == "bearish":
        score -= 8
        bear_reasons.append("Futures CVD bearish — speculative selling increasing")

    # ── CVD Divergence ───────────────────────────────────────────────────────
    # Spot vs futures CVD divergence is one of the most sophisticated setups
    # used by prop desks — distinguishes organic moves from leveraged speculation.
    # Spot-led = real conviction; futures-led = leveraged speculative money.
    cvd_div = analysis.get("cvd_divergence") or {}
    div_type = cvd_div.get("type", "neutral")
    if div_type == "futures_led_up":
        score -= 18
        bear_reasons.append("Futures-driven rally — spot CVD falling, no real demand; leveraged pump likely to fade")
    elif div_type == "spot_led_up":
        score += 22
        bull_reasons.append("Spot-driven rally — genuine organic buying, futures not leading; more sustainable")
    elif div_type == "confirmed_up":
        score += 28
        bull_reasons.append("Fully confirmed rally — both spot and futures CVD rising; strongest bullish confluence")
    elif div_type == "futures_led_down":
        score += 18
        bull_reasons.append("Futures-driven selloff — spot CVD rising, no real selling; high short squeeze risk")
    elif div_type == "spot_led_down":
        score -= 22
        bear_reasons.append("Spot-driven selloff — genuine distribution by real holders; more sustainable decline")
    elif div_type == "confirmed_down":
        score -= 28
        bear_reasons.append("Fully confirmed selloff — both spot and futures CVD falling; strongest bearish confluence")

    # ── Funding Rate ─────────────────────────────────────────────────────────
    # THE highest-reliability crypto-specific signal. Extreme negative funding
    # means shorts are paying longs — the market is max short, creating intense
    # squeeze risk. Documented by BitMEX traders, Arthur Hayes, Cobie, and
    # multiple quant studies on perpetual swap funding as a contrarian indicator.
    # Consistently the strongest mean-reversion signal in crypto markets.
    fr = funding.get("current", 0.0) or 0.0
    if fr < -0.02:
        score += 30
        bull_reasons.append(f"Funding extremely negative ({fr:.4f}%) — market max short, very high squeeze probability")
    elif fr < -0.005:
        score += 15
        bull_reasons.append(f"Funding negative ({fr:.4f}%) — shorts paying longs, structurally favours longs")
    elif fr > 0.04:
        score -= 30
        bear_reasons.append(f"Funding extremely high ({fr:.4f}%) — market max long, very high flush probability")
    elif fr > 0.015:
        score -= 15
        bear_reasons.append(f"Funding elevated ({fr:.4f}%) — longs overextended, late-cycle caution")

    # ── Open Interest ─────────────────────────────────────────────────────────
    # Rising OI + rising price = new longs entering (bullish conviction).
    # Rising OI + falling price = new shorts entering (bearish conviction).
    # Widely used by futures-focused traders; works best as a confirmation filter.
    oi_change = oi.get("change_pct", 0.0) or 0.0
    if len(candles) >= 5:
        prev_price = candles[-5]["close"]
        price_up = current_price > prev_price
        if oi_change > 5:
            if price_up:
                score += 12
                bull_reasons.append(f"OI +{oi_change:.1f}% with rising price — new longs opening, trend conviction")
            else:
                score -= 12
                bear_reasons.append(f"OI +{oi_change:.1f}% with falling price — new shorts entering, bearish conviction")
        elif oi_change < -5:
            if price_up:
                score += 8
                bull_reasons.append(f"OI declining ({oi_change:.1f}%) with rising price — shorts being squeezed out")
            else:
                score -= 8
                bear_reasons.append(f"OI declining ({oi_change:.1f}%) with falling price — longs capitulating")

    # ── Fair Value Gaps ───────────────────────────────────────────────────────
    # ICT concept — price tends to return to fill gaps ~70% of the time.
    # Useful as magnet zones and dynamic support/resistance. Moderate standalone
    # signal strength; works best combined with CVD or funding confirmation.
    unfilled = [f for f in fvgs if not f["filled"]]
    below = [f for f in unfilled if f["type"] == "bullish" and f["midpoint"] < current_price]
    above = [f for f in unfilled if f["type"] == "bearish" and f["midpoint"] > current_price]

    if below:
        score += min(len(below) * 8, 20)
        bull_reasons.append(
            f"{len(below)} bullish FVG(s) acting as support below (nearest: ${below[0]['midpoint']:,.4f})"
        )
    if above:
        score -= min(len(above) * 8, 20)
        bear_reasons.append(
            f"{len(above)} bearish FVG(s) as resistance above (nearest: ${above[0]['midpoint']:,.4f})"
        )

    # ── Flag Patterns — one strongest per direction ───────────────────────────
    # Bulkowski's "Encyclopedia of Chart Patterns" gives confirmed bull flags
    # ~67% success rate — one of the stronger chart pattern signals.
    # Dominant (highest-TF) flag scores more; secondary TF flag scores less.
    scored_dirs: set = set()
    for f in flags:
        if not f.get("is_active"):
            continue
        d = f["direction"]
        if d in scored_dirs:
            continue
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
    # Bulkowski research + HTF studies show confirmed engulfing has 60-65%
    # accuracy on daily+ timeframes, especially with volume confirmation.
    # Most recent candle (ago=1) is significantly more reliable than older.
    engulfing = analysis.get("engulfing") or []
    for e in engulfing:
        if not e.get("confirmed"):
            continue
        ago = e.get("candles_ago", 99)
        if ago > 2:
            continue
        pts = 25 if ago == 1 else 15
        ratio = e.get("body_ratio", 1.0)
        label = f"{'Bearish' if e['direction'] == 'bearish' else 'Bullish'} engulfing confirmed " \
                f"({ago} candle ago, {ratio}x body) — HTF reversal signal"
        if e["direction"] == "bullish":
            score += pts
            bull_reasons.append(label)
        else:
            score -= pts
            bear_reasons.append(label)

    # ── Elliott Wave ──────────────────────────────────────────────────────────
    # Lowest-reliability signal in this system. EW is highly subjective even
    # for expert humans; algorithmic labelling has multiple valid interpretations.
    # Many prop traders don't use it at all. Kept as a weak tiebreaker only.
    wave_bias = elliott.get("bias", "neutral")
    wave_label = elliott.get("wave_count", "")
    if wave_bias == "bullish":
        score += 8
        bull_reasons.append(f"Elliott Wave: {wave_label} (bullish phase) — weak supporting signal")
    elif wave_bias == "bearish":
        score -= 8
        bear_reasons.append(f"Elliott Wave: {wave_label} (bearish phase) — weak supporting signal")

    # ── Final direction ───────────────────────────────────────────────────────
    # Max theoretical bull score:
    #   Funding +30, CVD div confirmed +28, Engulfing +25, Spot CVD +18,
    #   CVD div spot-led already counted above (mutually exclusive with confirmed),
    #   RSI +22, OI +12, Flags +20, FVGs +20, Futures CVD +8, Elliott +8 = ~191
    # In practice signals are partially overlapping so 140 is a realistic ceiling.
    MAX_SCORE = 140.0
    if score >= 30:
        direction = "LONG"
        strength = min(int(score / MAX_SCORE * 100), 100)
    elif score <= -30:
        direction = "SHORT"
        strength = min(int(abs(score) / MAX_SCORE * 100), 100)
    else:
        direction = "NEUTRAL"
        strength = int(abs(score) / 30 * 50)

    # Strength tier: how many indicators are in confluence.
    # Weak   (< 38) → score 30–53  → only 2–3 mild signals; use 25% size
    # Moderate (38–57) → score 53–80 → several aligned; use 50% size
    # Strong (58–76) → score 81–106  → good confluence; use full size
    # Confirmed (77+) → score 107+   → maximum confluence; can scale
    if direction == "NEUTRAL":
        tier = "Neutral"
        size_guide = "No trade"
    elif strength < 38:
        tier = "Weak"
        size_guide = "25% position — low confluence, minimal indicators aligned"
    elif strength < 58:
        tier = "Moderate"
        size_guide = "50% position — several signals aligned, manage risk carefully"
    elif strength < 77:
        tier = "Strong"
        size_guide = "Full position — good multi-indicator confluence"
    else:
        tier = "Confirmed"
        size_guide = "Full position — maximum confluence, can consider scaling"

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry = sl = None
    tp_targets: List[float] = []
    rr_ratio = None

    timeframe = analysis.get("timeframe", "1W")

    TF_SL_MULT = {
        "4H":  1.0, "8H":  1.0, "12H": 1.2,
        "1D":  1.3, "1W":  1.5, "2W":  1.5,
        "3W":  1.5, "1M":  1.5,
    }
    sl_m = TF_SL_MULT.get(timeframe, 1.5)

    TP1_RR, TP2_RR, TP3_RR = 1.5, 2.5, 4.0
    tp1_m = sl_m * TP1_RR
    tp2_m = sl_m * TP2_RR
    tp3_m = sl_m * TP3_RR

    TF_MAX_ATR_PCT = {
        "4H":  0.030,
        "8H":  0.040,
        "12H": 0.050,
        "1D":  0.065,
        "1W":  0.090,
        "2W":  0.100,
        "3W":  0.100,
        "1M":  0.100,
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
        "tier": tier,
        "size_guide": size_guide,
        "bullish_reasons": bull_reasons,
        "bearish_reasons": bear_reasons,
        "entry": entry,
        "sl": sl,
        "tp_targets": tp_targets,
        "rr_ratio": rr_ratio,
    }
