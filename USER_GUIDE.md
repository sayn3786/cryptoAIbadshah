# CryptoMonk — Complete User Guide

> **For beginners and experienced traders alike.**
> No jargon. No assumptions. Everything explained from scratch.

---

## Table of Contents

1. [What is CryptoMonk?](#1-what-is-cryptomonk)
2. [The Big Picture — How It Works](#2-the-big-picture--how-it-works)
3. [Getting Around the Dashboard](#3-getting-around-the-dashboard)
4. [Step-by-Step: How to Use It](#4-step-by-step-how-to-use-it)
5. [Understanding the Signal Card](#5-understanding-the-signal-card)
6. [Every Indicator Explained Simply](#6-every-indicator-explained-simply)
7. [The Chart — Trendlines, Zones & Overlays](#7-the-chart--trendlines-zones--overlays)
8. [The Reversal Radar — Spotting Exhaustion & Bottoms](#8-the-reversal-radar--spotting-exhaustion--bottoms)
9. [Higher-Timeframe Context — Why Some Info is Dimmed](#9-higher-timeframe-context--why-some-info-is-dimmed)
10. [Token-Specific Dashboards (BTC · TAO · GoMining)](#10-token-specific-dashboards-btc--tao--gomining)
11. [The Recommendation Cards](#11-the-recommendation-cards)
12. [Risk Management — The Most Important Section](#12-risk-management--the-most-important-section)
13. [Common Beginner Mistakes](#13-common-beginner-mistakes)
14. [Glossary](#14-glossary)

---

## 1. What is CryptoMonk?

CryptoMonk is an **AI-powered crypto market analysis dashboard**. It watches 30 cryptocurrencies across multiple exchanges, runs 30+ technical indicators, and gives you a clear **BUY / SELL / WAIT** signal with an exact entry price, stop loss, and profit targets.

**Think of it like this:**
Imagine hiring 5 experienced traders to each look at the same chart, then they vote and tell you their combined conclusion. That is what CryptoMonk does — automatically, in seconds, across all your favourite coins.

**What it is NOT:**
- It is not a trading bot (it does not place trades for you)
- It is not financial advice
- It does not guarantee profits
- It is a tool to help you make better-informed decisions yourself

---

## 2. The Big Picture — How It Works

```
Step 1: Data Collection
  ↓  Price candles from 6+ exchanges (Binance, OKX, Bybit, KuCoin, Gate.io…)
  ↓  Derivatives data (funding rates, open interest, liquidations)
  ↓  On-chain data (exchange flows, mining health, whale activity)
  ↓  Sentiment data (news, Fear & Greed Index, social scores)

Step 2: Indicator Analysis (30+ indicators run simultaneously)
  ↓  Trend indicators  → Is the market going up or down overall?
  ↓  Momentum         → Is the move speeding up or slowing down?
  ↓  Flow             → Are big players buying or selling?
  ↓  Sentiment        → Is the crowd fearful or greedy?
  ↓  Patterns         → Are there chart patterns pointing to a move?

Step 3: Scoring
  ↓  Each indicator adds or removes points (+bullish / −bearish)
  ↓  All points are added together into a final score

Step 4: Signal Output
  ↓  Score ≥ +35  →  LONG  (buy signal)
  ↓  Score ≤ −35  →  SHORT (sell/short signal)
  ↓  Between      →  NEUTRAL (wait, no clear edge)
  ↓  Strength 0–100 shows HOW strong the signal is
```

---

## 3. Getting Around the Dashboard

### Top Bar
- **Asset tabs** — click BTC, ETH, SOL, etc. to switch coins (50 coins, sorted by live market cap). All 50 are fully analysable and chartable. The **recommendation engine scans 31 of them** — the extra majors and mid-caps (LTC, BCH, ETC, UNI, NEAR, FIL, OP, STX, GRT, CRV, LDO, APE, MANA, AXS, BAT, MINA, HNT, ZEN, WLFI) are there to browse, not to be published as trades. That split exists because every scanned coin costs fetches on a publish path already close to its 60-second ceiling.
- **Timeframe tabs** — 1H, 2H, 4H, 8H, 12H, 1D, 1W, 2W, 3W, 1M
- **⟳ Refresh button** — get the latest data
- **🔔 Bell icon** — alerts for weekly engulfing candle patterns

### Timeframe Guide — Which One Should I Use?

| Timeframe | Best For | How Long to Hold |
|---|---|---|
| **1H** | Very active traders, scalping | Hours |
| **2H** | Day trading | Hours to 1 day |
| **4H** | Swing trading | 1–3 days |
| **8H / 12H** | Swing trading | 2–5 days |
| **1D** | Position trading | Days to weeks |
| **1W** | Long-term investing | Weeks to months |

> **Beginner tip:** Start with **1D or 4H**. Lower timeframes are noisier and harder to trade successfully as a beginner.

---

## 4. Step-by-Step: How to Use It

### The Simple 5-Step Process

```
1. Check RECOMMENDED TRADES first (top of page)
   → These are pre-filtered, high-conviction setups

2. Pick a coin and a timeframe

3. Read the SIGNAL CARD
   → Is it LONG, SHORT, or NEUTRAL?
   → What is the strength? (higher = more confident)

4. Check HTF CONFLUENCE
   → Do the bigger timeframes agree?
   → If yes = safer trade. If no = skip it.

5. Set your trade with the given Entry, Stop Loss, and TP targets
   → Never risk more than 1–2% of your account on one trade
```

---

### Detailed Walkthrough (Example: BTC on 4H)

**Scenario:** You open CryptoMonk, select BTC, and choose 4H timeframe.

**You see:**
- Signal: **LONG** | Strength: **68/100** | Tier: **Strong**
- Entry: **$62,450** | Stop Loss: **$60,800** | TP1: **$64,200** | TP2: **$66,500**

**What this means in plain English:**
> "68 out of 100 indicators point upward for BTC on the 4H chart. If you buy at $62,450, place a stop loss at $60,800 (where you accept the trade is wrong and exit), and look to take profit at $64,200 first, then $66,500."

**Then you check HTF Confluence:**
- 8H: LONG ✅ — agrees
- 1D: LONG ✅ — agrees
- 1W: NEUTRAL ⚠️ — not clear

This is a reasonably confirmed trade because 2 higher timeframes agree, even though the weekly is neutral.

---

## 5. Understanding the Signal Card

### Direction

| Label | Meaning |
|---|---|
| 🟢 **LONG** | Indicators say price is likely to go UP — consider buying |
| 🔴 **SHORT** | Indicators say price is likely to go DOWN — consider selling or shorting |
| ⚪ **NEUTRAL** | No clear edge — the safest choice is to WAIT |

### Strength (0–100)

This tells you how many indicators agree and how strongly.

| Strength | Tier | What It Means | Position Size |
|---|---|---|---|
| 0–32 | **Weak** | Only a few indicators agree — high risk, skip or tiny size | 25% of your planned amount |
| 33–50 | **Moderate** | Decent agreement — tradeable but use caution | 50% |
| 51–65 | **Strong** | Good confluence — solid trade setup | 75% |
| 66–80 | **Very Strong** | Most indicators aligned — high confidence | 100% |
| 81–100 | **Extreme** | Rare — nearly everything pointing same direction | 100% + consider adding |

> **Key rule:** NEVER trade a NEUTRAL signal. NEVER go full size on a Weak signal. Strength below 33 = WAIT.

### Entry, Stop Loss, Take Profit

- **Entry** — the price to buy (LONG) or sell (SHORT)
- **Stop Loss (SL)** — the price where you EXIT if the trade goes wrong. This is not optional — always set it.
- **TP1, TP2, TP3** — three price targets to take profit. Take some profit at TP1, leave rest for TP2, etc.
- **R/R Ratio** — Risk/Reward. "3.2" means you risk $1 to potentially make $3.20. Aim for at least 2.0+.

### Leverage (for futures traders only)

The dashboard suggests leverage based on market volatility. If it says **3×**, use 3× maximum. Using more than suggested dramatically increases your chance of liquidation (losing everything).

> **Beginner warning:** If you are new, trade **spot only** (no leverage). Leverage can wipe out your account very quickly. Learn the market first.

### Extra warnings on the Signal card

- **⚠ Overbought/oversold at this TF** — the signal direction is fighting a momentum extreme; the entry is overextended, wait for a pullback
- **⚡ N indicators just flipped** — fresh directional flips (MACD cross, SuperTrend flip, EMA cross…) — the earliest entry evidence
- **🛑/🟢 Reversal Radar** — exhaustion/bottoming gauge for the current trend (full explanation in [Section 8](#8-the-reversal-radar--spotting-exhaustion--bottoms))
- **Smart Money Structure** — CHoCH, Liquidity Grab and the ICT triple-combo setup when present

### Why is it NEUTRAL when so many things look bullish?

The confluence list can show many bullish lines yet the signal stays NEUTRAL. That's usually correct, because:
- Many lines are **low-weight** (news, sentiment) or **down-weighted on your timeframe** (🗓️ daily+ context)
- Strong structural signals on the other side (trend, real-money flow) cancel them
- If only ONE group of indicators leans a direction (e.g. momentum alone, without trend or flow), the score is **damped 15%** — "⚠️ Unconfirmed signal" — so a lone momentum spike can't print a LONG
- The bar is deliberately high: a signal needs the equivalent of 2–3 real indicators agreeing (score ±35) before it calls a side. NEUTRAL means "no edge worth risking money on."

---

## 6. Every Indicator Explained Simply

### TREND Indicators — "Which way is the wind blowing?"

---

#### RSI (Relative Strength Index)
**What it is:** A 0–100 meter that shows if a coin is overbought or oversold.

**Simple rule:**
- RSI below 30 = coin is very beaten down → potential bounce UP
- RSI above 70 = coin is very stretched up → potential pullback DOWN
- RSI 30–70 = no clear extreme, wait for other signals

**How CryptoMonk uses it:** Only extreme readings score points. Middle readings are ignored because they're too noisy.

---

#### EMA (Exponential Moving Average) — 9, 20, 50, 200
**What it is:** A line that follows the price trend. Think of it like a moving average of recent prices, with more weight on recent candles.

**Simple rule:**
- Price ABOVE the EMA line = uptrend
- Price BELOW the EMA line = downtrend
- Short EMA (9) crossing above long EMA (200) = strong bullish signal ("golden cross")
- Short EMA (9) crossing below long EMA (200) = strong bearish signal ("death cross")

**How to think about it:** The 4 EMA lines are like 4 different "trend judges" looking at different time windows (9=short-term, 200=long-term).

---

#### SuperTrend
**What it is:** A line that switches between green (below price = uptrend) and red (above price = downtrend) based on price volatility.

**Simple rule:**
- Green line below price = BUY zone
- Red line above price = SELL zone
- Line just flipped green → new uptrend just started

---

#### Ichimoku Cloud
**What it is:** A Japanese system that shows support, resistance, trend direction, and momentum all at once using a coloured "cloud."

**Simple rule:**
- Price ABOVE the cloud = bullish (uptrend)
- Price BELOW the cloud = bearish (downtrend)
- Price INSIDE the cloud = confused / risky zone, avoid trading

---

### MOMENTUM Indicators — "Is the move speeding up or slowing down?"

---

#### MACD (Moving Average Convergence Divergence)
**What it is:** Shows the difference between two moving averages. When they cross, it signals a shift in momentum.

**Simple rule:**
- MACD line crosses ABOVE signal line = bullish momentum building
- MACD line crosses BELOW signal line = bearish momentum building
- Histogram bars getting bigger = momentum accelerating
- Histogram bars getting smaller = momentum fading (caution)

---

#### Bollinger Bands
**What it is:** Three lines around price — a middle average and two outer bands that expand/contract based on volatility.

**Simple rule:**
- Price hits the LOWER band = potentially oversold, possible bounce up
- Price hits the UPPER band = potentially overbought, possible pullback
- Bands squeezing VERY TIGHT = big move coming (direction unknown yet)
- Bands very wide = already in a big move, late to enter

---

#### Stochastic RSI
**What it is:** A more sensitive version of RSI. Oscillates between 0 and 100 very quickly.

**Simple rule:**
- Below 20 = oversold (looking for bounce)
- Above 80 = overbought (looking for pullback)
- %K line crosses above %D line in oversold zone = buy signal

---

#### RSI Divergence — all FOUR types
**What it is:** When price and RSI disagree. The direction of the disagreement tells you whether a trend is about to REVERSE or about to CONTINUE.

**The four types:**

| Type | Price | RSI | Meaning |
|---|---|---|---|
| **Bullish (regular)** | Lower Low | Higher Low | Selloff losing force → reversal UP |
| **Bearish (regular)** | Higher High | Lower High | Rally losing force → reversal DOWN |
| **Hidden Bullish** | Higher Low | Lower Low | Uptrend CONTINUES — buy-the-dip signal |
| **Hidden Bearish** | Lower High | Higher High | Downtrend CONTINUES — sell-the-bounce signal |

**Key insight:** Regular divergence calls a *reversal*; hidden divergence confirms the *trend continuing*. Two analysts can look at the same chart and one says "bullish divergence" (reading the lows) while another says "hidden bearish" (reading the highs) — both can be technically present. In a downtrend, the trend-aligned hidden bearish is usually the higher-probability read. CryptoMonk detects all four, reports the one on the most recent swing, and warns you when an opposing divergence coexists ("mixed structure — wait for confirmation").

**⏳ Forming divergence:** a divergence needs a confirmed swing point, which takes a few closed candles — on the WEEKLY that can mean weeks of lag. When the newest candles show a divergence shaping up but not yet confirmed, the card shows it as **"forming ⏳"** with reduced weight. This is exactly when analysts on X call it early — you see it too, honestly labelled as unconfirmed.

---

### FLOW Indicators — "Where is the big money going?"

---

#### CVD (Cumulative Volume Delta)
**What it is:** Tracks whether buyers or sellers are more aggressive. Every trade is either a "buy" (market buyer hit the ask) or "sell" (market seller hit the bid). CVD accumulates this difference.

**Simple rule:**
- CVD trending UP while price is flat or down = hidden buying → bullish
- CVD trending DOWN while price is flat or up = hidden selling → bearish
- CVD and price moving together = healthy trend, normal

**Spot CVD vs Futures CVD:**
- **Spot** = people actually buying/selling the real coin
- **Futures** = traders betting on price with leverage
- If both agree = strong signal. If they diverge = caution.

---

#### Order Book — Bid/Ask Walls
**What it is:** The real-time list of all buy and sell orders sitting at different prices. CryptoMonk aggregates this from 6 exchanges simultaneously (like Bookmap).

**Simple rule:**
- Large **BID wall** (buy orders) below current price = strong support — price unlikely to fall through it easily
- Large **ASK wall** (sell orders) above current price = strong resistance — price unlikely to break above it easily
- More bids than asks = buyers in control → bullish
- More asks than bids = sellers in control → bearish

---

#### Funding Rate
**What it is:** In futures/perpetual swap markets, traders who are "long" pay traders who are "short" (or vice versa) every 8 hours. This fee is the funding rate.

**Simple rule:**
- **Positive funding** = longs are paying shorts = too many people betting on price going up → contrarian warning, possible pullback
- **Negative funding** = shorts are paying longs = too many people betting on price going down → contrarian signal, possible bounce
- Near-zero funding = balanced, healthy

---

#### Open Interest (OI)
**What it is:** The total value of all open futures contracts. Rising OI = new money entering the market. Falling OI = traders closing positions.

**Simple rule:**
- Price UP + OI UP = fresh longs entering = genuine uptrend (bullish)
- Price UP + OI DOWN = shorts being forced to close (short squeeze) = move may not last
- Price DOWN + OI UP = fresh shorts entering = genuine downtrend (bearish)
- Price DOWN + OI DOWN = longs giving up = may be nearing a bottom

---

#### Long/Short Ratio
**What it is:** The percentage of traders who are long (betting up) vs short (betting down), averaged across Binance, Bybit, and OKX.

**Why it's used as a CONTRARIAN signal:**
Most retail traders are wrong at extremes. When EVERYONE is long, there is nobody left to buy and push price up further. The market tends to punish the crowded side.

**Simple rule:**
- 70%+ traders long = dangerous, market loves to dump and trap them
- 70%+ traders short = dangerous, market loves to squeeze them up
- Near 50/50 = no clear crowding

---

#### Exchange Netflow (BTC and ETH only)
**What it is:** Tracks how many coins are moving INTO vs OUT OF exchanges on the blockchain.

**Why it matters:** To sell coins, you first have to send them to an exchange. So large inflows to exchanges often precede selling.

**Simple rule:**
- Large **INFLOW** to exchanges → whales are depositing to sell → bearish
- Large **OUTFLOW** from exchanges → people withdrawing to hold (self-custody) → bullish (they are not planning to sell)

---

### SENTIMENT Indicators — "What is the crowd feeling?"

---

#### Fear & Greed Index (0–100)
**What it is:** A daily score measuring overall crypto market sentiment from multiple sources (volatility, volume, social media, surveys).

**Simple rule:**
- 0–24 = **Extreme Fear** → market is panicking → historically good time to BUY carefully
- 25–49 = **Fear** → cautious market → potential buying opportunity
- 50 = **Neutral**
- 51–74 = **Greed** → market feeling confident → start being careful
- 75–100 = **Extreme Greed** → everyone is euphoric → historically good time to SELL or avoid buying

> Famous quote by Warren Buffett: *"Be fearful when others are greedy, and greedy when others are fearful."*

---

#### News Sentiment
**What it is:** The dashboard reads recent crypto news headlines and scores them as bullish, bearish, or neutral. Combines LunarCrush social sentiment with RSS feeds from CoinDesk and Cointelegraph.

**Simple rule:**
- Mostly positive news → small bullish boost
- Mostly negative news → small bearish signal
- This is a weak signal alone but adds context

---

### PATTERN Indicators — "What shapes do I see in the chart?"

---

#### FVG (Fair Value Gap) — ICT Concept
**What it is:** A price gap created by a fast move where the market skipped over a range of prices without trading there. Price tends to come back and "fill" these gaps.

**Imagine this:**
Price jumped from $50,000 to $52,000 so fast that it never traded between $50,500 and $51,500. That $1,000 range is an FVG. Price will often retrace back to that zone later.

**Simple rule:**
- **Bullish FVG** below current price = a support zone the market should come back to fill → look to buy near it
- **Bearish FVG** above current price = a resistance zone → market may bounce down from it
- The dashboard shows only **unfilled** FVGs (filled ones are removed automatically)

---

#### BAG (Break Away Gap)
**What it is:** Like an FVG but bigger and more explosive — the middle candle that created the gap was at least 2.5× the average candle size. This is a very strong signal that a breakout occurred.

**Simple rule:**
- BAG = stronger than FVG → worth more points in the signal score
- BAG zones are stronger magnets — price will usually fill them
- BAGs are shown with a thicker line and highlighted differently in the table

---

#### Engulfing Candle
**What it is:** When one candle completely "swallows" the body of the previous candle. This signals a shift in who is in control.

**Simple rule:**
- Green candle engulfs a red candle = **bullish engulfing** → buyers just took over from sellers
- Red candle engulfs a green candle = **bearish engulfing** → sellers just took over from buyers
- CryptoMonk only checks the **most recent closed candle** (so the signal is fresh, not stale)

---

#### CHoCH (Change of Character)
**What it is:** An ICT/Smart Money concept. In an uptrend, price makes Higher Highs and Higher Lows. A CHoCH is when price breaks below a previous Higher Low — signalling the trend has changed character.

**Simple rule:**
- Bullish CHoCH = downtrend breaking above a previous high → possible trend reversal UP
- Bearish CHoCH = uptrend breaking below a previous low → possible trend reversal DOWN

---

#### Liquidity Grab
**What it is:** When price briefly spikes below a support level (to "grab" the stop losses sitting there) then immediately reverses. This is smart money triggering retail stop losses before moving the other direction.

**Simple rule:**
- Price dips below recent lows, wicks down, then quickly recovers → bullish liquidity grab
- Price spikes above recent highs, wicks up, then quickly drops → bearish liquidity grab
- These are potential reversal entry points

---

#### Elliott Wave
**What it is:** A theory that markets move in repeating wave patterns — 5 waves with the trend, then 3 waves against it. Helps identify where in the cycle price currently is.

**Simple rule:**
- Wave 1, 3, 5 = impulse waves (go WITH the main trend)
- Wave 2, 4 = correction waves (pullbacks, potential entry points)
- Wave 3 is usually the longest and strongest — the best wave to catch

---

#### Flag Pattern
**What it is:** After a strong move (the "pole"), price consolidates in a tight range (the "flag") before continuing in the same direction.

**Simple rule:**
- Bull flag (after strong up move) = tight sideways channel → breakout UP expected
- Bear flag (after strong down move) = tight sideways channel → breakdown DOWN expected

---

#### Whale Activity (Volume-Based)
**What it is:** Detects candles where volume was extremely high compared to the average — these are often caused by large institutional buy or sell orders.

**Types:**
- **Bullish whale** = massive buying volume → institutions accumulating
- **Bearish whale** = massive selling volume → institutions distributing
- **Absorption** = large sell into strong buyers (price didn't fall) → very bullish

---

### SPECIAL Indicators

---

#### Options Expiry / Max Pain (BTC only, Deribit)
**What it is:** Every Friday, billions of dollars in Bitcoin options expire on Deribit exchange. "Max Pain" is the price at which the most options contracts lose money — options market makers have incentive to push price toward this level before expiry.

**Simple rule:**
- If current BTC price is BELOW max pain → there is upward "pinning pressure" (bullish this week)
- If current BTC price is ABOVE max pain → there is downward "pinning pressure" (bearish this week)
- Only relevant in the days before Friday expiry — the banner only appears in the "pinning window"

The banner shows:
- `▲ Price pinning UP · signal strength 22/100` = options market is pulling price up this week
- `Put/Call 0.84 — call-heavy (bullish bets)` = more traders betting on price going up

---

#### BTC Mining Health (BTC only)
**What it is:** Tracks the health of Bitcoin miners. Miners are long-term holders who sell BTC to pay electricity bills. When miners are stressed, they sell more, adding to sell pressure.

**Metrics:**
- **Mining difficulty** — how hard it is to mine BTC (higher = miners healthy and competitive)
- **Miner revenue** — how much miners earn (falling = stress, risk of forced selling)
- **MVRV ratio** — compares market value to what all holders paid on average
  - MVRV > 3.5 = market historically overvalued (bull top zone)
  - MVRV < 1 = historically undervalued (excellent long-term buying zone)

---

#### HTF Confluence (Higher Timeframe Alignment)
**What it is:** Checks if the BIGGER timeframes agree with your current signal.

**Why it matters:**
A 1H signal saying LONG but the daily chart saying SHORT = high-risk trade. A 1H signal saying LONG AND the 4H AND 1D saying LONG = much higher confidence.

**How to read it:**
- ✅ = aligned (agrees with your signal)
- ❌ = against (disagrees — warns of danger)
- The more ✅ the better
- "Confirmed" = majority of higher timeframes agree → safer to take the trade

---

## 7. The Chart — Trendlines, Zones & Overlays

The main chart is now full-width and scales to your screen (works on desktop, laptop, and the Android/iPhone app). Every line on it is listed in the **legend** above the chart. Here is what each one means and how to use it.

### The Two Trendlines (drawn automatically, textbook rules)

| Line | Looks like | What it is | What it's for |
|---|---|---|---|
| **Local trendline** | Solid, thick — 🟢 green (support) or 🔴 red (resistance) | The floor/ceiling of the **current leg**, near price | The actionable line: its break is a trade trigger, and it anchors your Entry / Stop Loss / Take Profit |
| **Macro line** | Grey dashed | The **multi-week** floor/ceiling of the whole visible window | The bias filter: while price is under a descending macro ceiling, counter-trend longs are lower-probability |

**How they are drawn (the textbook two-rule method):**
1. Anchor at the **true extreme** (the major low for an ascending support, the major high for a descending resistance)
2. Take the **shallowest slope that keeps all later price action on the correct side** (a support line must stay below the candles, a resistance line above)

**Details that make them trustworthy:**
- Anchors use **candle wicks, clamped** — support sits at the real defended lows, but a single freak spike wick can't hijack the line
- A **break only counts on a candle CLOSE** beyond the line — a wick poking through is a fakeout, not a break
- On a break, the local line **flips colour** to the breakout direction
- Both lines **project ~8 bars into the future** so you can see where price will meet them next
- A macro line that price has decisively broken (moved >5% past) is **hidden** — a "resistance" under price is meaningless
- In choppy sideways ranges you may see **no diagonal lines at all** — that's honest: no valid trendline exists there, and the zones (below) carry the structure

**In the signal:** local line breakout/breakdown = ±14 points; pressing into resistance / holding support = ±8; a *fresh* macro-line break = ±12 (regime change); sitting under the macro ceiling = −5 bias against longs.

### Supply & Demand Zones (the coloured bands)

- 🟥 **Supply Zone** — a band **above** price where sellers have repeatedly defended (clustered swing highs). Expect rejection risk there.
- 🟩 **Demand Zone** — a band **below** price where buyers have repeatedly stepped in (clustered swing lows). Expect bounce attempts there.
- The label shows **"• IN ZONE"** or **"• near"** when price reaches one.
- **In the signal:** inside/approaching a supply zone = −8/−5; inside/approaching a demand zone = +8/+5.
- **In your trade levels:** entries anchor at the near edge of a zone, stops just beyond the far edge (losing the zone = thesis dead), and when the opposite zone offers at least 1.4× your risk, **TP2 is anchored to it** — you'll see "🎯 TP2 anchored to the opposing supply zone / resistance line" in the confluence list. Your plan trades to real structure, not just ATR maths.

### EMA 50 & EMA 200 lines

- **EMA 50** = pink line, **EMA 200** = yellow line, drawn on the chart.
- The 200 EMA is the most-watched dynamic support/resistance in trading.
- **200 EMA retest signal:** in an uptrend, price dipping to the 200 EMA and **closing back above** it = classic trend-continuation BUY (+12). In a downtrend, rallying into it and closing back below = continuation SELL (−12). Wick touches alone don't trigger it — the close decides.
- Note: EMA 200 needs 200 candles of history, so it shows on 1H–1D; on 1W/1M most coins simply don't have 200 weeks/months of data yet.

### Other overlays (quick reference)

- **SuperTrend** — blue while bullish, orange while bearish; a dynamic trailing stop line
- **Ichimoku Span A/B** — purple/cyan dashed; the "cloud" boundaries
- **FVG lines** — only the **3 nearest unfilled gaps within 12% of price** are drawn now (far-away gaps cluttered the chart without being tradeable); strong BAG gaps keep their full band
- **Swing High/Low, Realized Price (BTC), Top band (BTC)** — horizontal reference levels

---

## 8. The Reversal Radar — Spotting Exhaustion & Bottoms

**The problem it solves:** trend-following signals say LONG in uptrends and SHORT in downtrends — but they never tell you when a good market is *exhausted* and about to top, or when a downtrend is *washed out* and about to reverse. The Reversal Radar answers exactly that.

**How it works:** it first determines the trend, then counts **contrarian evidence**:
- In an **UPTREND** it counts **topping signals**: RSI overbought, bearish divergence, extreme greed, overheated funding, price pinned to the upper Bollinger band, Stoch RSI rolling over, distribution volume, crowd extremely long, leverage-only rally (futures-led CVD), on-chain euphoria (SOPR/MVRV/Puell, BTC), price stretched far above the EMA50
- In a **DOWNTREND** it counts the mirror **bottoming signals** (oversold, bullish divergence, extreme fear, negative funding, capitulation volume, crowd short, on-chain capitulation, etc.)

**How to read it (on the Signal card):**

| Level | Meaning | What to do |
|---|---|---|
| **Low** | Trend healthy | Trade with the trend normally |
| **Building** | 2–3 warning signs | Stay alert, tighten management |
| **Elevated** | ~35%+ of checks firing | Trim into strength / avoid fresh entries with the trend |
| **High** | Majority firing | Reversal risk is high — consider standing aside or hunting the reversal |

It shows e.g. **"🛑 TOPPING RISK — 8/14"** with a gauge and the exact list of firing signals. When elevated or high, it also adds a caution line into Signal Confluence.

**On 1H/2H** the slow on-chain/cycle checks are excluded from the count — only intraday-relevant evidence is used (see next section for why).

---

## 9. Higher-Timeframe Context — Why Some Info is Dimmed

Some data simply cannot move a 1-hour candle: ETF flows, macro releases (CPI, Fed), BTC on-chain cycle metrics (SOPR, Puell, MVRV, Hash Ribbon, halving phase), market regime (BTC dominance, stablecoin supply), GoMining tokenomics, TAO ecosystem flows. These describe **days-to-months** behaviour.

**What the app does on 1H and 2H:**
- These signals are **heavily down-weighted** in the score (as low as ×15%), so a "cycle top" reading can't distort your intraday signal
- Their cards get a **🗓️ "daily+ context"** badge and are dimmed
- In Signal Confluence they are moved into a separate dimmed group: **"🗓️ Higher-timeframe context (daily+ · down-weighted)"** — visible as background context, clearly separated from the live intraday drivers

**On 4H and above** they progressively regain full weight (100% from 1D up) — because that's where they belong.

> **Why this matters:** before this, a scary "Miners capitulating!" line could sit at the top of a 1H confluence list, implying it mattered for the next hour. It doesn't. Now what you see at the top of the list is always what's actually driving the current timeframe.

---

## 10. Token-Specific Dashboards (BTC · TAO · GoMining)

### BTC extras
- **Spot ETF Flows** — daily institutional inflow/outflow bars with an as-of date (like "ETF flow" you see on X, live from SoSoValue)
- **BTC Mining / On-chain Health** — Hash Ribbon, Puell Multiple, SOPR, MVRV, Realized Price, halving phase, cycle-top cluster (Pi Cycle / Mayer). All zero/broken-data readings are sanity-guarded so a dead API can never print "STRONG BUY" from a 0.00 value
- **Options Expiry / Max Pain** — weekly pinning pressure banner
- **Open Interest** — now live in USD (OKX primary). If no live source is reachable the value is dimmed and labelled "Estimated" so a fallback can never masquerade as real data

### TAO (Bittensor) — three layers

**1. Bittensor Ecosystem card** — supply staked %, stake in Alphas vs Root, subnet pool flow (24h & 7d), active subnets, Alpha breadth, top-5 emission concentration, and the full 128-subnet emission table.

**2. 🏆 Subnet Flows box** — answers "where did the TAO actually go?":
- Each window (24H / 7D / 30D) leads with the **Σ total** and a momentum chip: today vs **previous 24h** (×ratio, or "flipped +/−"), and today vs the **7d and 30d daily pace**
- The 24H row lists the **top inflow subnets** (and the biggest outflow) — per-subnet figures are unit-calibrated against trusted aggregates, and if the data can't be validated it's dropped rather than shown wrong
- Flow momentum also feeds the signal: *"inflows accelerating (today ×5 the weekly pace)"* = bullish; a flip to outflow against a positive pace = bearish

**3. 💧 TAO Pool Flow — daily** — a separate ETF-style dashboard: green/red bars of net TAO into subnet pools per day (up to 31 days), with Today / 7-day / 30-day comparison tiles. Read it exactly like the BTC ETF flow chart: strings of green bars = sustained staking demand pulling TAO off exchanges.

### GoMining extras
- **Tokenomics card** — supply trend, next Burn & Mint date, **maintenance paid on-chain** (7d total, week-over-week %, 21-day daily bars) — rising maintenance = miners paying more = more burn pressure (even small ±3% WoW moves now score)
- **Large on-chain moves** — transfers ≥1M GOMINING flagged with counterparty labels (burn contract, distribution, whale)
- **Weekly manual log** — app-only figures (mint ratio, total locked, TVL %, veGOMINING APR) that aren't on-chain can be logged weekly on your device, with backfill support for missed weeks
- **Strategy Advisor** — reinvest vs collect BTC, payout currency, and sell radars

---

## 11. The Recommendation Cards

The **Recommended Trades** section at the top of the page automatically finds the best 3 trade setups right now.

### How Recommendations Work

1. The system analyses ALL 30 coins at both 1H and 2H timeframes simultaneously
2. It only keeps coins where **both 1H and 2H agree on direction** (e.g. both say LONG)
3. It adjusts strength based on BTC's direction (if BTC is bullish, correlated alts get a small boost)
4. Options expiry pin pressure is applied
5. The top 3 strongest signals are shown

### Reading a Recommendation Card

```
┌─────────────────────────────────────────────┐
│  ETH   LONG   1H · 2H                       │
│  Strength: 71/100  [Very Strong]             │
│                                              │
│  Entry:  $3,245.00                           │
│  SL:     $3,108.00  (−4.2%)                 │
│  TP1:    $3,420.00  (+5.4%)                 │
│  TP2:    $3,610.00  (+11.2%)                │
│  TP3:    $3,850.00  (+18.6%)                │
│  R/R:    2.8    Leverage: 3×                 │
└─────────────────────────────────────────────┘
```

- **1H · 2H** = both these timeframes confirmed the signal
- **R/R 2.8** = risk $1 to potentially make $2.80
- Recommendations refresh at **8AM, 4PM, and 8PM Singapore time** (SGT)

---

## 12. Risk Management — The Most Important Section

> ⚠️ **This section could save your account. Read it carefully.**

### The Golden Rules

**Rule 1: Never risk more than 1–2% per trade**
If you have $1,000, risk maximum $10–$20 on any single trade.
If you lose 10 trades in a row (which can happen), you are down only $100–$200, not wiped out.

**Rule 2: Always set a Stop Loss before entering**
Decide before entering where you will exit if wrong. Put the stop loss ORDER in immediately after entering. Never "wait and see" if the trade goes against you.

**Rule 3: Don't chase entries**
If the price has already moved significantly past the suggested entry, SKIP the trade. Wait for the next signal. Chasing entries destroys risk/reward ratios.

**Rule 4: Match leverage to your experience**
- Beginners: **spot only** (no leverage)
- Intermediate: maximum 3–5× leverage
- Experienced: up to 10× on small sizes
- Never use 20×, 50×, 100× — you WILL get liquidated eventually

**Rule 5: Don't trade NEUTRAL signals**
When the dashboard says NEUTRAL, it means there is no edge. Trading noise is how accounts get slowly drained.

### Position Sizing by Strength

| Signal Strength | Risk Per Trade | Example ($1,000 account) |
|---|---|---|
| 0–32 (Weak) | 0.5% max | $5 risk |
| 33–50 (Moderate) | 1% | $10 risk |
| 51–65 (Strong) | 1.5% | $15 risk |
| 66–80 (Very Strong) | 2% | $20 risk |
| 81+ (Extreme) | 2% | $20 risk |

### Take Profit Strategy

- At **TP1**: close 40–50% of your position, move stop loss to breakeven
- At **TP2**: close another 30–40%
- Let the remaining 10–20% ride to TP3 or trail the stop loss

---

## 13. Common Beginner Mistakes

### ❌ Mistake 1: Trading every signal
**Wrong:** "It says LONG on 1H, let me buy now!"
**Right:** Wait for at least moderate strength (33+), check HTF confluence, make sure you like the risk/reward.

### ❌ Mistake 2: Ignoring the stop loss
**Wrong:** "I'll just hold through the dip, it'll come back."
**Right:** The stop loss exists because sometimes it does NOT come back. A small planned loss is infinitely better than a catastrophic unplanned one.

### ❌ Mistake 3: Using too much leverage
**Wrong:** "I'll use 50× so I can make 5× profits quickly."
**Right:** A 2% move against you at 50× = liquidated (100% loss). At 3×, a 2% move against you = 6% loss on your position — manageable.

### ❌ Mistake 4: Overtrading on short timeframes
**Wrong:** Watching the 1H chart all day and trading every signal.
**Right:** Set up alerts, check 4H/1D a few times per day, make fewer but higher-quality trades.

### ❌ Mistake 5: Trading when everything is NEUTRAL
**Wrong:** "It's NEUTRAL but I think it looks bullish, I'll trade it."
**Right:** NEUTRAL means the system found no edge. Your gut is not more reliable than 30 combined indicators.

### ❌ Mistake 6: Expecting 100% accuracy
**Wrong:** "The signal was wrong twice, this tool doesn't work."
**Right:** No tool is right 100% of the time. Success in trading comes from having an edge OVER TIME — even being right 55% of the time with good risk management is profitable.

### ✅ Good Practice: The Pre-Trade Checklist

Before entering any trade, ask yourself:

- [ ] Is the signal LONG or SHORT (not NEUTRAL)?
- [ ] Is strength at least 33? (ideally 50+)
- [ ] Do the higher timeframes mostly agree? (HTF Confluence)
- [ ] Do I know exactly where my stop loss goes?
- [ ] Is the R/R ratio at least 2:1?
- [ ] Am I risking only 1–2% of my account?
- [ ] Am I trading with money I can afford to lose?

If any answer is NO — **do not take the trade.**

---

## 14. Glossary

| Term | Plain English Meaning |
|---|---|
| **Long** | Buying, expecting price to go UP |
| **Short** | Selling or shorting, expecting price to go DOWN |
| **Spot** | Buying the real coin (no leverage, can't be liquidated) |
| **Futures/Perps** | Contracts that let you trade with leverage (riskier) |
| **Leverage** | Borrowing power. 5× leverage means a $100 deposit controls $500. Doubles both gains AND losses. |
| **Liquidation** | When a leveraged position loses so much that the exchange forcibly closes it and you lose your deposit |
| **Stop Loss (SL)** | An automatic exit order that closes your trade if price moves against you by a set amount |
| **Take Profit (TP)** | An automatic exit order that closes your trade when price hits your target |
| **R/R Ratio** | Risk-to-reward. 3:1 means you risk $1 to potentially make $3 |
| **Support** | A price level where buying has historically been strong (floor) |
| **Resistance** | A price level where selling has historically been strong (ceiling) |
| **Breakout** | When price moves decisively above resistance or below support |
| **Pullback** | A temporary move against the main trend (common buying opportunity) |
| **Confluence** | Multiple independent signals all pointing the same direction (stronger together) |
| **Candle / Candlestick** | A visual representation of price over a time period — shows open, high, low, close |
| **Bullish** | Expecting or showing upward price movement |
| **Bearish** | Expecting or showing downward price movement |
| **Volatility** | How much price swings up and down. High volatility = big swings = higher risk/reward |
| **Volume** | How many coins were traded in a period. High volume = strong move. Low volume = weak move. |
| **Timeframe (TF)** | The period each candle represents (1H = 1 candle = 1 hour of trading) |
| **HTF** | Higher Timeframe — a bigger picture view than what you are currently looking at |
| **OI (Open Interest)** | Total value of all open futures contracts |
| **CVD** | Cumulative Volume Delta — running total of buyers vs sellers |
| **Funding Rate** | Fee paid between longs and shorts in perpetual futures every 8 hours |
| **Max Pain** | The BTC options price where most contracts expire worthless (options market tends to pull toward it) |
| **FVG** | Fair Value Gap — a price zone the market skipped past and tends to return to |
| **BAG** | Break Away Gap — an explosive FVG caused by a very large single candle |
| **CHoCH** | Change of Character — when a trend starts showing signs of reversing |
| **MVRV** | Market Value to Realised Value — compares current price to what people paid on average |
| **Exchange Netflow** | How many coins are moving INTO vs OUT OF exchanges on the blockchain |
| **SGT** | Singapore Time (UTC+8) — the timezone used for signal slots |
| **Local trendline** | The auto-drawn solid green/red line hugging the current price leg — drives entries, stops and break triggers |
| **Macro line** | The grey dashed multi-week trendline — sets the big-picture bias; a fresh break of it = regime change |
| **Demand Zone** | A green band below price where buyers repeatedly stepped in (clustered swing lows) |
| **Supply Zone** | A red band above price where sellers repeatedly defended (clustered swing highs) |
| **Hidden divergence** | Price/RSI disagreement that confirms the TREND CONTINUING (vs regular divergence, which calls a reversal) |
| **Forming divergence ⏳** | A divergence visible in the newest candles but not yet confirmed by a closed swing — early, reduced weight |
| **Reversal Radar** | The exhaustion/bottoming gauge — counts contrarian evidence against the current trend |
| **200 EMA retest** | Price pulling back to the 200 EMA and closing back in the trend direction — classic continuation entry |
| **🗓️ daily+ context** | Data that moves over days–months (ETF, macro, on-chain); down-weighted and dimmed on 1H/2H |
| **dTAO / Alpha** | Bittensor's subnet tokens — buying one deposits TAO into that subnet's pool (locks supply) |
| **Subnet pool flow** | Net TAO entering (+) or leaving (−) Bittensor subnet pools — the "ETF flow" of TAO |
| **Net swap flow** | TAO bought minus TAO sold in a subnet's AMM pool over 24h — the per-subnet flow source |
| **Burn & Mint (GoMining)** | GoMining's weekly event: maintenance fees paid in GOMINING are burned; rewards distributed |

---

*CryptoMonk is a decision-support tool. All signals are based on technical analysis and historical patterns. Markets are unpredictable. Always do your own research, never invest more than you can afford to lose, and consider consulting a financial advisor before trading.*

---

**Version:** 3.0 | **Coverage:** 30 coins, 10 timeframes, 40+ indicators & structure tools
