# Signal Reliability — Phases 1–4

This document tracks the reliability-engineering effort on branch
`improve/signal-reliability`. The objective was to improve the **reliability,
honesty, risk management, and testability** of the CryptoMonk trading signals —
*without* adding indicators unless evidence shows independent predictive value.

The work is intentionally kept on its own branch, **not merged to `main`**,
until the Phase-4 backtest confirms the changes help rather than just look more
principled.

---

## Phase 1 — Closed-candle signals & data integrity

**Problem:** every indicator was computed from a candle list whose last element
was the still-*forming* bar. A forming bar repaints (its high/low/close change
every tick), so signals flickered and any backtest would lie.

**Changes (`backend/app.py`):**
- `_split_closed(candles, interval_s)` — a candle is *closed* when
  `open_time + interval ≤ now`; the forming bar is set aside for display only.
- One reassignment in `build_analysis` (`spot, live_candle = _split_closed(...)`)
  makes **every** downstream feature compute on closed candles.
- `_assess_data_quality(...)` grades each analysis `good | degraded | invalid`
  from demo-data / thin-history / staleness / timestamp-misalignment /
  live-vs-signal price gap, and stamps `tradeable`, `data_quality`,
  `signal_candle_closed_at`, `data_age_seconds`.
- CVD closed-bar trimming (`backend/cvd_sources.py`, `coinglass.py`:
  `_closed_series`).
- Dashboard shows a data-quality chip and renders the live bar separately.

## Phase 2 — Reduce double-counting

**Problem:** within a group, signals are correlated (RSI level + slope + ROC +
StochRSI + MACD all read the same momentum) yet summed at full weight, so one
market condition could stack 100+ points.

**Changes (`backend/signals.py`):** transparent per-group soft-caps
(`trend 52, momentum 44, flow 48, sentiment 42, pattern 38`). When a group
exceeds its cap the excess is trimmed and a reason line explains it. Moderate
signals are untouched; only pile-ups are clipped.

## Phase 3 — Recommendation selection

**Problem:** recs were ranked on raw adjusted strength and used a
"force-an-opposite-direction" hack that could surface weak counter-trend trades
just to show both sides.

**Changes (`backend/app.py`):**
- Scan adds **4H** for higher-timeframe confirmation.
- `_rec_quality()` composite score = adjusted strength + R/R bonus
  (≥3.0 +10 / ≥2.0 +5 / <1.3 −12) + 4H agreement (+8 / −10) − reversal-radar
  fighting the trade (high −15 / elevated −8) − 2H exhaustion (−6) + fresh 2H
  flips (+4) − degraded data (−6). Factors surfaced on the card.
- Hard filter: drop any trade with **R/R < 1.3**.
- Rank by quality (tie-break strength) instead of raw strength.
- **Correlation-aware diversification** replaces the hack: never publish a 3rd
  high-correlation (BTC-corr ≥ 0.7) same-direction pick; backfill only if short.

## Phase 4 — Measurable validation (backtest harness)

**Problem:** after Phases 1–3 the signals are *cleaner*, but "cleaner" is not
"profitable". Phase 4 answers the only question that matters — **do they have
edge?** — with numbers.

**How it works (`backend/backtest.py`):** walk real candle history forward one
closed bar at a time; at each bar rebuild the price/structure analysis on the
repaint-free view, run the **real** `generate_signal`, and simulate any
actionable trade with **no look-ahead**:
- enter at the **next bar's open**,
- SL/TP from the signal's own % distances applied to the real fill,
- if a bar straddles both SL and TP1, assume **SL first** (pessimistic),
- time-stop at `max_hold` bars,
- outcome expressed as an **R-multiple**; a fee/slippage (bps) is deducted.

Headline metrics: trade count, win-rate, **expectancy (R)**, profit factor,
avg win/loss R, max drawdown (R), max consecutive losses, avg hold, and an
outcome breakdown (tp1/tp2/tp3/sl/time).

**Group ablation** (`ablation=1`): re-runs with each price group's inputs
neutralised and reports the **expectancy drop** vs baseline — a positive drop
means removing the group *hurt* performance (it added edge); ≤ 0 means the group
added no independent predictive value. This is the evidence test the brief asked
for before keeping any indicator.

### Honest scope

Only the **OHLCV-derivable** groups are replayable, so the backtest reproduces
**trend, momentum and pattern** faithfully. **Flow** (funding / OI /
futures-CVD / long-short) and **sentiment / cycle** (Fear & Greed, news,
on-chain, ETF, macro, options) have no historical series to replay — those
inputs are absent and their scoring blocks simply don't fire
(`generate_signal` degrades gracefully). Every result payload carries a
`scope_note` so this is never misread as validating the whole engine.

### Running it

Exposed as a debug endpoint (run it in production where the exchange APIs are
reachable):

```
/api/backtest/<SYMBOL>?tf=2H
/api/backtest/BTC?tf=2H&limit=1000
/api/backtest/TAO?tf=4H&min_strength=40&max_hold=18&fee_bps=8
/api/backtest/ETH?tf=2H&ablation=1&stride=2          # group-ablation study
/api/backtest/SOL?tf=1H&trades=1                     # include per-trade ledger
```

| param          | default | meaning                                             |
|----------------|---------|-----------------------------------------------------|
| `tf`           | `2H`    | timeframe                                            |
| `limit`        | `500`   | candles to pull (120–1000)                           |
| `min_strength` | `35`    | strength gate to take a trade                       |
| `max_hold`     | `24`    | bars before a time-stop exit                        |
| `warmup`       | `60`    | leading bars skipped for indicator seeding          |
| `fee_bps`      | `6`     | round-trip fee+slippage (bps) deducted per trade    |
| `stride`       | `1`     | evaluate every Nth bar (use 2–3 with `ablation`)    |
| `ablation`     | off     | also run the group-suppression study (slower)       |
| `trades`       | off     | include the full per-trade ledger                   |

**Reading the result:** expectancy > 0 R (after fees) with profit factor > 1 and
an acceptable max-drawdown/consecutive-loss profile is the bar for the
price/structure edge being real. Compare across timeframes (2H is primary) and a
spread of tokens before trusting any single number — the available history is
finite, so treat small trade counts as indicative, not conclusive.
