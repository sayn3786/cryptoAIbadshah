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
    # Group contribution tracker — signed (positive = bull, negative = bear)
    g = {'trend': 0, 'momentum': 0, 'flow': 0, 'sentiment': 0, 'pattern': 0}
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

    # ── RSI level (contrarian — extreme readings only) ───────────────────────
    # Mid-range RSI (45–65) is genuinely ambiguous: the same reading occurs both
    # in healthy trends and in weak rallies. Only extreme levels carry reliable
    # mean-reversion edge; the 55–65 band is removed (was −4, net noise).
    if rsi is not None:
        if rsi < 25:
            score += 22; g['momentum'] += 22
            bull_reasons.append(f"RSI extremely oversold ({rsi}) — historically rare, high mean-reversion probability")
        elif rsi < 35:
            score += 12; g['momentum'] += 12
            bull_reasons.append(f"RSI oversold ({rsi}) — selling pressure elevated, watch for reversal")
        elif rsi < 45:
            score += 4; g['momentum'] += 4
            bull_reasons.append(f"RSI below midline ({rsi}) — mild oversold lean, low conviction alone")
        elif rsi > 75:
            score -= 22; g['momentum'] -= 22
            bear_reasons.append(f"RSI extremely overbought ({rsi}) — historically rare, high mean-reversion probability")
        elif rsi > 65:
            score -= 12; g['momentum'] -= 12
            bear_reasons.append(f"RSI overbought ({rsi}) — buying pressure elevated, watch for reversal")
        # 45–65: genuinely neutral — no score (same reading in uptrends and dead-cat bounces)

    # ── RSI slope (momentum direction — catches building/fading pressure) ────
    # RSI level is contrarian; RSI slope is momentum. They answer different
    # questions. A coin with RSI=55 and slope=+14 is building bullish pressure.
    # The same coin with RSI=55 and slope=−14 is momentum fading from overbought.
    # Source: Elder "Trading for a Living" — RSI slope > RSI level for trend detection.
    rsi_slope = analysis.get("rsi_slope")
    if rsi_slope is not None:
        if rsi_slope > 18:
            score += 16; g['momentum'] += 16
            bull_reasons.append(f"RSI momentum surging (+{rsi_slope:.1f} over 5 candles) — strong buying pressure building rapidly")
        elif rsi_slope > 9:
            score += 9; g['momentum'] += 9
            bull_reasons.append(f"RSI rising (+{rsi_slope:.1f} over 5 candles) — momentum building, buyers gaining control")
        elif rsi_slope > 4:
            score += 4; g['momentum'] += 4
            bull_reasons.append(f"RSI drifting higher (+{rsi_slope:.1f} over 5 candles) — mild upward pressure")
        elif rsi_slope < -18:
            score -= 16; g['momentum'] -= 16
            bear_reasons.append(f"RSI momentum collapsing ({rsi_slope:.1f} over 5 candles) — strong selling pressure building rapidly")
        elif rsi_slope < -9:
            score -= 9; g['momentum'] -= 9
            bear_reasons.append(f"RSI falling ({rsi_slope:.1f} over 5 candles) — momentum fading, sellers gaining control")
        elif rsi_slope < -4:
            score -= 4; g['momentum'] -= 4
            bear_reasons.append(f"RSI drifting lower ({rsi_slope:.1f} over 5 candles) — mild downward pressure")

    # ── Price Rate-of-Change (ROC) ────────────────────────────────────────────
    # The most direct momentum signal: "this coin is actively moving right now."
    # A coin that's up 16% in 4 candles scores zero from RSI/trend indicators
    # if it was previously in a downtrend. ROC fills that gap by reading the
    # CURRENT price action without depending on historical context.
    # Source: standard price momentum factor (Jegadeesh & Titman 1993 momentum anomaly).
    price_roc = analysis.get("price_roc")
    if price_roc is not None:
        if price_roc > 12:
            score += 20; g['momentum'] += 20
            bull_reasons.append(f"Strong price momentum ({price_roc:+.1f}% over 4 candles) — active buying surge; coin is moving right now")
        elif price_roc > 6:
            score += 12; g['momentum'] += 12
            bull_reasons.append(f"Price momentum building ({price_roc:+.1f}% over 4 candles) — sustained upward move in progress")
        elif price_roc > 2.5:
            score += 5; g['momentum'] += 5
            bull_reasons.append(f"Mild positive price momentum ({price_roc:+.1f}% over 4 candles)")
        elif price_roc < -12:
            score -= 20; g['momentum'] -= 20
            bear_reasons.append(f"Strong price selloff ({price_roc:+.1f}% over 4 candles) — active selling surge; coin is dropping right now")
        elif price_roc < -6:
            score -= 12; g['momentum'] -= 12
            bear_reasons.append(f"Price momentum falling ({price_roc:+.1f}% over 4 candles) — sustained downward move in progress")
        elif price_roc < -2.5:
            score -= 5; g['momentum'] -= 5
            bear_reasons.append(f"Mild negative price momentum ({price_roc:+.1f}% over 4 candles)")

    # ── Last-4-candle direction consistency ───────────────────────────────────
    # Raw candle direction (close > open = bullish) over the 4 most recently
    # CLOSED candles (live candle excluded — it hasn't closed yet).
    # Rewards 3-4 candles aligned in one direction (+6/+12), penalises whipsawing.
    # Net contribution: ±12 pts max into momentum bucket.
    candle_dirs = analysis.get("candle_dirs") or []
    if len(candle_dirs) >= 4:
        bull_count = sum(1 for d in candle_dirs[-4:] if d > 0)
        bear_count = 4 - bull_count
        candle_pts = {4: 12, 3: 6, 2: 0, 1: -6, 0: -12}[bull_count]
        score += candle_pts; g['momentum'] += candle_pts
        if candle_pts > 0:
            bull_reasons.append(f"Candle consistency: {bull_count}/4 recent closed candles bullish — sustained buying pressure")
        elif candle_pts < 0:
            bear_reasons.append(f"Candle consistency: {bear_count}/4 recent closed candles bearish — sustained selling pressure")

    # ── CVD: Unified Spot × Futures Analysis ─────────────────────────────────
    # Spot CVD, Futures CVD, and their divergence type are NOT independent —
    # they describe the same market event from different angles.
    #
    # The divergence type encodes both the direction AND the magnitude relationship
    # between the two streams. Scoring all three separately triple-counts the same
    # signal and creates incoherence (e.g. futures_dominated_down = squeeze risk,
    # yet individual bearish CVD scores fight that conclusion).
    #
    # Rule: divergence type is the master signal when present.
    # Individual spot/futures trends are fallback only when no divergence is detected.
    # A magnitude intensifier adds a small dynamic push for extreme ratios.
    cvd_div    = analysis.get("cvd_divergence") or {}
    div_type   = cvd_div.get("type", "neutral")
    spot_ratio = cvd_div.get("spot_ratio",    1) or 1
    fut_ratio  = cvd_div.get("futures_ratio", 1) or 1

    _CVD_BASE = {
        "spot_dominated_up":     +35,   # spot >10×: pure organic buying
        "spot_heavy_up":         +30,   # spot 2–10×: real buyers leading
        "confirmed_up":          +26,   # balanced: both streams confirming
        "spot_led_up":           +20,   # spot bullish, futures neutral/missing
        "futures_led_up":        -16,   # futures pump, spot not confirming
        "futures_dominated_up":  -14,   # futures >50×: leveraged long crowding
        "futures_dominated_down":+10,   # futures >50×: speculative short pile-on
        "futures_led_down":      +16,   # futures selling, spot rising — squeeze
        "futures_heavy_down":    -14,   # futures 10–50×: speculative, lower conviction
        "spot_led_down":         -20,   # spot selling, futures not following
        "confirmed_down":        -26,   # balanced: both streams confirming
        "spot_heavy_down":       -30,   # spot 2–10×: real sellers leading
        "spot_dominated_down":   -35,   # spot >10×: pure holder distribution
    }
    _CVD_REASON = {
        "spot_dominated_up":     ("bull", "Spot-dominated rally — spot CVD {sr:.0f}× futures; overwhelmingly organic buying with minimal leverage, highest-conviction bullish signal"),
        "spot_heavy_up":         ("bull", "Spot-heavy confirmed rally — spot CVD {sr:.1f}× futures; real buyers leading with futures confirming organically"),
        "confirmed_up":          ("bull", "Fully confirmed rally — spot and futures CVD rising in sync; balanced organic + speculative buying, strong confluence"),
        "spot_led_up":           ("bull", "Spot-driven rally — spot CVD rising, futures not chasing; genuine demand without leverage build-up, more sustainable"),
        "futures_led_up":        ("bear", "Futures-driven pump — spot CVD falling despite rally; no real spot demand behind the move; leveraged buyers only, likely to fade"),
        "futures_dominated_up":  ("bear", "Futures-dominated rally — futures CVD {fr:.0f}× spot; speculative leverage crowding with no organic support; elevated long-squeeze risk"),
        "futures_dominated_down":("bull", "Futures-dominated selloff — futures CVD {fr:.0f}× spot; speculative short pile-on, real holders not selling; high short-squeeze risk"),
        "futures_led_down":      ("bull", "Futures-driven selloff — spot CVD rising while futures sell; no real distribution; short-squeeze risk elevated"),
        "futures_heavy_down":    ("bear", "Futures-heavy selloff — futures CVD {fr:.0f}× spot; bearish but mostly speculative, conviction lower than genuine distribution"),
        "spot_led_down":         ("bear", "Spot-driven selloff — spot CVD falling, futures not following; real holders distributing quietly without leverage"),
        "confirmed_down":        ("bear", "Fully confirmed selloff — spot and futures CVD falling in sync; real selling meets speculative pressure, strong bearish confluence"),
        "spot_heavy_down":       ("bear", "Spot-heavy confirmed selloff — spot CVD {sr:.1f}× futures; real sellers leading with futures confirming"),
        "spot_dominated_down":   ("bear", "Spot-dominated selloff — spot CVD {sr:.0f}× futures; pure holder distribution with minimal leverage, highest-conviction bearish signal"),
    }

    if div_type in _CVD_BASE:
        pts = _CVD_BASE[div_type]
        # Magnitude intensifier: extreme ratios push slightly beyond the base (cap ±5)
        # Makes scoring dynamic — a 200× ratio is meaningfully different from 55×
        if "spot_dominated" in div_type:
            extra = min(5, round((spot_ratio - 10) * 0.1))
            pts = pts + extra if pts > 0 else pts - extra
        elif "futures_dominated" in div_type:
            extra = min(5, round((fut_ratio - 50) * 0.02))
            pts = pts + extra if pts > 0 else pts - extra
        score += pts; g['flow'] += pts
        side, tmpl = _CVD_REASON[div_type]
        reason = tmpl.format(sr=spot_ratio, fr=fut_ratio)
        if side == "bull":
            bull_reasons.append(reason)
        else:
            bear_reasons.append(reason)
    else:
        # No divergence detected — score individual CVD trends as independent signals
        # (lower weight than unified signal since they carry no relational context)
        cvd_trend = spot_cvd.get("trend", "neutral")
        if cvd_trend == "bullish":
            score += 14; g['flow'] += 14
            bull_reasons.append("Spot CVD rising — real buying pressure confirmed; no futures divergence to contextualise")
        elif cvd_trend == "bearish":
            score -= 14; g['flow'] -= 14
            bear_reasons.append("Spot CVD falling — real selling pressure confirmed; no futures divergence to contextualise")
        f_cvd_trend = futures_cvd.get("trend", "neutral")
        if f_cvd_trend == "bullish":
            score += 7; g['flow'] += 7
            bull_reasons.append("Futures CVD bullish — speculative demand rising; no divergence with spot")
        elif f_cvd_trend == "bearish":
            score -= 7; g['flow'] -= 7
            bear_reasons.append("Futures CVD bearish — speculative selling rising; no divergence with spot")

    # ── Funding Rate ─────────────────────────────────────────────────────────
    # THE highest-reliability crypto-specific signal. Extreme negative funding
    # means shorts are paying longs — the market is max short, creating intense
    # squeeze risk. Documented by BitMEX traders, Arthur Hayes, Cobie, and
    # multiple quant studies on perpetual swap funding as a contrarian indicator.
    # Consistently the strongest mean-reversion signal in crypto markets.
    fr = funding.get("current", 0.0) or 0.0
    if fr < -0.02:
        score += 30; g['flow'] += 30
        bull_reasons.append(f"Funding extremely negative ({fr:.4f}%) — market max short, very high squeeze probability")
    elif fr < -0.005:
        score += 15; g['flow'] += 15
        bull_reasons.append(f"Funding negative ({fr:.4f}%) — shorts paying longs, structurally favours longs")
    elif fr > 0.04:
        score -= 30; g['flow'] -= 30
        bear_reasons.append(f"Funding extremely high ({fr:.4f}%) — market max long, very high flush probability")
    elif fr > 0.015:
        score -= 15; g['flow'] -= 15
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
                score += 12; g['flow'] += 12
                bull_reasons.append(f"OI +{oi_change:.1f}% with rising price — new longs opening, trend conviction")
            else:
                score -= 12; g['flow'] -= 12
                bear_reasons.append(f"OI +{oi_change:.1f}% with falling price — new shorts entering, bearish conviction")
        elif oi_change < -5:
            if price_up:
                score += 8; g['flow'] += 8
                bull_reasons.append(f"OI declining ({oi_change:.1f}%) with rising price — shorts being squeezed out")
            else:
                score -= 8; g['flow'] -= 8
                bear_reasons.append(f"OI declining ({oi_change:.1f}%) with falling price — longs capitulating")

    # ── Fair Value Gaps ───────────────────────────────────────────────────────
    # ICT concept — price tends to return to fill gaps ~70% of the time.
    # Useful as magnet zones and dynamic support/resistance. Moderate standalone
    # signal strength; works best combined with CVD or funding confirmation.
    unfilled = [f for f in fvgs if not f["filled"]]
    below = [f for f in unfilled if f["type"] == "bullish" and f["midpoint"] < current_price]
    above = [f for f in unfilled if f["type"] == "bearish" and f["midpoint"] > current_price]

    if below:
        _fvg_bull_pts = min(len(below) * 8, 20)
        score += _fvg_bull_pts; g['pattern'] += _fvg_bull_pts
        bull_reasons.append(
            f"{len(below)} bullish FVG(s) acting as support below (nearest: ${below[0]['midpoint']:,.4f})"
        )
    if above:
        _fvg_bear_pts = min(len(above) * 8, 20)
        score -= _fvg_bear_pts; g['pattern'] -= _fvg_bear_pts
        bear_reasons.append(
            f"{len(above)} bearish FVG(s) as resistance above (nearest: ${above[0]['midpoint']:,.4f})"
        )

    # ── Pre-compute trend context for counter-trend discounts ─────────────────
    # t_bull / t_bear are the raw trend bucket values (before capping).
    # Computed here so Flag and MACD sections below can discount counter-trend signals.
    # The authoritative scoring still happens in the full Trend section further down.
    def _trend_raw(a: dict):
        tb, tr = 0, 0
        _ema = a.get("ema_trend") or {}
        _ab  = _ema.get("above", []);  _bl = _ema.get("below", [])
        if 50 in _ab and 200 in _ab: tb += 18
        elif 50 in _ab: tb += max(5, 8)
        if 50 in _bl and 200 in _bl: tr += 18
        elif 50 in _bl: tr += max(5, 8)
        _st = a.get("supertrend") or {}
        if _st.get("direction") == "bullish":   tb += 12
        elif _st.get("direction") == "bearish": tr += 12
        _ic = a.get("ichimoku") or {}
        if _ic.get("cloud_color")    == "green":  tb += 8
        elif _ic.get("cloud_color")  == "red":    tr += 8
        if _ic.get("price_vs_cloud") == "above":  tb += 15
        elif _ic.get("price_vs_cloud") == "below": tr += 15
        return tb, tr
    t_bull, t_bear = _trend_raw(analysis)

    # ── Flag Patterns — one strongest per direction ───────────────────────────
    # Bulkowski's "Encyclopedia of Chart Patterns" gives confirmed bull flags
    # ~67% success rate — one of the stronger chart pattern signals.
    # Dominant (highest-TF) flag scores more; secondary TF flag scores less.
    # Counter-trend discount: a bull flag in a strong bear trend (or vice versa)
    # is likely a relief rally / dead-cat bounce, not a genuine breakout.
    # When raw trend bucket ≥25 pts in one direction, opposing flag is cut 70%.
    scored_dirs: set = set()
    for f in flags:
        if not f.get("is_active"):
            continue
        d = f["direction"]
        if d in scored_dirs:
            continue
        scored_dirs.add(d)
        base = 20 if f.get("dominant") else 10
        if d == "bullish":
            # Discount if strong bearish trend context
            if t_bear >= 25:
                pts = max(1, round(base * 0.30))
                bull_reasons.append(
                    f"{'Dominant b' if f.get('dominant') else 'B'}ullish flag on {f['timeframe']} "
                    f"(+{f['pole_pct']}% pole, target ${f['target']:,.4f}) "
                    f"[counter-trend discount: +{pts} vs base +{base}]"
                )
            else:
                pts = base
                bull_reasons.append(
                    f"{'Dominant b' if f.get('dominant') else 'B'}ullish flag on {f['timeframe']} "
                    f"(+{f['pole_pct']}% pole, target ${f['target']:,.4f})"
                )
            score += pts; g['pattern'] += pts
        else:
            # Discount if strong bullish trend context
            if t_bull >= 25:
                pts = max(1, round(base * 0.30))
                bear_reasons.append(
                    f"{'Dominant b' if f.get('dominant') else 'B'}earish flag on {f['timeframe']} "
                    f"({f['pole_pct']}% pole, target ${f['target']:,.4f}) "
                    f"[counter-trend discount: -{pts} vs base -{base}]"
                )
            else:
                pts = base
                bear_reasons.append(
                    f"{'Dominant b' if f.get('dominant') else 'B'}earish flag on {f['timeframe']} "
                    f"({f['pole_pct']}% pole, target ${f['target']:,.4f})"
                )
            score -= pts; g['pattern'] -= pts

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
            score += pts; g['pattern'] += pts
            bull_reasons.append(label)
        else:
            score -= pts; g['pattern'] -= pts
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
        score += 20; g['momentum'] += 20
        bull_reasons.append("MACD bullish cross — momentum flipping bullish, strong early signal")
    elif m_trend == "bullish" and m_hist is not None and m_hist > 0:
        # Counter-trend histogram: cap at +4 when strong bearish trend context
        pts = 4 if t_bear >= 25 else 10
        score += pts; g['momentum'] += pts
        note = " [counter-trend, capped]" if t_bear >= 25 else ""
        bull_reasons.append(f"MACD histogram positive ({m_hist:+.4f}) — bullish momentum sustained{note}")
    if m_cross == "bearish" or m_zero == "bearish":
        score -= 20; g['momentum'] -= 20
        bear_reasons.append("MACD bearish cross — momentum flipping bearish, strong early signal")
    elif m_trend == "bearish" and m_hist is not None and m_hist < 0:
        pts = 4 if t_bull >= 25 else 10
        score -= pts; g['momentum'] -= pts
        note = " [counter-trend, capped]" if t_bull >= 25 else ""
        bear_reasons.append(f"MACD histogram negative ({m_hist:+.4f}) — bearish momentum sustained{note}")

    # ── Trend indicators — EMA + SuperTrend + Ichimoku (capped bucket) ────────
    # These three all measure the same thing: "is the market in an uptrend?"
    # Letting them each score independently can add 50+ pts from one idea.
    # Cap the combined trend contribution at ±35 so they confirm each other
    # without triple-counting. Individual reasons still shown in confluence list.
    # NOTE: t_bull / t_bear were pre-computed above for counter-trend discounts.
    # Reset here for the full authoritative calculation with reasons.
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

    # EMA7/21 short-term cross — fast-responding momentum signal
    # These flip bullish within the first 1-2 candles of a breakout, far faster than EMA50/200.
    # Scored in the MOMENTUM group (not trend) to bypass TREND_CAP.
    ema7_cross  = ema.get("ema7_cross")
    short_trend = ema.get("short_trend")
    if ema7_cross == "bullish":
        score += 14; g['momentum'] += 14
        bull_reasons.append("EMA7 crossed above EMA21 — short-term momentum just turned bullish; fast-moving trend flip")
    elif ema7_cross == "bearish":
        score -= 14; g['momentum'] -= 14
        bear_reasons.append("EMA7 crossed below EMA21 — short-term momentum just turned bearish; fast-moving trend flip")
    elif short_trend == "bullish":
        score += 6; g['momentum'] += 6
        bull_reasons.append("EMA7 above EMA21 — short-term trend bullish; near-term buyers in control")
    elif short_trend == "bearish":
        score -= 6; g['momentum'] -= 6
        bear_reasons.append("EMA7 below EMA21 — short-term trend bearish; near-term sellers in control")

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
            score += 14; g['sentiment'] += 14
            bull_reasons.append(f"L/S ratio {ls_ratio} ({ls_short:.1f}% short) — crowd heavily short, contrarian long signal")
        elif ls_ratio < 0.85:
            score += 8; g['sentiment'] += 8
            bull_reasons.append(f"L/S ratio {ls_ratio} ({ls_short:.1f}% short) — moderate short bias, favours longs")
        elif ls_ratio > 2.5:
            score -= 14; g['sentiment'] -= 14
            bear_reasons.append(f"L/S ratio {ls_ratio} ({ls_long:.1f}% long) — crowd extremely long, contrarian short signal")
        elif ls_ratio > 1.5:
            score -= 8; g['sentiment'] -= 8
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
            score += pts; g['sentiment'] += pts
            bull_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — extreme fear, best buying zones{tf_note}")
        elif fg_val <= 30:
            pts = round(12 * tf_macro_w)
            score += pts; g['sentiment'] += pts
            bull_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — market fearful, contrarian bullish lean{tf_note}")
        elif fg_val >= 80:
            pts = round(25 * tf_macro_w)
            score -= pts; g['sentiment'] -= pts
            bear_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — extreme greed, historically marks tops{tf_note}")
        elif fg_val >= 65:
            pts = round(12 * tf_macro_w)
            score -= pts; g['sentiment'] -= pts
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
        score += pts; g['sentiment'] += pts
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        bull_reasons.append(
            f"News sentiment bullish — {news_bull} bullish vs {news_bear} bearish "
            f"articles in last 48h{tf_note}"
        )
    elif news_signal == "bearish":
        raw = min(15, max(6, news_bear * 4))
        pts = round(raw * tf_macro_w)
        score -= pts; g['sentiment'] -= pts
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
        score += 8; g['pattern'] += 8
        bull_reasons.append(f"Elliott Wave: {wave_label} (bullish phase) — weak supporting signal")
    elif wave_bias == "bearish":
        score -= 8; g['pattern'] -= 8
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
        score += pts; g['momentum'] += pts
        bull_reasons.append(div_desc or "Bullish RSI divergence — price lower low, RSI higher low")
    elif div_type == "bearish":
        pts = 18 if div_str >= 5 else 12
        score -= pts; g['momentum'] -= pts
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
        score += 16; g['pattern'] += 16
        bull_reasons.append(f"Bollinger squeeze breakout BULLISH — price closed above upper band {fmt_p(bb_upper)} after compression; explosive move signal")
    elif bb_squeeze and bb_breakout == "bearish":
        score -= 16; g['pattern'] -= 16
        bear_reasons.append(f"Bollinger squeeze breakdown BEARISH — price closed below lower band {fmt_p(bb_lower)} after compression; explosive move signal")
    elif bb_squeeze:
        if bb_pct_b > 0.6:
            score += 5; g['pattern'] += 5
            bull_reasons.append(f"Bollinger squeeze active — bands compressed, price upper half (%B {bb_pct_b:.2f}); breakout likely imminent")
        elif bb_pct_b < 0.4:
            score -= 5; g['pattern'] -= 5
            bear_reasons.append(f"Bollinger squeeze active — bands compressed, price lower half (%B {bb_pct_b:.2f}); breakdown risk elevated")
    elif bb_breakout == "bullish":
        score += 10; g['pattern'] += 10
        bull_reasons.append(f"Price above Bollinger upper band {fmt_p(bb_upper)} — strong bullish momentum")
    elif bb_breakout == "bearish":
        score -= 10; g['pattern'] -= 10
        bear_reasons.append(f"Price below Bollinger lower band {fmt_p(bb_lower)} — strong bearish momentum")

    # SuperTrend — flip scores outside the trend cap (it's a momentum event,
    # not just a trend state), sustained direction goes into the cap bucket
    st = analysis.get("supertrend") or {}
    st_dir     = st.get("direction")
    st_flipped = st.get("flipped", False)
    st_val     = st.get("value")
    if st_dir == "bullish":
        if st_flipped:
            score += 20; g['trend'] += 20   # fresh flip = momentum event → outside trend cap
            bull_reasons.append(f"SuperTrend flipped BULLISH — fresh BUY signal, trend just reversed up (support ${st_val:,.4f})" if st_val else "SuperTrend flipped BULLISH — fresh BUY signal")
        else:
            t_bull += 12; t_bull_r.append(f"SuperTrend bullish — price above dynamic support (${st_val:,.4f}), uptrend intact" if st_val else "SuperTrend bullish — uptrend intact")
    elif st_dir == "bearish":
        if st_flipped:
            score -= 20; g['trend'] -= 20   # fresh flip = momentum event → outside trend cap
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
    g['trend'] += eff_t_bull
    g['trend'] -= eff_t_bear
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
        score += 14; g['trend'] += 14
        bull_reasons.append(f"VWAP bullish cross — price just crossed above VWAP {fmt_v(vwap_val)}, institutional momentum shift")
    elif vwap_cross == "bearish":
        score -= 14; g['trend'] -= 14
        bear_reasons.append(f"VWAP bearish cross — price just crossed below VWAP {fmt_v(vwap_val)}, institutional selling pressure")
    elif vwap_pos == "above":
        pts = 10 if vwap_slope == "rising" else 6
        score += pts; g['trend'] += pts
        slope_note = " + VWAP rising" if vwap_slope == "rising" else ""
        bull_reasons.append(f"Price above VWAP{slope_note} {fmt_v(vwap_val)} — institutional buy-side structure intact")
    elif vwap_pos == "below":
        pts = 10 if vwap_slope == "falling" else 6
        score -= pts; g['trend'] -= pts
        slope_note = " + VWAP falling" if vwap_slope == "falling" else ""
        bear_reasons.append(f"Price below VWAP{slope_note} {fmt_v(vwap_val)} — institutional sell-side pressure dominant")

    # ── Stochastic RSI ────────────────────────────────────────────────────────
    # More sensitive than plain RSI — oscillates faster and gives earlier signals.
    # Cross from oversold/overbought zone is highest quality; zone alone is weaker.
    srsi           = analysis.get("stoch_rsi") or {}
    srsi_signal    = srsi.get("signal")
    srsi_k         = srsi.get("k")
    srsi_d         = srsi.get("d")
    if srsi_signal == "bull_surge":
        # K just entered overbought zone — momentum SURGE, not topping signal
        score += 16; g['momentum'] += 16
        bull_reasons.append(f"Stoch RSI momentum surge into overbought (K:{srsi_k}) — K just crossed 80, strong breakout momentum confirmation")
    elif srsi_signal == "bull_cross_oversold":
        score += 20; g['momentum'] += 20
        bull_reasons.append(f"Stoch RSI bullish cross from oversold (K:{srsi_k} D:{srsi_d}) — high-quality reversal signal")
    elif srsi_signal == "oversold":
        score += 10; g['momentum'] += 10
        bull_reasons.append(f"Stoch RSI oversold (K:{srsi_k} D:{srsi_d}) — momentum deeply oversold, bounce likely")
    elif srsi_signal == "near_oversold":
        score += 5; g['momentum'] += 5
        bull_reasons.append(f"Stoch RSI near oversold (K:{srsi_k}) — mild oversold lean")
    elif srsi_signal == "bear_collapse":
        # K just entered oversold zone — momentum COLLAPSE, not reversal signal yet
        score -= 16; g['momentum'] -= 16
        bear_reasons.append(f"Stoch RSI momentum collapse into oversold (K:{srsi_k}) — K just crossed 20, strong breakdown momentum confirmation")
    elif srsi_signal == "bear_cross_overbought":
        score -= 20; g['momentum'] -= 20
        bear_reasons.append(f"Stoch RSI bearish cross from overbought (K:{srsi_k} D:{srsi_d}) — high-quality topping/reversal signal")
    elif srsi_signal == "overbought":
        # Stable overbought (K has been >80 for multiple candles) — reduce penalty vs fresh cross
        score -= 8; g['momentum'] -= 8
        bear_reasons.append(f"Stoch RSI overbought (K:{srsi_k} D:{srsi_d}) — momentum extended; not a topping signal on its own")
    elif srsi_signal == "near_overbought":
        score -= 4; g['momentum'] -= 4
        bear_reasons.append(f"Stoch RSI near overbought (K:{srsi_k}) — mild extended lean")

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
        score += pts; g['flow'] += pts
        bull_reasons.append(vol_desc or f"Volume confirmation bullish ({vol_ratio:.1f}× avg)")
    elif vol_sig == "bearish":
        pts = 12 if vol_ratio >= 2.0 else 8
        score -= pts; g['flow'] -= pts
        bear_reasons.append(vol_desc or f"Volume confirmation bearish ({vol_ratio:.1f}× avg)")

    # ── BTC mining / on-chain signals (BTC only) ──────────────────────────────
    # Hash Ribbon  : miners recovered (+12 buy cross / +7 bull) or capitulating (-10/-6)
    # Halving phase: mid (6-18 mo post-halving) = historically bullish window (+6)
    # Profitability: price vs estimated break-even cost
    # Difficulty   : rising network difficulty = miner confidence context (+4/-4)
    # Group mapping: Hash Ribbon + Profitability + Difficulty → g['flow'] (on-chain network signals)
    #                Halving phase → g['sentiment'] (macro cycle context)
    mining = analysis.get("btc_mining") or {}
    if mining:
        ribbon = mining.get("hash_ribbon", "neutral")
        if ribbon == "buy":           # fresh 30d/60d bullish cross
            score += 12;  g['flow'] += 12
            bull_reasons.append("▲ Hash Ribbon buy signal — miner capitulation over, 30d MA crossed above 60d MA")
        elif ribbon == "bull":        # 30d > 60d, no fresh cross
            score += 7;   g['flow'] += 7
            bull_reasons.append("▲ Hash Ribbon bullish — miners recovering, 30d MA above 60d MA")
        elif ribbon == "capitulation": # fresh bearish cross
            score -= 10;  g['flow'] -= 10
            bear_reasons.append("▼ Hash Ribbon capitulation — miners under stress, 30d MA crossed below 60d MA")
        elif ribbon == "bear":         # 30d < 60d
            score -= 6;   g['flow'] -= 6
            bear_reasons.append("▼ Hash Ribbon bearish — miner sell pressure, 30d MA below 60d MA")

        phase = mining.get("halving_phase")
        months = mining.get("halving_months_since", 0) or 0
        if phase == "mid":            # 6-18 months post-halving — historically strongest bull window
            score += 6;   g['sentiment'] += 6
            bull_reasons.append(f"▲ Halving cycle mid-phase ({months:.0f} mo post-halving) — historically strongest price appreciation window")
        elif phase == "early":        # 0-6 months — consolidation, slight bullish lean
            score += 3;   g['sentiment'] += 3
            bull_reasons.append(f"▲ Early post-halving phase ({months:.0f} mo) — supply shock still digesting, accumulation zone")
        elif phase == "late":         # 18-36 months — late cycle, distribution risk
            score -= 4;   g['sentiment'] -= 4
            bear_reasons.append(f"▼ Late halving cycle ({months:.0f} mo post-halving) — historical distribution / top formation zone")

        prof = mining.get("profitability_ratio")
        if prof is not None:
            if prof >= 2.0:           # very profitable → miners holding, not selling
                score += 8;  g['flow'] += 8
                bull_reasons.append(f"▲ Miners highly profitable ({prof:.1f}× break-even) — no forced selling pressure")
            elif prof >= 1.3:
                score += 4;  g['flow'] += 4
                bull_reasons.append(f"▲ Miners profitable ({prof:.1f}× break-even) — healthy miner economics")
            elif prof < 1.05:         # at or near break-even → capitulation risk
                score -= 8;  g['flow'] -= 8
                bear_reasons.append(f"▼ Miners near break-even ({prof:.1f}×) — selling pressure risk, potential capitulation")

        diff_chg = mining.get("difficulty_change")
        if diff_chg is not None:
            if diff_chg >= 3.0:       # rising difficulty = more miners joining = bullish context
                score += 4;  g['flow'] += 4
                bull_reasons.append(f"▲ Difficulty rising +{diff_chg:.1f}% — new miners joining, network confidence high")
            elif diff_chg <= -3.0:    # falling difficulty = miners leaving = bearish
                score -= 4;  g['flow'] -= 4
                bear_reasons.append(f"▼ Difficulty dropping {diff_chg:.1f}% — miners leaving, reduced network security")

    # ── Confluence Engine ─────────────────────────────────────────────────────────
    # Analyzes cross-group relationships to dynamically adjust the final score.
    # Groups: TREND | MOMENTUM | FLOW | SENTIMENT | PATTERN
    # BTC additionally populates FLOW (Hash Ribbon, Profitability, Difficulty)
    # and SENTIMENT (Halving phase) from mining/on-chain data.
    # Indicators do not score in isolation — they validate or contradict each other.

    def _gdir(v):
        return 'bull' if v > 8 else ('bear' if v < -8 else 'neutral')

    gdir = {k: _gdir(v) for k, v in g.items()}
    overall_dir = 'bull' if score > 0 else 'bear'

    # Groups agreeing / conflicting with the overall score direction
    agreeing    = [k for k, d in gdir.items() if d == overall_dir]
    conflicting = [k for k, d in gdir.items() if d != overall_dir and d != 'neutral']
    n_agree     = len(agreeing)

    combo_pts = 0   # additive bonuses/penalties from specific cross-group combos

    # ── Combo 1: Flow confirms Trend (real money behind the move) ─────────────────
    if gdir['flow'] == gdir['trend'] != 'neutral':
        pts = 12
        combo_pts += pts if score > 0 else -pts
        label = "🔗 Flow+Trend confluence — CVD/OI confirms trend direction; real money behind the move"
        (bull_reasons if score > 0 else bear_reasons).append(label)

    # ── Combo 2: Momentum confirms Trend (healthy trend continuation) ─────────────
    if gdir['momentum'] == gdir['trend'] != 'neutral':
        pts = 8
        combo_pts += pts if score > 0 else -pts
        label = "🔗 Momentum+Trend confluence — MACD/RSI aligned with trend; healthy continuation signal"
        (bull_reasons if score > 0 else bear_reasons).append(label)

    # ── Combo 3: Flow contradicts Trend (CVD divergence warning) ─────────────────
    if gdir['flow'] not in ('neutral', gdir['trend']) and gdir['trend'] != 'neutral':
        penalty = min(abs(g['flow']), 20)
        combo_pts += penalty if score < 0 else -penalty   # works against dominant direction
        if score > 0:
            bear_reasons.append(f"⚠️ Flow-Trend divergence — CVD/Volume contradicts uptrend (−{penalty} pts caution); watch for reversal")
        else:
            bull_reasons.append(f"⚠️ Flow-Trend divergence — CVD/Volume contradicts downtrend (−{penalty} pts caution); squeeze risk elevated")

    # ── Combo 4: Momentum diverging from Trend (early exhaustion warning) ─────────
    if gdir['momentum'] not in ('neutral', gdir['trend']) and gdir['trend'] != 'neutral':
        penalty = min(abs(g['momentum']), 12)
        combo_pts += penalty if score < 0 else -penalty
        if score > 0:
            bear_reasons.append(f"⚠️ Momentum diverging from trend (−{penalty} pts) — MACD/RSI losing alignment; trend exhaustion risk")
        else:
            bull_reasons.append(f"⚠️ Momentum diverging from downtrend (−{penalty} pts) — possible reversal building; monitor closely")

    # ── Combo 5: Extreme Funding + Trend aligned (maximum squeeze/flush setup) ───
    fr_val = funding.get("current", 0.0) or 0.0
    if abs(fr_val) >= 0.02 and gdir['trend'] == overall_dir and gdir['trend'] != 'neutral':
        pts = 15
        combo_pts += pts if score > 0 else -pts
        if score > 0:
            bull_reasons.append(f"🔗 Extreme Funding+Trend aligned — max short positioning ({fr_val:.4f}%) + bullish trend = extreme squeeze setup")
        else:
            bear_reasons.append(f"🔗 Extreme Funding+Trend aligned — max long positioning ({fr_val:.4f}%) + bearish trend = extreme flush setup")

    # ── Combo 6: SuperTrend flip + Volume confirmation (breakout with conviction) ─
    vol_sig_local = (analysis.get("vol_signal") or {}).get("signal")
    st_local      = analysis.get("supertrend") or {}
    st_flipped_local = st_local.get("flipped", False)
    st_dir_local     = st_local.get("direction")
    vol_with_trend  = (vol_sig_local == 'bullish' and score > 0) or (vol_sig_local == 'bearish' and score < 0)
    st_flip_with_trend = st_flipped_local and ((st_dir_local == 'bullish' and score > 0) or (st_dir_local == 'bearish' and score < 0))
    if st_flip_with_trend and vol_with_trend:
        pts = 10
        combo_pts += pts if score > 0 else -pts
        if score > 0:
            bull_reasons.append("🔗 SuperTrend flip + Volume — trend reversal confirmed with elevated volume; high-conviction breakout")
        else:
            bear_reasons.append("🔗 SuperTrend flip + Volume — trend breakdown confirmed with elevated volume; high-conviction breakdown")

    # ── Combo 7: RSI divergence + MACD cross (dual momentum reversal) ─────────────
    rsi_div_local  = analysis.get("rsi_divergence") or {}
    div_type_local = rsi_div_local.get("type")
    macd_local     = analysis.get("macd") or {}
    macd_cross_local = macd_local.get("cross")
    macd_zero_local  = macd_local.get("zero_cross")
    rsi_div_bull  = div_type_local == 'bullish'
    rsi_div_bear  = div_type_local == 'bearish'
    macd_bull_sig = macd_cross_local == 'bullish' or macd_zero_local == 'bullish'
    macd_bear_sig = macd_cross_local == 'bearish' or macd_zero_local == 'bearish'
    if (rsi_div_bull and macd_bull_sig) or (rsi_div_bear and macd_bear_sig):
        pts = 12
        is_bull_reversal = rsi_div_bull and macd_bull_sig
        combo_pts += pts if is_bull_reversal else -pts
        if is_bull_reversal:
            bull_reasons.append("🔗 RSI divergence + MACD cross — dual momentum reversal confirmed; high-quality bottom signal")
        else:
            bear_reasons.append("🔗 RSI divergence + MACD cross — dual momentum reversal confirmed; high-quality top signal")

    # ── Combo 8: Bollinger squeeze + Volume breakout (coiled spring released) ──────
    bb_local      = analysis.get("bollinger") or {}
    bb_squeeze_l  = bb_local.get("squeeze", False)
    bb_breakout_l = bb_local.get("breakout")
    bb_bull_break = bb_squeeze_l and bb_breakout_l == 'bullish'
    bb_bear_break = bb_squeeze_l and bb_breakout_l == 'bearish'
    if (bb_bull_break and vol_with_trend and score > 0) or (bb_bear_break and vol_with_trend and score < 0):
        pts = 10
        combo_pts += pts if score > 0 else -pts
        if score > 0:
            bull_reasons.append("🔗 BB squeeze + Volume — compressed bands broke bullish with volume confirmation; explosive move setup")
        else:
            bear_reasons.append("🔗 BB squeeze + Volume — compressed bands broke bearish with volume confirmation; explosive breakdown setup")

    # ── Combo 9: BTC Hash Ribbon + Trend aligned (on-chain confirms price trend) ─
    # BTC-only. Hash Ribbon is a lagging but high-accuracy miner health signal.
    # When it agrees with the price trend direction, it adds deep structural weight.
    if mining:
        ribbon_local = mining.get("hash_ribbon", "neutral")
        ribbon_bull  = ribbon_local in ("buy", "bull")
        ribbon_bear  = ribbon_local in ("capitulation", "bear")
        if ribbon_bull and gdir['trend'] == 'bull' and score > 0:
            pts = 14
            combo_pts += pts
            bull_reasons.append(f"🔗 Hash Ribbon+Trend (BTC) — miners healthy ({ribbon_local}) + bullish trend = structural BTC bull setup")
        elif ribbon_bear and gdir['trend'] == 'bear' and score < 0:
            pts = 14
            combo_pts -= pts
            bear_reasons.append(f"🔗 Hash Ribbon+Trend (BTC) — miner stress ({ribbon_local}) + bearish trend = structural BTC bear pressure")

    # ── Combo 10: BTC Profitability extreme + Halving phase (macro cycle alignment) ─
    # When miners are highly profitable AND we're in the historical bull phase window,
    # both on-chain and macro cycle agree → high conviction BTC bullish structural context.
    if mining:
        prof_local  = mining.get("profitability_ratio")
        phase_local = mining.get("halving_phase")
        if prof_local is not None and prof_local >= 2.0 and phase_local in ("mid", "early") and score > 0:
            pts = 10
            combo_pts += pts
            bull_reasons.append(f"🔗 Miner Profitability+Halving Phase (BTC) — highly profitable ({prof_local:.1f}×) in {phase_local} post-halving phase = structural accumulation conditions")
        elif prof_local is not None and prof_local < 1.05 and phase_local == "late" and score < 0:
            pts = 10
            combo_pts -= pts
            bear_reasons.append(f"🔗 Miner Stress+Late Cycle (BTC) — near break-even ({prof_local:.1f}×) in late halving cycle = maximum capitulation risk")

    # Apply combo points
    score += combo_pts

    # ── Multi-group confluence multiplier ─────────────────────────────────────────
    # Applied after combo adjustments — amplifies already-strong multi-group signals
    MULT_LABELS = {5: "Penta", 4: "Quad", 3: "Triple", 2: "Double"}
    if n_agree >= 4 and len(conflicting) == 0:
        mult = 1.30
        lbl = MULT_LABELS.get(n_agree, "Multi")
        aligned_str = " + ".join(a.capitalize() for a in agreeing[:4])
        msg = f"⚡ {lbl} confluence ({n_agree}/5 groups: {aligned_str}) — 30% strength amplifier"
        (bull_reasons if score > 0 else bear_reasons).append(msg)
    elif n_agree == 3 and len(conflicting) <= 1:
        mult = 1.15
        aligned_str = " + ".join(a.capitalize() for a in agreeing[:3])
        msg = f"⚡ Triple confluence ({aligned_str}) — 15% strength amplifier"
        (bull_reasons if score > 0 else bear_reasons).append(msg)
    elif n_agree >= 2 and len(conflicting) == 0:
        mult = 1.08
    elif len(conflicting) >= 2:
        mult = 0.82   # multiple groups in conflict — noisy, reduce confidence
        conf_str = " + ".join(c.capitalize() for c in conflicting[:3])
        msg = f"⚠️ Conflicting groups ({conf_str}) — −18% confidence penalty; signals mixed"
        (bear_reasons if score > 0 else bull_reasons).append(msg)
    else:
        mult = 1.00

    score = round(score * mult)

    # ── Final direction ───────────────────────────────────────────────────────
    #   VWAP cross +14, Stoch RSI bull cross +20, Volume +12 → total ~320
    # In practice signals overlap — realistic ceiling ~200.
    # MAX_SCORE is the realistic ceiling — what a genuinely strong multi-signal setup
    # actually scores. Theoretical max (every signal firing perfectly) is ~480, but
    # signals overlap in practice. Using 480 compresses everything into 0–20% and
    # makes a solid 150-pt signal display as "31/100 WEAK" — wrong calibration.
    # Realistic ceiling (~200 pts) calibrates the display so:
    #   Score 35  (threshold)  → 16/100  WEAK        (just signalling — 2-3 signals)
    #   Score 80               → 36/100  MODERATE     (several aligned)
    #   Score 120              → 55/100  STRONG        (good multi-indicator confluence)
    #   Score 160+             → 73+/100 CONFIRMED     (max conviction)
    MAX_SCORE = 220.0

    strength = min(int(abs(score) / MAX_SCORE * 100), 100)

    # Threshold at 35 pts — requires at least 2-3 real signals agreeing.
    DIRECTION_THRESHOLD = 35
    if score >= DIRECTION_THRESHOLD:
        direction = "LONG"
    elif score <= -DIRECTION_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # Strength tiers (strength = score / 220 * 100):
    # Weak     (16–32): score  35–70  — 2-3 signals, cautious 25% size
    # Moderate (33–50): score  73–110 — several aligned, 50% size
    # Strong   (51–68): score 112–150 — good confluence, full size
    # Confirmed  (69+): score  152+   — maximum confluence, can scale
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

        # Dynamic limit-entry: scan real technical levels in priority order.
        # For LONG: find the nearest support BELOW current price to place a limit buy.
        # For SHORT: find the nearest resistance ABOVE current price to place a limit sell.
        # Priority: EMA21 → BB upper/lower → recent 5-candle swing high/low → current price.
        # Each level is only used when it is within LEVEL_CAP of current price (avoids
        # suggesting entries that would never fill on a 4-24h timeframe).
        LEVEL_CAP  = 0.04    # max 4% away — if all levels are further, enter at market
        SWING_CAP  = 0.025   # swing highs/lows are noisy; tighter cap (2.5%)

        ema21_val  = (analysis.get("ema_trend") or {}).get("ema21")
        _bb        = analysis.get("bollinger") or {}
        bb_upper   = _bb.get("upper")
        bb_lower   = _bb.get("lower")
        # Last 5 closed candles (skip live candle at index -1)
        _closed    = candles[-6:-1] if len(candles) >= 6 else candles[:-1]
        swing_high = max((c["high"] for c in _closed), default=None) if _closed else None
        swing_low  = min((c["low"]  for c in _closed), default=None) if _closed else None

        def _within(level, above: bool) -> bool:
            """True if level is on the right side and within cap distance."""
            if level is None or current_price <= 0:
                return False
            if above:
                gap = (level - current_price) / current_price
            else:
                gap = (current_price - level) / current_price
            return 0 < gap <= LEVEL_CAP

        def _within_swing(level, above: bool) -> bool:
            if level is None or current_price <= 0:
                return False
            gap = ((level - current_price) if above else (current_price - level)) / current_price
            return 0 < gap <= SWING_CAP

        if direction == "LONG":
            # Collect support levels below current price; pick the highest (closest)
            supports = []
            if _within(ema21_val, above=False):      supports.append(ema21_val)
            if _within(bb_lower,  above=False):      supports.append(bb_lower)
            if _within_swing(swing_low, above=False): supports.append(swing_low)
            base = max(supports) if supports else current_price

        elif direction == "SHORT":
            # Collect resistance levels above current price; pick the lowest (closest)
            resistances = []
            if _within(ema21_val, above=True):        resistances.append(ema21_val)
            if _within(bb_upper,  above=True):        resistances.append(bb_upper)
            if _within_swing(swing_high, above=True): resistances.append(swing_high)
            base = min(resistances) if resistances else current_price

        else:
            base = current_price

        entry = round(base, 8)

        eff_atr  = min(atr, max_atr_abs)
        sl_dist  = eff_atr * sl_m
        tp1_dist = eff_atr * tp1_m
        tp2_dist = eff_atr * tp2_m
        tp3_dist = eff_atr * tp3_m

        def _tp_short(dist):
            """Return TP price for a SHORT, or None if it would require >95% drop."""
            target = entry - dist
            if target <= entry * 0.05:   # >95% drop — not achievable
                return None
            return round(target, 8)

        if direction == "LONG":
            sl = round(max(entry * 0.001, entry - sl_dist), 8)
            tp_targets = [
                round(entry + tp1_dist, 8),
                round(entry + tp2_dist, 8),
                round(entry + tp3_dist, 8),
            ]
        elif direction == "SHORT":
            sl = round(entry + sl_dist, 8)
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
        "current_price": round(current_price, 8) if current_price else None,
    }
