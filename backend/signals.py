from typing import Dict, List


# ── Market-cap volatility tier ────────────────────────────────────────────────
# Smaller caps move more per candle — BTC rarely does 5% in 1H but HYPE can.
# We scale the ATR cap (not the SL multiplier) so stops are sized to each
# asset's actual volatility range rather than one-size-fits-all.
#
# Tier thresholds (USD market cap) and their ATR cap multipliers:
#   Mega  (>$100 B) — BTC, ETH          → 1.0×  (base)
#   Large ($10B-100B) — SOL, BNB, XRP   → 1.5×
#   Mid   ($1B-10B)  — LINK, ALGO, AAVE → 2.0×
#   Small ($200M-1B) — KAS, SUI, HYPE   → 3.0×
#   Micro (<$200M)   — tiny alts        → 4.0×
#
# Typical 1H ATR %: Mega 0.5-1.5 | Large 1-3 | Mid 2-5 | Small 4-10 | Micro 8-20

_MCAP_TIERS = [
    (100_000_000_000, "mega",  "Mega Cap (>$100 B)",     1.0),
    ( 10_000_000_000, "large", "Large Cap ($10B-$100B)", 1.5),
    (  1_000_000_000, "mid",   "Mid Cap ($1B-$10B)",     2.0),
    (    200_000_000, "small", "Small Cap ($200M-$1B)",  3.0),
    (              0, "micro", "Micro Cap (<$200M)",     4.0),
]

def _mcap_tier(market_cap):
    """Return (tier_id, tier_label, atr_mult) for the given market cap (USD)."""
    if market_cap is None:
        return "mid", "Unknown Cap", 2.0   # safe default
    for threshold, tid, label, mult in _MCAP_TIERS:
        if market_cap >= threshold:
            return tid, label, mult
    return "micro", "Micro Cap (<$200M)", 4.0


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
    timeframe   = analysis.get("timeframe", "1H")

    current_price = candles[-1]["close"] if candles else 0.0

    # ── Timeframe weight for macro/sentiment indicators ───────────────────────
    # Fear & Greed and News update at most once per day. Applying their full
    # weight on a 1H chart is misleading — they carry no 1H-specific edge.
    # Scale linearly from 30% on 1H up to 100% on 1D+.
    _TF_MACRO_W = {
        "1H": 0.30, "2H": 0.40, "4H": 0.50, "8H": 0.65, "12H": 0.80,
        "1D": 1.00, "1W": 1.00, "2W": 1.00, "3W": 1.00,  "1M": 1.00,
    }
    tf_macro_w = _TF_MACRO_W.get(timeframe, 1.0)

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

    # ── MACD ─────────────────────────────────────────────────────────────────
    # Momentum crossover — documented by Van Tharp and Larry Connors backtests.
    # Fresh cross > histogram direction alone. Zero-cross (histogram flipping
    # sign) is the strongest MACD signal.
    macd = analysis.get("macd") or {}
    m_cross     = macd.get("cross")
    m_zero      = macd.get("zero_cross")
    m_hist      = macd.get("histogram")
    m_trend     = macd.get("trend", "neutral")
    if m_cross == "bullish" or m_zero == "bullish":
        score += 20
        bull_reasons.append("MACD bullish cross — momentum flipping bullish, strong early signal")
    elif m_trend == "bullish" and m_hist is not None and m_hist > 0:
        score += 10
        bull_reasons.append(f"MACD histogram positive ({m_hist:+.4f}) — bullish momentum sustained")
    if m_cross == "bearish" or m_zero == "bearish":
        score -= 20
        bear_reasons.append("MACD bearish cross — momentum flipping bearish, strong early signal")
    elif m_trend == "bearish" and m_hist is not None and m_hist < 0:
        score -= 10
        bear_reasons.append(f"MACD histogram negative ({m_hist:+.4f}) — bearish momentum sustained")

    # ── Trend indicators — EMA + SuperTrend + Ichimoku (capped bucket) ────────
    # These three all measure the same thing: "is the market in an uptrend?"
    # Letting them each score independently can add 50+ pts from one idea.
    # Cap the combined trend contribution at ±35 so they confirm each other
    # without triple-counting. Individual reasons still shown in confluence list.
    TREND_CAP = 35
    t_bull = 0; t_bear = 0
    t_bull_r: List[str] = []; t_bear_r: List[str] = []

    # EMA
    ema = analysis.get("ema_trend") or {}
    ema_above = ema.get("above", [])
    ema_below = ema.get("below", [])
    if 50 in ema_above and 200 in ema_above:
        t_bull += 18; t_bull_r.append("Price above EMA50 & EMA200 — sustained uptrend structure confirmed")
    elif 50 in ema_above and 200 in ema_below:
        t_bull += 8;  t_bull_r.append("Price above EMA50 but below EMA200 — medium-term bullish, long-term still bearish")
    elif 50 in ema_above:
        t_bull += 5;  t_bull_r.append("Price above EMA50 — medium-term bullish momentum")
    if 50 in ema_below and 200 in ema_below:
        t_bear += 18; t_bear_r.append("Price below EMA50 & EMA200 — sustained downtrend structure confirmed")
    elif 50 in ema_below and 200 in ema_above:
        t_bear += 8;  t_bear_r.append("Price below EMA50 but above EMA200 — medium-term bearish, long-term still bullish")
    elif 50 in ema_below:
        t_bear += 5;  t_bear_r.append("Price below EMA50 — medium-term bearish pressure")

    # ── Long / Short Ratio ────────────────────────────────────────────────────
    # Contrarian indicator — crowd positioning from a single exchange (OKX).
    # Downweighted vs funding rate: funding measures actual money paid,
    # L/S ratio only measures account count on one exchange — less reliable.
    ls = analysis.get("long_short") or {}
    ls_ratio   = ls.get("ratio")
    ls_long    = ls.get("long_pct", 50)
    ls_short   = ls.get("short_pct", 50)
    if ls_ratio is not None:
        if ls_ratio < 0.65:
            score += 14
            bull_reasons.append(f"L/S ratio {ls_ratio} ({ls_short:.1f}% short) — crowd heavily short, contrarian long signal")
        elif ls_ratio < 0.85:
            score += 8
            bull_reasons.append(f"L/S ratio {ls_ratio} ({ls_short:.1f}% short) — moderate short bias, favours longs")
        elif ls_ratio > 2.5:
            score -= 14
            bear_reasons.append(f"L/S ratio {ls_ratio} ({ls_long:.1f}% long) — crowd extremely long, contrarian short signal")
        elif ls_ratio > 1.5:
            score -= 8
            bear_reasons.append(f"L/S ratio {ls_ratio} ({ls_long:.1f}% long) — crowd long-heavy, late-cycle caution")

    # ── Fear & Greed Index ────────────────────────────────────────────────────
    # Composite sentiment — same contrarian principle as funding rate but macro.
    # Extreme Fear historically marks the best buying opportunities across cycles.
    # Alternative.me index; rivals funding rate for macro contrarian reliability.
    fg = analysis.get("fear_greed") or {}
    fg_val = fg.get("value")
    fg_lbl = fg.get("label", "")
    if fg_val is not None:
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        if fg_val <= 15:
            pts = round(25 * tf_macro_w)
            score += pts
            bull_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — extreme fear, best buying zones{tf_note}")
        elif fg_val <= 30:
            pts = round(12 * tf_macro_w)
            score += pts
            bull_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — market fearful, contrarian bullish lean{tf_note}")
        elif fg_val >= 80:
            pts = round(25 * tf_macro_w)
            score -= pts
            bear_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — extreme greed, historically marks tops{tf_note}")
        elif fg_val >= 65:
            pts = round(12 * tf_macro_w)
            score -= pts
            bear_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — market greedy, contrarian bearish lean{tf_note}")

    # ── News Sentiment ────────────────────────────────────────────────────────
    # CryptoPanic community votes + keyword analysis (CoinDesk / CoinTelegraph RSS).
    # Major events (ETF approval, exchange hack, govt ban) move markets 10-30%;
    # routine news is noise. Capped at ±20 — confirmation role, not a trigger.
    news        = analysis.get("news") or {}
    news_signal = news.get("signal", "neutral")
    news_bull   = news.get("bullish", 0)
    news_bear   = news.get("bearish", 0)
    if news_signal == "bullish":
        raw = min(15, max(6, news_bull * 4))   # cap lowered 20→15, base 8→6
        pts = round(raw * tf_macro_w)
        score += pts
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        bull_reasons.append(
            f"News sentiment bullish — {news_bull} bullish vs {news_bear} bearish "
            f"articles in last 48h{tf_note}"
        )
    elif news_signal == "bearish":
        raw = min(15, max(6, news_bear * 4))
        pts = round(raw * tf_macro_w)
        score -= pts
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        bear_reasons.append(
            f"News sentiment bearish — {news_bear} bearish vs {news_bull} bullish "
            f"articles in last 48h{tf_note}"
        )

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

    # ── RSI Divergence ────────────────────────────────────────────────────────
    # One of the most reliable reversal signals — price and momentum disagree.
    # Bullish divergence (price lower low, RSI higher low) precedes many of the
    # biggest altcoin pumps including XLM-style consolidation breakouts.
    # Detected over a 14-candle window to avoid noise on very short timeframes.
    rsi_div = analysis.get("rsi_divergence") or {}
    div_type = rsi_div.get("type")
    div_desc = rsi_div.get("description", "")
    div_str  = rsi_div.get("strength", 0) or 0
    if div_type == "bullish":
        pts = 18 if div_str >= 5 else 12   # stronger divergence = more points
        score += pts
        bull_reasons.append(div_desc or "Bullish RSI divergence — price lower low, RSI higher low")
    elif div_type == "bearish":
        pts = 18 if div_str >= 5 else 12
        score -= pts
        bear_reasons.append(div_desc or "Bearish RSI divergence — price higher high, RSI lower high")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    # Squeeze = coiled spring. Breakout after squeeze = high-probability burst.
    # Weights conservative until we have live performance data — can raise later.
    bb = analysis.get("bollinger") or {}
    bb_squeeze   = bb.get("squeeze", False)
    bb_breakout  = bb.get("breakout")
    bb_pct_b     = bb.get("pct_b", 0.5)
    bb_upper     = bb.get("upper")
    bb_lower     = bb.get("lower")

    fmt_p = lambda v: f"${v:,.4f}" if v else ""
    if bb_squeeze and bb_breakout == "bullish":
        score += 16
        bull_reasons.append(f"Bollinger squeeze breakout BULLISH — price closed above upper band {fmt_p(bb_upper)} after compression; explosive move signal")
    elif bb_squeeze and bb_breakout == "bearish":
        score -= 16
        bear_reasons.append(f"Bollinger squeeze breakdown BEARISH — price closed below lower band {fmt_p(bb_lower)} after compression; explosive move signal")
    elif bb_squeeze:
        if bb_pct_b > 0.6:
            score += 5
            bull_reasons.append(f"Bollinger squeeze active — bands compressed, price upper half (%B {bb_pct_b:.2f}); breakout likely imminent")
        elif bb_pct_b < 0.4:
            score -= 5
            bear_reasons.append(f"Bollinger squeeze active — bands compressed, price lower half (%B {bb_pct_b:.2f}); breakdown risk elevated")
    elif bb_breakout == "bullish":
        score += 10
        bull_reasons.append(f"Price above Bollinger upper band {fmt_p(bb_upper)} — strong bullish momentum")
    elif bb_breakout == "bearish":
        score -= 10
        bear_reasons.append(f"Price below Bollinger lower band {fmt_p(bb_lower)} — strong bearish momentum")

    # SuperTrend — flip scores outside the trend cap (it's a momentum event,
    # not just a trend state), sustained direction goes into the cap bucket
    st = analysis.get("supertrend") or {}
    st_dir     = st.get("direction")
    st_flipped = st.get("flipped", False)
    st_val     = st.get("value")
    if st_dir == "bullish":
        if st_flipped:
            score += 20   # fresh flip = momentum event → outside trend cap
            bull_reasons.append(f"SuperTrend flipped BULLISH — fresh BUY signal, trend just reversed up (support ${st_val:,.4f})" if st_val else "SuperTrend flipped BULLISH — fresh BUY signal")
        else:
            t_bull += 12; t_bull_r.append(f"SuperTrend bullish — price above dynamic support (${st_val:,.4f}), uptrend intact" if st_val else "SuperTrend bullish — uptrend intact")
    elif st_dir == "bearish":
        if st_flipped:
            score -= 20   # fresh flip = momentum event → outside trend cap
            bear_reasons.append(f"SuperTrend flipped BEARISH — fresh SELL signal, trend just reversed down (resistance ${st_val:,.4f})" if st_val else "SuperTrend flipped BEARISH — fresh SELL signal")
        else:
            t_bear += 12; t_bear_r.append(f"SuperTrend bearish — price below dynamic resistance (${st_val:,.4f}), downtrend intact" if st_val else "SuperTrend bearish — downtrend intact")

    # Ichimoku — all three layers into the trend bucket
    ichi = analysis.get("ichimoku") or {}
    cloud_color    = ichi.get("cloud_color")
    price_vs_cloud = ichi.get("price_vs_cloud")
    tk_cross       = ichi.get("tk_cross")
    tenkan         = ichi.get("tenkan")
    kijun          = ichi.get("kijun")
    if cloud_color == "green":
        t_bull += 8;  t_bull_r.append("Ichimoku cloud green (Span A > Span B) — bullish trend territory")
    elif cloud_color == "red":
        t_bear += 8;  t_bear_r.append("Ichimoku cloud red (Span A < Span B) — bearish trend territory")
    if price_vs_cloud == "above":
        t_bull += 15; t_bull_r.append("Price above Ichimoku cloud — cloud acting as support, bullish structure")
    elif price_vs_cloud == "below":
        t_bear += 15; t_bear_r.append("Price below Ichimoku cloud — cloud acting as resistance, bearish structure")
    if tk_cross == "bullish":
        tk_desc = f"Tenkan (${tenkan:,.4f}) crossed above Kijun (${kijun:,.4f})" if (tenkan and kijun) else "Tenkan crossed above Kijun"
        t_bull += 12; t_bull_r.append(f"Ichimoku TK bullish cross — {tk_desc}, short-term momentum turning up")
    elif tk_cross == "bearish":
        tk_desc = f"Tenkan (${tenkan:,.4f}) crossed below Kijun (${kijun:,.4f})" if (tenkan and kijun) else "Tenkan crossed below Kijun"
        t_bear += 12; t_bear_r.append(f"Ichimoku TK bearish cross — {tk_desc}, short-term momentum turning down")

    # Apply trend cap and flush reasons into main lists
    eff_t_bull = min(t_bull, TREND_CAP)
    eff_t_bear = min(t_bear, TREND_CAP)
    score += eff_t_bull
    score -= eff_t_bear
    bull_reasons += t_bull_r
    bear_reasons += t_bear_r
    if t_bull > TREND_CAP:
        bull_reasons.append(f"⚡ Trend cap applied — raw trend score {t_bull} capped at {TREND_CAP} (EMA/SuperTrend/Ichimoku all agree, preventing triple-counting)")
    if t_bear > TREND_CAP:
        bear_reasons.append(f"⚡ Trend cap applied — raw trend score {t_bear} capped at {TREND_CAP} (EMA/SuperTrend/Ichimoku all agree, preventing triple-counting)")

    # ── VWAP ─────────────────────────────────────────────────────────────────
    # Most widely used institutional intraday indicator. Price above rising VWAP
    # = institutions accumulating. Fresh cross = high-quality entry signal.
    vwap_data      = analysis.get("vwap") or {}
    vwap_pos       = vwap_data.get("price_vs_vwap")
    vwap_slope     = vwap_data.get("slope")
    vwap_cross     = vwap_data.get("vwap_cross")
    vwap_val       = vwap_data.get("vwap")
    fmt_v = lambda v: f"${v:,.4f}" if v else ""
    if vwap_cross == "bullish":
        score += 14
        bull_reasons.append(f"VWAP bullish cross — price just crossed above VWAP {fmt_v(vwap_val)}, institutional momentum shift")
    elif vwap_cross == "bearish":
        score -= 14
        bear_reasons.append(f"VWAP bearish cross — price just crossed below VWAP {fmt_v(vwap_val)}, institutional selling pressure")
    elif vwap_pos == "above":
        pts = 10 if vwap_slope == "rising" else 6
        score += pts
        slope_note = " + VWAP rising" if vwap_slope == "rising" else ""
        bull_reasons.append(f"Price above VWAP{slope_note} {fmt_v(vwap_val)} — institutional buy-side structure intact")
    elif vwap_pos == "below":
        pts = 10 if vwap_slope == "falling" else 6
        score -= pts
        slope_note = " + VWAP falling" if vwap_slope == "falling" else ""
        bear_reasons.append(f"Price below VWAP{slope_note} {fmt_v(vwap_val)} — institutional sell-side pressure dominant")

    # ── Stochastic RSI ────────────────────────────────────────────────────────
    # More sensitive than plain RSI — oscillates faster and gives earlier signals.
    # Cross from oversold/overbought zone is highest quality; zone alone is weaker.
    srsi           = analysis.get("stoch_rsi") or {}
    srsi_signal    = srsi.get("signal")
    srsi_k         = srsi.get("k")
    srsi_d         = srsi.get("d")
    if srsi_signal == "bull_cross_oversold":
        score += 20
        bull_reasons.append(f"Stoch RSI bullish cross from oversold (K:{srsi_k} D:{srsi_d}) — high-quality reversal signal")
    elif srsi_signal == "oversold":
        score += 12
        bull_reasons.append(f"Stoch RSI oversold (K:{srsi_k} D:{srsi_d}) — momentum deeply oversold, bounce likely")
    elif srsi_signal == "near_oversold":
        score += 6
        bull_reasons.append(f"Stoch RSI near oversold (K:{srsi_k}) — mild oversold lean")
    elif srsi_signal == "bear_cross_overbought":
        score -= 20
        bear_reasons.append(f"Stoch RSI bearish cross from overbought (K:{srsi_k} D:{srsi_d}) — high-quality reversal signal")
    elif srsi_signal == "overbought":
        score -= 12
        bear_reasons.append(f"Stoch RSI overbought (K:{srsi_k} D:{srsi_d}) — momentum deeply overbought, pullback likely")
    elif srsi_signal == "near_overbought":
        score -= 6
        bear_reasons.append(f"Stoch RSI near overbought (K:{srsi_k}) — mild overbought lean")

    # ── Volume Confirmation ───────────────────────────────────────────────────
    # Elevated volume on a directional candle validates the move — price action
    # without volume is weak; with volume it's conviction. Keeps whale activity
    # (2.5×) separate — this covers the 1.3-2.4× range (elevated but not whale).
    vol            = analysis.get("vol_signal") or {}
    vol_sig        = vol.get("signal")
    vol_ratio      = vol.get("ratio", 0) or 0
    vol_desc       = vol.get("description", "")
    if vol_sig == "bullish":
        pts = 12 if vol_ratio >= 2.0 else 8
        score += pts
        bull_reasons.append(vol_desc or f"Volume confirmation bullish ({vol_ratio:.1f}× avg)")
    elif vol_sig == "bearish":
        pts = 12 if vol_ratio >= 2.0 else 8
        score -= pts
        bear_reasons.append(vol_desc or f"Volume confirmation bearish ({vol_ratio:.1f}× avg)")

    # ── Final direction ───────────────────────────────────────────────────────
    #   VWAP cross +14, Stoch RSI bull cross +20, Volume +12 → total ~320
    # In practice signals overlap — realistic ceiling ~200.
    MAX_SCORE = 330.0

    strength = min(int(abs(score) / MAX_SCORE * 100), 100)

    # Directional threshold raised from 30 → 42.
    # Old threshold (30) was only 10.7% of max — a single CVD signal + mild RSI
    # was enough to trigger LONG. Raising to 42 requires at least 2-3 real
    # signals in agreement before calling a direction.
    DIRECTION_THRESHOLD = 42
    if score >= DIRECTION_THRESHOLD:
        direction = "LONG"
    elif score <= -DIRECTION_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # Strength tiers (strength = score / MAX_SCORE * 100):
    # NEUTRAL threshold = 42/280 = 15% strength
    # Weak     (15–32): score 42–90   — few signals, cautious 25% size
    # Moderate (33–50): score 92–140  — several aligned, 50% size
    # Strong   (51–68): score 143–190 — good confluence, full size
    # Confirmed  (69+): score 193+    — maximum confluence, can scale
    if direction == "NEUTRAL":
        tier = "Neutral"
        size_guide = "No trade"
    elif strength < 33:
        tier = "Weak"
        size_guide = "25% position — low confluence, minimal indicators aligned"
    elif strength < 51:
        tier = "Moderate"
        size_guide = "50% position — several signals aligned, manage risk carefully"
    elif strength < 69:
        tier = "Strong"
        size_guide = "Full position — good multi-indicator confluence"
    else:
        tier = "Confirmed"
        size_guide = "Full position — maximum confluence, can consider scaling"

    # ── Market-cap volatility tier — dynamic ATR cap ──────────────────────────
    market_cap = analysis.get("market_cap")
    vol_tier_id, vol_tier_label, atr_mult = _mcap_tier(market_cap)

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry = sl = None
    tp_targets: List[float] = []
    rr_ratio = None
    sl_pct = tp1_pct = tp2_pct = tp3_pct = None

    # SL distance multiplier — same across market caps; wider ATR cap does the work
    TF_SL_MULT = {
        "1H":  0.8, "2H":  0.9,
        "4H":  1.0, "8H":  1.0, "12H": 1.2,
        "1D":  1.3, "1W":  1.5, "2W":  1.5,
        "3W":  1.5, "1M":  1.5,
    }
    sl_m = TF_SL_MULT.get(timeframe, 1.5)

    TP1_RR, TP2_RR, TP3_RR = 1.5, 2.5, 4.0
    tp1_m = sl_m * TP1_RR
    tp2_m = sl_m * TP2_RR
    tp3_m = sl_m * TP3_RR

    # Base ATR cap per timeframe — calibrated for mega-cap (BTC/ETH level).
    # Scaled up by atr_mult so smaller caps get room matching their true volatility:
    #   1H BTC cap: 1.5%  |  1H HYPE (small) cap: 1.5% × 3.0 = 4.5%
    #   1W BTC cap: 9.0%  |  1W HYPE cap: 9.0% × 3.0 = 27%
    TF_BASE_ATR_PCT = {
        "1H":  0.015, "2H":  0.022,
        "4H":  0.030, "8H":  0.040, "12H": 0.050,
        "1D":  0.065, "1W":  0.090,
        "2W":  0.100, "3W":  0.100, "1M":  0.100,
    }
    base_pct     = TF_BASE_ATR_PCT.get(timeframe, 0.09)
    max_atr_pct  = base_pct * atr_mult          # e.g. 0.015 × 3.0 = 0.045 (4.5%)
    max_atr_abs  = current_price * max_atr_pct

    if candles and len(candles) >= 14 and current_price > 0:
        atr = sum(c["high"] - c["low"] for c in candles[-14:]) / 14
        entry = round(current_price, 8)

        eff_atr  = min(atr, max_atr_abs)
        sl_dist  = eff_atr * sl_m
        tp1_dist = eff_atr * tp1_m
        tp2_dist = eff_atr * tp2_m
        tp3_dist = eff_atr * tp3_m

        def _tp_short(dist):
            """Return TP price for a SHORT, or None if it would require >95% drop."""
            target = current_price - dist
            if target <= current_price * 0.05:   # >95% drop — not achievable
                return None
            return round(target, 8)

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
                _tp_short(tp1_dist),
                _tp_short(tp2_dist),
                _tp_short(tp3_dist),
            ]

        if sl and sl != entry and tp_targets and tp_targets[0] is not None:
            rr_ratio = round(abs((tp_targets[1] or tp_targets[0]) - entry) / abs(sl - entry), 2)
            # Percentage distances from entry (always positive)
            sl_pct  = round(abs(sl - entry) / entry * 100, 2)
            tp1_pct = round(abs(tp_targets[0] - entry) / entry * 100, 2) if tp_targets[0] else None
            tp2_pct = round(abs(tp_targets[1] - entry) / entry * 100, 2) if tp_targets[1] else None
            tp3_pct = round(abs(tp_targets[2] - entry) / entry * 100, 2) if tp_targets[2] else None

    return {
        "direction": direction,
        "score": score,
        "strength": strength,
        "tier": tier,
        "size_guide": size_guide,
        "vol_tier": vol_tier_id,
        "vol_tier_label": vol_tier_label,
        "bullish_reasons": bull_reasons,
        "bearish_reasons": bear_reasons,
        "entry": entry,
        "sl": sl,
        "sl_pct": sl_pct,
        "tp_targets": tp_targets,
        "tp_pcts": [tp1_pct, tp2_pct, tp3_pct],
        "rr_ratio": rr_ratio,
    }
