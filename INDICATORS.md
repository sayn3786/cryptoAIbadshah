# CryptoSTARS — Indicator Reference

Every indicator used in `generate_signal()`, their scoring points, group membership,
and how they integrate with each other to produce a final direction + strength.

---

## How the Score Becomes a Signal

```
Raw score (positive = bullish, negative = bearish)
  → apply confluence combos (bonus/penalty)
  → apply multi-group multiplier
  → strength = min(abs(score) / 220 × 100, 100)
  → direction = LONG if score ≥ 35 | SHORT if score ≤ −35 | NEUTRAL
```

| Constant | Value | Meaning |
|---|---|---|
| `MAX_SCORE` | 220 | Realistic ceiling for normalising strength |
| `DIRECTION_THRESHOLD` | 35 | Minimum score to trigger LONG or SHORT |
| `TREND_CAP` | 35 | Prevents triple-counting EMA + SuperTrend + Ichimoku |

### Strength Tiers

| Strength | Tier | Position Size |
|---|---|---|
| — | Neutral | No trade |
| < 33 | Weak | 25% |
| 33–50 | Moderate | 50% |
| 51–68 | Strong | Full |
| ≥ 69 | Confirmed | Full + scale |

---

## Indicator Groups

Each indicator is tagged to one of five groups.
Alignment across groups drives the multiplier (see bottom of doc).

| Group | Covers |
|---|---|
| `trend` | EMA 50/200, SuperTrend, Ichimoku, VWAP |
| `momentum` | RSI, RSI slope, Price ROC, Candle consistency, EMA 7/21, MACD, Stoch RSI, RSI divergence |
| `flow` | CVD divergence, Funding rate, Open interest, Volume, Hash ribbon, Profitability, Difficulty |
| `sentiment` | Long/short ratio, Fear & Greed, News, BTC halving phase |
| `pattern` | FVG/BAG, Flag patterns, Engulfing, Bollinger Bands, CHoCH, Liq Grab, Acc+EQL+FVG, Elliott Wave |

---

## Timeframe Macro Weight (`tf_macro_w`)

Fear & Greed and News update at most daily — applying full weight on 1H is misleading.

| TF | Weight |
|---|---|
| 1H | 0.30 |
| 2H | 0.40 |
| 4H | 0.50 |
| 8H | 0.65 |
| 12H | 0.80 |
| 1D+ | 1.00 |

---

## Indicators — Full Scoring Table

### RSI Level · `momentum`
*Contrarian. Extreme readings only — 45–65 is genuinely ambiguous and scores 0.*

| Condition | Points |
|---|---|
| RSI < 25 (extremely oversold) | +22 |
| RSI 25–34 (oversold) | +12 |
| RSI 35–44 (below midline) | +4 |
| RSI 45–65 | 0 |
| RSI 65–75 (overbought) | −12 |
| RSI > 75 (extremely overbought) | −22 |

---

### RSI Slope · `momentum`
*Rate of change over last 5 valid RSI values. Captures momentum direction independent of level.*

| Condition | Points |
|---|---|
| Slope > 18 (surging) | +16 |
| Slope 9–18 (rising) | +9 |
| Slope 4–9 (drifting up) | +4 |
| Slope −4 to 4 | 0 |
| Slope −9 to −4 (drifting down) | −4 |
| Slope −18 to −9 (falling) | −9 |
| Slope < −18 (collapsing) | −16 |

---

### Price ROC · `momentum`
*4-candle rate of change — captures "coin is actively moving right now".*

| Condition | Points |
|---|---|
| ROC > 12% | +20 |
| ROC 6–12% | +12 |
| ROC 2.5–6% | +5 |
| ROC −2.5 to 2.5% | 0 |
| ROC −6 to −2.5% | −5 |
| ROC −12 to −6% | −12 |
| ROC < −12% | −20 |

---

### Candle Consistency · `momentum`
*Last N closed candles (N varies by TF — lower TFs are noisier so more candles required).*

| Result | Points |
|---|---|
| 4/4 bullish (all green) | +12 |
| 3/4 bullish | +6 |
| 2/4 (split) | 0 |
| 1/4 bullish | −6 |
| 0/4 bullish (all red) | −12 |

---

### CVD Divergence · `flow`
*Primary flow signal. Compares spot vs futures cumulative volume delta. Overrides simple CVD when present.*

| Signal | Points | Description |
|---|---|---|
| `spot_dominated_up` | +35 | Spot CVD > 10× futures (strong organic buying) |
| `spot_heavy_up` | +30 | Spot CVD 2–10× futures |
| `confirmed_up` | +26 | Both spot + futures bullish (balanced) |
| `spot_led_up` | +20 | Spot bullish, futures data missing |
| `futures_led_up` | −16 | Futures pump but spot not confirming (suspect) |
| `futures_dominated_up` | −14 | Futures > 50× spot (likely leverage, not real demand) |
| `futures_dominated_down` | +10 | Speculative short pile-on (contrarian) |
| `futures_led_down` | +16 | Futures selling while spot rising (dumb money shorts) |
| `futures_heavy_down` | −14 | Futures 10–50× spot selling |
| `spot_led_down` | −20 | Spot selling, futures not following |
| `confirmed_down` | −26 | Both spot + futures bearish |
| `spot_heavy_down` | −30 | Spot CVD 2–10× futures bearish |
| `spot_dominated_down` | −35 | Spot CVD > 10× futures bearish |

*Magnitude intensifier: ±min(5, ratio_adj) added when ratio is extreme.*

**Simple CVD fallback** (when no divergence signal):

| Condition | Points |
|---|---|
| Spot CVD bullish | +14 |
| Spot CVD bearish | −14 |
| Futures CVD bullish | +7 |
| Futures CVD bearish | −7 |

---

### Funding Rate · `flow`
*CoinGlass preferred (multi-exchange). Extreme negative = crowded shorts = contrarian bullish.*

| Condition | Points |
|---|---|
| FR < −0.02% (extremely short) | +30 |
| FR −0.02% to −0.005% (moderately short) | +15 |
| FR 0 to 0.015% | 0 |
| FR 0.015% to 0.04% (elevated longs) | −15 |
| FR > 0.04% (extremely long) | −30 |

---

### Open Interest · `flow`
*CoinGlass preferred. Interpreted with price direction to identify new positions vs liquidations.*

| Condition | Points |
|---|---|
| OI +5%+ AND price up (new longs entering) | +12 |
| OI +5%+ AND price down (new shorts entering) | −12 |
| OI −5%+ AND price up (shorts squeezed out) | +8 |
| OI −5%+ AND price down (longs capitulating) | −8 |

---

### EMA 7/21 · `momentum`
*Short-term trend. Cross scored separately from sustained state.*

| Condition | Points |
|---|---|
| EMA 7/21 cross bullish (fresh) | +14 |
| EMA 7/21 cross bearish (fresh) | −14 |
| EMA 7 above 21 sustained | +6 |
| EMA 7 below 21 sustained | −6 |

---

### EMA 50/200 · `trend` *(subject to TREND_CAP = 35)*

| Condition | Points |
|---|---|
| Price above both 50 & 200 | +18 |
| Price above 50, below 200 (medium bullish) | +8 |
| Price above 50 only | +5 |
| Price below 50 only | −5 |
| Price below 50, above 200 (medium bearish) | −8 |
| Price below both 50 & 200 | −18 |

---

### SuperTrend · `trend` + `momentum`
*Flip (fresh reversal) bypasses TREND_CAP; sustained state is inside cap.*

| Condition | Points |
|---|---|
| Bullish flip (fresh) | +20 (outside cap) |
| Bullish sustained | +12 (inside cap) |
| Bearish flip (fresh) | −20 (outside cap) |
| Bearish sustained | −12 (inside cap) |

---

### Ichimoku Cloud · `trend` *(subject to TREND_CAP = 35)*

| Condition | Points |
|---|---|
| Price above cloud | +15 |
| Cloud green (Span A > Span B) | +8 |
| TK bullish cross | +12 |
| TK bearish cross | −12 |
| Cloud red (Span A < Span B) | −8 |
| Price below cloud | −15 |

---

### MACD · `momentum`

| Condition | Points |
|---|---|
| Line cross bullish OR zero-line cross bullish | +20 |
| Trend bullish + histogram > 0 (no cross) | +10 (capped to +4 in strong bear trend) |
| Line cross bearish OR zero-line cross bearish | −20 |
| Trend bearish + histogram < 0 (no cross) | −10 (capped to −4 in strong bull trend) |

---

### RSI Divergence · `momentum`
*14-candle window. Divergence between price and RSI.*

| Condition | Points |
|---|---|
| Bullish divergence, strength ≥ 5 | +18 |
| Bullish divergence, strength < 5 | +12 |
| Bearish divergence, strength ≥ 5 | −18 |
| Bearish divergence, strength < 5 | −12 |

---

### Bollinger Bands · `pattern`

| Condition | Points |
|---|---|
| Squeeze + price breaks above upper band | +16 |
| Squeeze + price breaks below lower band | −16 |
| Squeeze active + %B > 0.6 (upper half) | +5 |
| Squeeze active + %B < 0.4 (lower half) | −5 |
| Price above upper band (no squeeze) | +10 |
| Price below lower band (no squeeze) | −10 |

---

### Stochastic RSI · `momentum`

| Condition | Points |
|---|---|
| K crossing into overbought zone (momentum surge) | +16 |
| Bull cross from oversold zone | +20 |
| K in oversold zone | +10 |
| K near oversold | +5 |
| K crossing into oversold (collapse) | −16 |
| Bear cross from overbought zone | −20 |
| K in overbought zone (stable) | −8 |
| K near overbought | −4 |

---

### VWAP · `trend`

| Condition | Points |
|---|---|
| VWAP cross bullish (fresh) | +14 |
| VWAP cross bearish (fresh) | −14 |
| Price above VWAP + VWAP rising | +10 |
| Price above VWAP (flat/falling) | +6 |
| Price below VWAP + VWAP falling | −10 |
| Price below VWAP (rising) | −6 |

---

### Volume Confirmation · `flow`

| Condition | Points |
|---|---|
| Bullish candle + vol_ratio ≥ 2.0 (whale buy) | +12 |
| Bullish candle + vol_ratio < 2.0 (elevated buy) | +8 |
| Bearish candle + vol_ratio ≥ 2.0 (whale sell) | −12 |
| Bearish candle + vol_ratio < 2.0 (elevated sell) | −8 |

---

### Long/Short Ratio · `sentiment`

| Condition | Points |
|---|---|
| Ratio < 0.65 (crowd heavily short) | +14 |
| Ratio 0.65–0.85 (moderate short bias) | +8 |
| Ratio 0.85–1.5 | 0 |
| Ratio 1.5–2.5 (crowd long-heavy) | −8 |
| Ratio > 2.5 (crowd extremely long) | −14 |

---

### Fear & Greed · `sentiment`
*Scaled by `tf_macro_w` (0.30 on 1H → 1.00 on 1D+).*

| Condition | Base Points | At 1H (×0.30) | At 1D (×1.00) |
|---|---|---|---|
| FG ≤ 15 (extreme fear) | +25 | +7.5 | +25 |
| FG 16–30 (fear) | +12 | +3.6 | +12 |
| FG 31–64 | 0 | 0 | 0 |
| FG 65–79 (greed) | −12 | −3.6 | −12 |
| FG ≥ 80 (extreme greed) | −25 | −7.5 | −25 |

---

### News Sentiment · `sentiment`
*Scaled by `tf_macro_w`. Based on article count, capped at ±15.*

| Condition | Points |
|---|---|
| signal == "bullish" | min(bullish_count × 4, 15) × tf_macro_w |
| signal == "bearish" | min(bearish_count × 4, 15) × tf_macro_w |

*Sources: LunarCrush (social sentiment pulse) + RSS (CoinDesk, Cointelegraph).*

---

### Fair Value Gap (FVG) + Break Away Gap (BAG) · `pattern`
*Unfilled gaps only. BAG = same 3-candle structure but middle candle range ≥ 2.5× avg.*

| Condition | Points Each | Max Total |
|---|---|---|
| Bullish FVG below price (support) | +8 | +24 |
| Bullish BAG below price (strong support) | +14 | +24 |
| Bearish FVG above price (resistance) | −8 | −24 |
| Bearish BAG above price (strong resistance) | −14 | −24 |

**Difference:** FVG gaps tend to fill (~70% of the time). BAG gaps hold — the explosive breakout candle signals strong conviction, making the zone a reliable level.

---

### Flag Patterns · `pattern`

| Condition | Points |
|---|---|
| Dominant bullish flag | +20 |
| Secondary bullish flag | +10 |
| Dominant bearish flag | −20 |
| Secondary bearish flag | −10 |

*Counter-trend discount: ×0.30 applied if strong opposite trend (trend bucket ≥ 25 pts).*

---

### Engulfing Candle · `pattern`
*Only checks the single most recent confirmed closed candle.*

| Condition | Points |
|---|---|
| Bullish engulfing (1 candle ago) | +25 |
| Bearish engulfing (1 candle ago) | −25 |

---

### CHoCH (Change of Character) · `pattern`
*Fresh structure shift — decays with age.*

```
freshness = max(0, 1 − candles_ago / 10)
points    = round(±18 × freshness)
```

| candles_ago | Max Points |
|---|---|
| 0–1 | ±18 |
| 5 | ±9 |
| 10+ | 0 |

---

### Liquidity Grab · `pattern`
*Wick through a key level then close back — decays faster than CHoCH.*

```
freshness = max(0, 1 − candles_ago / 5)
points    = round(±15 × freshness)
```

| candles_ago | Max Points |
|---|---|
| 0–1 | ±15 |
| 3 | ±6 |
| 5+ | 0 |

---

### ICT Triple Combo: Acc + Equal H/L + FVG · `pattern`
*Accumulation range + Equal Highs/Lows at edge + opposing FVG = pump/dump setup.*

```
strength  = 55–100 (from detect_acc_eql_fvg_setup)
points    = max(8, round(25 × (strength − 55) / 45))
```

| Detector Strength | Points |
|---|---|
| 55 | +8 (minimum) |
| 70 | +15 |
| 85 | +21 |
| 100 | +25 (maximum) |

---

### Options Expiry Pin Pressure · `sentiment`
*Only active inside the pinning window. BTC only; ALTs scaled by BTC correlation in recommendations.*

| Window | Days to Expiry |
|---|---|
| Quarterly | ≤ 7 days |
| Monthly | ≤ 4 days |
| Weekly | ≤ 2 days |

| Condition | Effect on Strength |
|---|---|
| Max pain above price (bullish) AND signal = LONG | +signal_pts (up to +20) |
| Max pain above price (bullish) AND signal = SHORT | −signal_pts × 0.5 |
| Max pain below price (bearish) AND signal = SHORT | +signal_pts (up to +20) |
| Max pain below price (bearish) AND signal = LONG | −signal_pts × 0.5 |

---

### BTC-Only Indicators

These only score when the symbol is `BTCUSDT`.

#### BTC Hash Ribbon · `flow`

| Condition | Points |
|---|---|
| `buy` (30d MA crosses above 60d MA) | +12 |
| `bull` (sustained above) | +7 |
| `capitulation` (30d crosses below 60d) | −10 |
| `bear` (sustained below) | −6 |

#### BTC Halving Phase · `sentiment`

| Phase | Months Since Halving | Points |
|---|---|---|
| Early | 0–6 mo | +3 |
| Mid | 6–18 mo (historical bull window) | +6 |
| Late | 18–36 mo (distribution zone) | −4 |
| Bear | 36+ mo | 0 |

#### BTC Miner Profitability · `flow`

| Condition | Points |
|---|---|
| Profitability ≥ 2.0 (highly profitable) | +8 |
| Profitability 1.3–2.0 (profitable) | +4 |
| Profitability 1.05–1.3 | 0 |
| Profitability < 1.05 (near break-even) | −8 |

#### BTC Mining Difficulty Change · `flow`

| Condition | Points |
|---|---|
| Difficulty change ≥ +3% (rising) | +4 |
| Difficulty change ≤ −3% (falling) | −4 |

---

### Elliott Wave · `pattern`
*Lowest reliability — weak tiebreaker only.*

| Condition | Points |
|---|---|
| Wave bias bullish | +8 |
| Wave bias bearish | −8 |

---

## Confluence Combos (Applied After Individual Scores)

These bonuses/penalties fire based on group bucket alignment, not raw indicator values.

| Combo | Condition | Points |
|---|---|---|
| Flow + Trend aligned | Same direction | +12 |
| Momentum + Trend aligned | Same direction | +8 |
| Flow contradicts Trend | Opposite directions | −min(abs(flow), 20) |
| Momentum diverges from Trend | Opposite directions | −min(abs(momentum), 12) |
| Extreme Funding + Trend aligned | \|FR\| ≥ 0.02 + same direction | +15 |
| SuperTrend flip + Volume | Both same direction | +10 |
| RSI Divergence + MACD cross | Both same direction | +12 |
| Bollinger squeeze breakout + Volume | Breakout + volume spike | +10 |
| BTC Hash Ribbon + Trend | Both same direction | +14 |
| BTC Profitability extreme + Halving | Extreme profit + mid/early phase | +10 |

---

## Multi-Group Confluence Multiplier (Applied Last)

After all combos, count how many of the 5 groups have net positive (bullish) score.

| Groups Aligned | Conflicts | Multiplier | Label |
|---|---|---|---|
| 5/5 | 0 | ×1.30 | Penta confluence |
| 4/5 | 0 | ×1.30 | Quad confluence |
| 3/5 | ≤ 1 | ×1.15 | Triple confluence |
| 2/5 | 0 | ×1.08 | Double confluence |
| Any | ≥ 2 conflicts | ×0.82 | Conflicted — reduce confidence |
| Other | — | ×1.00 | No adjustment |

---

## Flipped Indicators

Tracked separately from the score — these are *fresh directional changes*, not sustained states.
Shown in the dashboard as "⚡ N indicators just flipped direction."

Tracked: MACD line cross, MACD zero cross, EMA 7/21 cross, SuperTrend flip,
VWAP cross, Stochastic RSI cross from oversold/overbought, Ichimoku TK cross.

---

## Data Sources by Metric

| Metric | Primary | Fallback |
|---|---|---|
| Candles / Price | OKX | Bybit → KuCoin → Gate.io → MEXC → Kraken → LBank |
| Funding Rate | CoinGlass | Binance |
| Open Interest | CoinGlass | Binance |
| Liquidations | CoinGlass | Binance |
| Futures CVD | CoinGlass (real taker vol) | Candle close/open estimate |
| Spot CVD | Candle estimate | — |
| Long/Short Ratio | Binance | — |
| Fear & Greed | alternative.me | — |
| News Sentiment | LunarCrush | RSS (CoinDesk + Cointelegraph) |
| Options Expiry | Deribit (live) | Calendar-only |
| MVRV / Profitability | CoinMetrics | — |
| Hash Ribbon / Difficulty | mempool.space + blockchain.info | — |
| Market Cap | CoinGecko | — |
