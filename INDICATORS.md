# CryptoMonk — Indicator Reference

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

### RSI (14) — the value itself

Wilder's smoothing (RMA), which is exactly what TradingView's RSI uses, so the
two agree. The seed is a simple mean of the first 14 gains and losses; every bar
after that is `avg = (avg × 13 + new) / 14`. Verified against an exact-decimal
Wilder reference to within **0.01**, and against the classic SMA-of-gains
variant to prove it is *not* that — the two agree at the seed and drift after,
so a test checking only the first value would miss the difference.

Wilder's smoothing has unbounded memory, so a short warm-up gives a slightly
different answer than a long one. The app fetches **240 candles** on 1H–1D,
where the drift against a 3000-bar seed measures **0.000**. At 60 bars it is
still only ~0.06.

The panel's 70/30 threshold lines are **price lines**, not data series. Drawn as
series they carried a time coordinate — anchored 2.85 years in the past — which
stretched the chart's time domain back to 2023 and made `fitContent()` frame
three years to show thirty days.

**When ours and TradingView disagree, check the timeframe first.** Daily and
weekly RSI are different numbers about different things. Note also that
TradingView's RSI pane prints *two* values — the RSI and its own moving average
— and the second is not an RSI.

### RSI Divergence · `momentum`
*14-candle window. Divergence between price and RSI.*

**Freshness.** A divergence is not a permanent fact about the chart — it called a
turn on a particular candle. It used to score the same on candle 1 as on candle
29 and then vanish outright when its pivots fell out of the lookback: full
weight, full weight, nothing.

| `status` | When | Weight |
|---|---|---|
| `forming` | second pivot still provisional | as before (8 pts) |
| `confirmed` | ≤ `FRESH_BARS` (12) closed candles since the second pivot | full |
| `expired` | past the window, within 3 more candles | linear fade to zero |
| *(dropped)* | beyond that | not returned at all |

`age_candles`, `fresh_bars` and `freshness` come back with the verdict, and
`signals.py` multiplies the divergence points by `freshness` — never rounding a
still-counting signal away to nothing. A missing `freshness` is treated as 1.0,
so an analysis built before this existed is not silently zeroed.

The 3-candle grace is the same window flags and wedges already use
(`patterns.FAILURE_SHOW_BARS`), so a setup that expired reads as expired rather
than disappearing. The dashboard draws the three states distinctly.

The detector locates two swing pivots to reach its verdict, and now **returns
them** as `points` — `{kind, prev:{timestamp, price, rsi}, curr:{…}}` — so the
dashboard can draw the claim instead of only asserting it: price pivots on one
axis, RSI pivots on another, joined by lines sloping opposite ways.

The dashboard draws them in a dedicated full-width panel (`#divPanelSection`),
not in the metric card — at card size the prices are unreadable, which is the
whole reason for showing it. Both pivots carry their real price and RSI value,
each is dated, and the price and RSI axes are labelled, so the chart reads as
numbers rather than only as a shape. The section is hidden entirely when there
is no divergence: an empty frame reads as *no data* when the truth is *no
divergence*.

`points` is `null` when no divergence was found, and also when the candle feed
carries no timestamps. Timestamps are needed to *draw*, never to *detect*, so a
feed without them still detects normally and the card simply shows its text with
no picture.

| Condition | Points |
|---|---|
| Bullish divergence, strength ≥ 5 | +18 |
| Bullish divergence, strength < 5 | +12 |
| Bearish divergence, strength ≥ 5 | −18 |
| Bearish divergence, strength < 5 | −12 |

**Forming (unconfirmed) divergences.** A pivot needs `pivot_window` closed
candles on *both* sides, so a fresh second low is invisible for that many closes
— on the weekly, that is weeks of lag on exactly the charts where a divergence is
called early. When no confirmed divergence exists, the last few candles are
checked as a *provisional* second pivot and reported with `forming: true`.

Such a signal carries `closes_to_confirm` — how many more closes must hold before
the pivot is real — and it **counts down**: some of the required closes have
usually already printed, so the wait shrinks with every candle. It previously
quoted the full window flat, which meant a divergence spotted yesterday still
claimed the same number of closes to go today. That read as a live countdown
while being a constant, and invited waiting for a confirmation that had already
happened. `pivot_window` is 2 on weekly and above, 3 below.

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

### Market-Structure Confluence · `pattern`
*Applied to **strength**, after direction is settled. Never changes direction —
resting stops below a LONG are a reason to size down or wait, not to go short.*

These three reads are also what the Market Structure panel displays; the panel
and the score share `average_true_range()` and `structure_range()` so the
numbers on screen are the numbers being scored.

**1. Stop-run risk** — a liquidity pool sitting *against* the trade within
`0.35 ATR`. That is where the stops of everyone already positioned rest, and
price tends to take them first. Needs ≥ 2 touches; a single touch is not a pool.

Read from **`liquidity_pools`** — the full clustered ladder — falling back to the
single `equal_levels` pair only when the ladder is absent. `equal_levels` holds
one level per side, and on a live BTC 2H chart that single equal-high was a level
price had already traded through, while the ladder held a 7-touch and a 4-touch
cluster 0.18–0.19 ATR overhead that scored nothing.

**Swept pools do not score** — see *Swept liquidity* below. The fallback to
`equal_levels` fires only when the ladder **key is absent** (or null), never
when it is present — including present and empty. `equal_levels` carries no
sweep flag, so falling back there would readmit a spent level unflagged.

**Only the nearest qualifying pool scores.** Two clusters a few points apart are
one zone in practice; a penalty per level would double-count it.

```
closeness  = 1 − (distance_atr / 0.35)          # 1.0 when at price
conviction = min(1.0, touches / 5)              # 5+ touches = full weight
points     = −round(10 × max(closeness, 0.35) × conviction)
```

| Pool distance | Touches | Points |
|---|---|---|
| 0.05 ATR | 5 | −9 |
| 0.30 ATR | 3 | −2 |
| > 0.35 ATR | any | 0 |

**Age discount.** Resting stops are not permanent — orders get filled, cancelled
or moved — so the penalty is scaled by how long ago the level was last touched:

```
freshness = max(0.35, 1 − bars_ago / 40)     # 20 bars = exactly half weight
points    = round(base × freshness)
```

The floor is deliberately **non-zero**: a level defended eight times is still a
level even when last defended a while back. Staleness discounts the claim *"stops
are resting here"*, it does not erase the price. A pool with no `last_ts` scores
as **fresh** — absence of age information must not read as staleness.

On the reported BTC 2H SHORT the penalising pool was **29 bars old**, which took
it from −4 to −1.

For a LONG the threatening pool is the equal-**low** cluster at or below price;
for a SHORT, the equal-**high** cluster at or above.

**2. Range chase** — entering where the move has already happened, measured on
the same 30-bar window the panel reports.

| Condition | Points |
|---|---|
| LONG, range position ≥ 80% | −1 to −8, scaling to the extreme |
| SHORT, range position ≤ 20% | −1 to −8, scaling to the extreme |
| Anything between | 0 |

A LONG at the range *low* (or a SHORT at the range high) is the opposite of
chasing and is not penalised.

**3. BOS persistence** — structure repeatedly taken out, and still holding.

```
freshness = max(0, 1 − bars_ago / 10)      # same decay window as CHoCH
points    = round(base × freshness)
```

| Condition | Points |
|---|---|
| BOS aligned with direction, `held` | +3 per break, max **+8**, × freshness |
| BOS opposing direction, `held` | −3 per break, max **−6**, × freshness |
| BOS `given back` (level lost again) | **0** — stale context, not a live read |
| BOS ≥ 10 bars old | **0** — recorded as `bos_stale`, not scored |

A break nine bars back is not the same evidence as one on the last candle.
Without the decay a stale break moved conviction forever; a 1× break 9 bars ago
went from −3 to 0.

**Total clamp:** `[−18, +8]`. Asymmetric on purpose — risk should be able to cut
conviction harder than confirmation can inflate it.

---

### Liquidity-Aware Stop Placement · *risk, not scoring*
*Changes where the stop goes, not the strength number.*

A stop sitting just short of a liquidity pool is in the worst possible place:
price runs the pool, takes the stop, then reverses — stopped out by the exact
move the trade was positioned for. On a live BTC 2H SHORT the stop landed at
64922.05 with an 8-touch pool at 64941.62, twenty points above it.

After the normal SL anchors and the hard cap, `clear_stop_of_liquidity` looks for
a pool on the trade's risk side sitting **within `0.25 ATR` beyond** the stop, and
moves the stop past it with `0.10 ATR` (min 0.15%) of clearance.

| Rule | Behaviour |
|---|---|
| Never tightens | Returned distance is always ≥ the one passed in |
| Respects the hard cap | If clearing exceeds `_max_sl_abs`, the stop is **unchanged** and flagged `blocked` |
| Blocked → size decision | Emits *"reduce size or wait"* rather than silently leaving the stop in the sweep path |
| Only the risk side | Below entry for a LONG, above for a SHORT |
| Only pools just beyond | A distant pool is not what takes the stop out |
| Needs 3+ touches | Stricter than the 2 required merely to dock conviction — widening real risk demands a better-defended level |
| Needs freshness ≥ 0.5 | A **stale** pool cannot widen a stop at all (≈ touched within 20 bars). Spending real risk needs a live claim that a sweep is coming — stricter than the 0.35 scoring floor. |
| Must be unswept | A pool whose stops have already been taken cannot widen a stop — the sweep it would protect against has happened. See *Swept liquidity* |

Exposed on the signal as `stop_liquidity`
(`{sl_dist, moved, pool_price, touches, blocked, note}`), with the note added to
the reason list **opposing** the trade, because a wider stop is a cost.

**Note on R/R:** widening a stop lowers reward-per-risk, so some candidates will
now fall below the recommendation engine's `R/R ≥ 1.3` gate. On the reported case
R/R moved 1.53 → 1.30. That is the honest number for a stop placed out of the
sweep zone, not a regression.

---

### Liquidity Pools as TP Anchors · *targets, not scoring*
*Changes where profit is taken, not the strength number.*

A pool ahead of the trade is where resting orders sit, so price is drawn to it.
TP snapping already anchored to supply/demand zones, trend-lines, swings and the
macro line — but ignored pools, so the ladder could target an ATR projection
while a real wall of liquidity sat closer.

Pools ahead of the trade now join the candidate walls in `_snap_tp_to_structure`,
which keeps all its existing rules: the nearest wall clearing the R gate becomes
TP2, and every wall is **front-run by ~3%** so the order fills just *before* the
level rather than inside the fight over it.

| Rule | Behaviour |
|---|---|
| Direction | Only pools ahead of entry — above for a LONG, below for a SHORT |
| Minimum touches | **2** — looser than the 3 required to move a stop |
| Must be unswept | A spent pool has no resting orders left to draw price, so it is not a target. See *Swept liquidity* |
| Front-running | Inherited: TP fills before the pool, never inside it |
| Labelling | When the chosen wall *is* a pool, the reason says `"N-touch liquidity pool"` instead of `"zone / line"` |

**Why 2 touches here but 3 for stops.** Anchoring to a weak pool that price blows
through only takes profit slightly early. *Ignoring* a real pool leaves the TP
beyond it, where it may never fill. Under-shooting is the cheaper error, so the
gate is looser.

Exposed as `tp_anchor` (`{wall, r_multiple, kind, touches}`) where `kind` is
`liquidity_pool` or `zone_or_line`.

On the reported BTC 2H SHORT this moved **TP3** from an ATR extension (62505.61)
onto the real 3-touch pool at 62593.67. TP1/TP2 were unchanged there because an
existing level already sat nearer — the effect shows up wherever pools are the
closest structure.

Exposed on the signal as `structure_adjustment` (signed delta) and
`structure_factors` (per-factor breakdown), and the individual reasons appear in
the normal bullish/bearish factor lists.

---

### Swept Liquidity · *risk, targets and scoring*
*Changes where stops go, where targets go, and the strength number.*

A liquidity pool is resting stop orders. Once price has traded through it those
orders are filled: the level stops pulling price toward it, and there is nothing
left there to run a stop out. The `swept` flag has recorded this since it was
added — but only the chart read it. Every scoring path went on treating a spent
pool as loaded, so the system asserted two contradictory things about one level
at the same time: greyed out on screen, fully weighted in the maths.

**The sweep boundary is the zone edge, not the mean.** A pool is a cluster of
pivots, and `price` is their average — a number no order rests at. Pivots at
105.0 and 105.4 average to 105.2, but the stops behind those equal highs rest
above **105.4**, and a wick to 105.3 has taken none of them. Pools now carry
`zone_low`, `zone_high` and `sweep_level` (the high edge for a high pool, the
low edge for a low pool), and a sweep must clear `sweep_level`:

| Pool kind | Swept when a later closed candle |
|---|---|
| `high` | high **>** `zone_high` |
| `low` | low **<** `zone_low` |

`price` is unchanged and still what the chart labels and the reasons quote.

**Every consumer now skips a swept pool**, via one helper,
`signals.is_live_liquidity_pool(pool)`:

| Consumer | Effect of the exclusion |
|---|---|
| `clear_stop_of_liquidity` | Stop is **not widened** past a spent pool |
| `_tp_pool_levels` | A spent pool is not a TP wall |
| `_matching_pool` | A wall at a spent pool's price is not labelled as one |
| `_nearest_threatening_pool` | Stop-run risk does not dock conviction for it |

Skipping is per-pool, not early exit: a live pool sitting *behind* a swept one
is still found, and is usually the real risk.

**A missing `swept` field means live**, and so does a null one. Payloads
written before the flag existed must not have liquidity logic switched off
wholesale.

**The flag is normalised, never read by truthiness.** `bool("false")` is
`True`, and so is `bool("0")` — a pool arriving from JSON text, a cache, or
anything that stringified its booleans would read as swept and be dropped from
scoring while every display still called it intact.

| `swept` value | Read as |
|---|---|
| absent, `None` | live |
| `False`, `0`, `"false"`, `"0"`, `"no"`, `"off"`, `""` | live |
| `True`, `1`, `"true"`, `"1"`, `"yes"`, `"on"` | swept |
| anything else — `"maybe"`, `2`, `NaN`, lists, dicts | **swept** |

Strings are matched case-insensitively after stripping whitespace. Unreadable
values fall on the *swept* side deliberately: the costs are asymmetric. Skipping
a pool that was really live costs a little conviction; widening a real stop past
a pool that was really spent costs permanent risk on every trade through that
level.

**Freshness stays a separate axis.** An old pool may still be loaded; a swept
pool is empty however recently it was touched. `pool_freshness` answers "how
long ago", this answers "is anything left" — conflating them would discount a
live level twice.

**The `equal_levels` fallback narrowed.** `equal_levels` carries one level per
side and no sweep flag at all, so falling back to it after discarding a ladder
readmits the very level just thrown out, unflagged. It now fires **only when
the `liquidity_pools` key is absent, or explicitly null** — a payload from
before the detector existed, or one where it never ran.

| Ladder | Fallback |
|---|---|
| key absent | yes — legacy payload |
| `None` | yes — the detector never ran |
| `[]` | **no** — the detector ran and found nothing |
| present, nothing qualifies (swept, stale, malformed, safe side) | **no** |

Presence is tested by **key**, never by truthiness, because `[]` and a missing
key are both falsy and mean opposite things. An empty ladder is a current,
authoritative result: *"all the stops around here have been taken"* is an
answer, not a reason to ask a weaker source the same question.

The stop-placement case is the costly one: widening a stop spends permanent,
unrecoverable risk on the claim that a sweep is coming. If the sweep already
happened, the claim is false and the extra risk buys nothing on every trade
through that level.

`STRATEGY_VERSION` moved to **v45_4h_avg** — this changes stops, targets and
strength, so v44 rows are not comparable with v45 rows.

---

### Liquidation max-pain & TAO chain-buys nudges · *scoring (v46)*
*Two reads that were reporting-only now move strength — small, capped, advisory.*

Both sit beside the market-structure confluence, applied **after** direction is
settled because both are direction-relative: they move **strength**, never the
direction.

* **Liquidation max-pain squeeze bias** (all symbols with a derivatives feed).
  `_liquidation_bias` reads realized liquidations + the OI-squeeze quadrant and
  points at the side price would move to inflict the most forced-position pain.
  A trade **aligned** with that lean is confirmed, one **fighting** it is docked
  — **±4** strength points when the bias is *strong*, **±2** on a *lean*, 0 when
  balanced/absent. It corroborates; it can never manufacture a signal. The
  signed delta is stored on the snapshot, so the postmortem's new
  `fought_the_liquidation_squeeze` discriminator can later measure whether
  opposing the squeeze actually cost anything.
* **TAO chain-buy momentum** (TAO only). dTAO AMM buy volume is TAO actually
  spent acquiring subnet Alpha — the on-chain demand print. A day running ≥1.5×
  the trailing 7d pace adds +3 (accumulation accelerating); ≤0.5× subtracts 3
  (buying drying up). It folds into the existing TAO ecosystem `signal_pts`
  (±12 cap), corroborating the net-flow read rather than competing with it.

Neither can be replayed from history (liquidations and chain-buys are not stored
per bar), so both stay **dormant in the price-only backtest** — the reason they
were reporting-only until a human opted them in. `STRATEGY_VERSION` moved to
**v46_4h_avg**; v45 rows are not comparable, and the postmortem cohort restarts.

---

### Expired Setups · *publication gate, not scoring*
*Removes a recommendation entirely; never changes a strength number.*

The entry/SL/TP ladder is priced off the last **closed** 2H candle, but a
recommendation is served for the whole slot. If price trades through TP1 inside
that window, the published R/R describes a trade that no longer exists: the
reward has already been collected and only the risk is still in front of anyone
entering now.

Three live cases that shipped before this gate existed:

| Symbol | Entry | Live | TP1 | R/R published → at live |
|---|---|---|---|---|
| TRX | 0.32711979 | 0.32864 | 0.32852649 — **taken** | 1.36 → **0.50** |
| XMR | 353.72105263 | 358.04000 | **taken** | 2.04 → 1.36 |
| BNB | — | 580.741 | 575.224 / 576.766 / 579.944 — **all taken** | no target left |

TRX is the sharpest: it cleared the `R/R ≥ 1.3` gate at 1.36 and was served at an
effective 0.50 — worse than the gate exists to prevent. BNB had nowhere left to
go at all.

`_targets_behind_live(direction, tp_targets, live_price)` marks a rung **spent**
once price has reached it — `≤ live` for a LONG, `≥ live` for a SHORT, *at* the
level included. A candidate whose **TP1** is spent is dropped after the R/R gate.

| Rule | Behaviour |
|---|---|
| Drop, don't reprice | The ladder is not rebuilt from the live price. A setup the market already ran is not a setup, and repricing would invent levels no analysis chose. |
| TP1 only decides | A spent TP2/TP3 does not drop the trade — TP1 is what makes it worth taking. |
| Reaching counts as taken | `lvl == live` is spent. Conservative on purpose. |
| Missing data never expires a trade | No live price, or no priced ladder, means *not evaluated* — absence of a live price is not evidence the targets are gone. |
| Visible, not silent | Dropped candidates appear in the payload as `expired_setups` (`{symbol, direction, reason: "TP1_BEHIND_LIVE", entry, live_price, tp_targets, targets_behind, all_targets_behind, rr_ratio, strength}`). |
| Spent later rungs still reported | A published card carries `targets_behind_live` so a ladder with a dead TP2 doesn't read as fully available. |

This is distinct from the existing `chase_warning`, which compares the **entry**
against the level being broken. That says "you're entering late"; this says "the
target is already gone".

Because the published set now changes as price moves within a slot, the slot cache
key and `STRATEGY_VERSION` both moved to **v42_tpfilter** — signals scored with
this gate are not comparable with those scored without it.

---

### 4H Publication Cadence · *when a set is published, not what scores*
*Decides which bars produce signals; never changes a strength number.*

A recommendation used to be recorded on every **2H** close. The set was
recomputed and re-persisted bar after bar, and because the idempotency key is
per-candle, the same setup that stayed valid across six bars became six rows.
That is how sixty-odd "working" signals accumulated while only a handful of
distinct trades were ever taken.

Publication now happens on the **4H close and nowhere else**:

| | Before (v43) | After (v44) |
|---|---|---|
| Publication bars per day | 12 (every 2H close) | **6** (00, 04, 08, 12, 16, 20) |
| Trades per bar | 3 | 3 |
| Maximum published per day | 36 | **18** |

`_is_publication_bar(close_t)` is the whole gate: a closed candle is a
publication bar when its epoch second is a multiple of `4 × 3600`. 4H boundaries
fall at the same *instants* in UTC and SGT — the offset is a whole multiple of
four hours — so the check needs no timezone argument, and the six SGT slots the
tracker groups by are the same six bars.

| Rule | Behaviour |
|---|---|
| Serve between bars, record nothing | A recompute off a publication bar (a cold start, an on-demand call) still returns the set. It reports `persistence.skipped_reason = "NOT_A_PUBLICATION_BAR"` and stays **actionable** — the set for this bar was already decided and written, so blanking the dashboard for three hours out of four would be wrong. |
| A skip is not an error | `error_code` stays `null` and `persisted` is `0`. `DB_REQUIRED` does not turn a skip into a 503. |
| No candle, no publication | A set with no closed-candle timestamp has no candle identity to de-duplicate on, so it is never treated as a publication point. |
| The slot cache follows the same six bars | `_rec_cache_key()` buckets to the containing 4H boundary, so the served set changes when a 4H candle closes and not in between. |

### 1H/2H Average Ranking · *ordering, not gating*

The composite `quality_score` (R/R, 4H agreement, reversal-against, exhaustion)
used to be the ranking key. It is now the **tiebreak**, and candidates are ranked
by the plain average of **1H and 2H strength**:

```
sort key = (avg_tf_strength, quality_score, h2_strength)   — descending
avg_tf_strength = round((h1_strength + h2_strength) / 2, 1)
```

Both timeframes must already agree on direction for a candidate to exist at all,
so their average measures *how strongly they agree*. Ranking on 2H alone let a
strong 2H with a barely-qualifying 1H outrank a setup both timeframes liked.

What did **not** change: every quality gate still gates. R/R ≥ 1.3, direction
agreement, the data-quality flag, the expired-setup filter above and
correlation-aware diversification all still remove candidates. Demoting
`quality_score` changes the **order** of candidates that already passed, not
whether they pass — but it does mean R/R and reversal risk no longer *weight* the
ordering, only break ties between equally-agreed setups. `quality_score` and
`quality_factors` stay in the payload so the two orderings can be compared.

Both the cadence and the ranking change which trades exist, so `STRATEGY_VERSION`
moved to **v44_4h_avg** — signals from before this are not comparable with
signals from after.

---

### Triangles & Wedges · *detection integrity*
*Changes which patterns are confirmed, and confirmed patterns feed the score.*

Rails are fitted through the recent swing pivots, and a breakout is the first
decisive **close** beyond a rail after the last pivot. That last part had a hole:
after a breakout the breaking candle usually becomes a swing pivot itself, so the
rail was refitted **through** it and the scan window slid past the very bar that
broke. The pattern quietly un-broke itself.

Observed on a live TAO 1D falling wedge: breakout, retest the next day, and days
later the card read *"Forming — awaiting a break above the rail"* again. Same
series in a test harness, before the fix:

```
+1 candle   failed      ← correct
+2 candles  failed
+3 candles  forming     ← the breakout has been erased
+4 candles  forming
```

**A candle cannot be part of the boundary it broke.** Pivots whose candle closed
beyond the structure that preceded them are now dropped before the rails are
fitted (`_peel_breakout_pivots`).

| Rule | Behaviour |
|---|---|
| Which pivots are dropped | Any whose close was beyond rails fitted from the pivots *before* it — newest first, since a breakout pivot stops being the trailing one after a few bars and would otherwise poison the fit from the middle of the set. |
| Bounded | At most 3, and never below `TW_MIN_PIVOTS` per rail. A noisy stretch must not dissolve a valid structure. |
| Clean structures untouched | With nothing to peel the rails are bit-identical to before. |
| Still forming stays forming | The fix must not make every pattern look broken. |

**Both cards show.** A structure that has resolved does not stop the next one
existing, so the detector now emits the invalidated pattern *and* the one price
is building in its place — resolved first, forming after. One card hiding the
other meant either the failure or the new setup went unseen:

```
+1 candle   Falling Wedge  failed (up, closed back below the lower rail)
+2 candles  Falling Wedge  failed
+3 candles  Falling Wedge  failed   ·  Falling Wedge  forming   ← both
+4 candles  Falling Wedge  failed   ·  Falling Wedge  forming
+5 candles  Falling Wedge  forming            (the failure aged out)
```

A failed breakout still disappears once it ages past `FAILURE_SHOW_BARS` (3
candles) — that is unchanged, and it is what ends the pairing above.

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
