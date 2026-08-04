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

## Phase 4 — Measurable validation

Two backtests live here and they answer different questions. Confusing them is
how a strategy gets validated by a test that never ran it.

| | `/api/backtest/portfolio` | `/api/backtest/<SYMBOL>` |
|---|---|---|
| question | does the **published strategy** have edge? | do these **indicators** contain price/structure edge? |
| module | `backend/portfolio_backtest.py` | `backend/backtest.py` |
| status | current | **deprecated**, `result_kind: legacy_price_only` |
| universe | every scanned symbol, ranked against each other | one symbol, alone |
| timeframes | 1H **and** 2H must agree; 4H scored | one |
| entry | resting limit, may never fill | market at the next bar's open |
| exits | 50/30/20 with a breakeven stop | TP1 closes everything |

---

### Phase 4a — Production-parity walk-forward (`portfolio_backtest.py`)

**Problem.** The single-timeframe harness was being read as evidence about
published trades, and it was measuring something else. It had no 1H/2H
agreement, no BTC adjustment, no R/R gate, no expired-setup check, no ranking,
no top-three; it entered at the next bar's open, and it booked TP1 as a
complete exit.

Every one of those omissions flatters, and two of them badly. A market order
cannot fail to fill, while the limit order production actually rests frequently
never does — and the orders that never fill are disproportionately the ones
price ran away from, which is to say the winners. Booking TP1 whole records a
50% exit as 100%.

**What production parity means here.** Not "similar rules". The *same
functions*: `rec_policy` for every gate, the ranking and the top-three, and
`signal_monitor.evaluate` for every fill, target, stop move and expiry. Nothing
in the replay restates a rule, and the parity tests assert this by identity —
`app._passes_tf_gates is rec_policy.passes_tf_gates`. Two copies of a policy
drift, and they drift toward whichever one is being measured.

**The walk.** For every historical 4H publication slot, in order:

1. take only candles **closed at or before** that instant — a candle counts
   when `open_ts + bar_length <= slot`, so the forming bar is never visible;
2. run the real `generate_signal` on the 1H, 2H and 4H slices;
3. compute the BTC context once for the slot and screen every symbol through
   `rec_policy.screen_candidate` — the gates, in production's exact order;
4. rank by the 1H/2H average with quality as the tiebreak, then apply
   correlation-aware diversification and take the top three.

**The execution.** Each published recommendation becomes a resting limit order
at its own entry — not the next open:

| rule | behaviour |
|---|---|
| fill | only when a later candle's range trades **through** the entry |
| before the fill | no target and no stop can trigger; there is no position |
| unfilled after **24h** | `CANCELLED` — never a trade, never a win, loss or zero |
| TP1 | closes **50%**, stop moves to **entry (breakeven)** |
| TP2 / TP3 | **30%** / **20%**, from `signal_store.SCALE_OUT_SHARES` |
| after a partial | the trade continues; TP1 does **not** end it |
| stale after **72h** | `EXPIRED`, closed at the last price seen |

The clock steps with the candles, one at a time. Evaluating the whole future in
one call would let an order that price returned to on hour 30 still fill, when
production had withdrawn it on hour 24 and never taken the trade.

**Intrabar ambiguity.** OHLC does not reveal ordering. When one candle touches
both a target and the stop after entry, the **stop** is recorded. When the
filling candle also reaches the stop, that is a real same-bar stop-out. Both
rules are pessimistic on purpose: anything else lets the record claim wins the
data cannot prove. Every report restates this in `parity.execution`.

**Fees and slippage.** `(fee_bps + slippage_bps)` is charged on the entry leg
and again on every fraction closed — per leg, not as a flat round trip, so a
position that expired half open is not over-charged. Defaults are 6bp fee and
2bp slippage, and they are deliberately not zero: a strategy that only works at
zero cost does not work.

#### Parity modes — and why the mode is always stated

Production also scores funding, open interest, futures CVD, long/short ratio,
sentiment, macro, on-chain, ETF flows and options. None of those have a
historical series here.

- **`price_only`** (default) replays the OHLCV-derived groups only. Those
  scoring blocks stay dormant and `generate_signal` degrades gracefully. It
  measures the **price/structure edge** and **cannot validate any external
  input** — the result says so in `parity.external_data.note`.
- **`historical_full`** accepts timestamped external snapshots, uses only
  observations whose `available_at` is at or before the slot, **rejects
  future-dated ones**, and reports coverage per feature family.

Substituting current or neutral values for missing history is not implemented
and will not be. It produces a confident number built on information the
strategy never had, which is worse than reporting nothing. The endpoint returns
**400** for `historical_full` rather than faking it.

#### What is still not parity

Stated in `parity.known_non_parity` on every run, not just here:

- **No live tick.** `signal_price` and `live_price` are both the last closed
  price, so the divergence gate and the TP1-behind-live gate are measured
  against the close rather than an intra-slot tick.
- **`data_quality` is always `good`.** Replay feeds complete closed candles
  from one source, so candidates production drops for stale or misaligned data
  are still published here. This is optimistic.
- **The on-chain multiplier and options pressure are constants**, not series.

#### Populations — kept apart

An unfilled order is not a zero-return trade. Folding cancellations into the
win rate measures a population nobody could have traded.

`candidates_generated` · `recommendations_published` · `orders_filled` ·
`orders_cancelled_unfilled` · `trades_completed` · `trades_expired` ·
`open_at_dataset_end`, plus a `rejections` histogram keyed by the gate that
fired first. Every published recommendation lands in exactly one terminal
bucket, and a test asserts the arithmetic.

#### Metrics

Over the **filled and closed** population only: expectancy in R, realized
return %, profit factor, win rate, max drawdown in R, max consecutive losses,
average hold, TP1/TP2/TP3 hit rates, TP1-then-breakeven and TP2-then-breakeven
counts, stop-loss count, long/short split, per-symbol and per-slot breakdowns.
Fill rate and cancellation rate are reported against the **published**
population, because that is what they are rates of.

R for a scaled-out position is net realized return ÷ the initial risk distance.
A TP1-then-breakeven trade therefore lands slightly positive, not at zero and
certainly not at −1R.

#### Running it

```
/api/backtest/portfolio                          # scan universe, last 20 slots
/api/backtest/portfolio?symbols=BTC,ETH,SOL&slots=40
/api/backtest/portfolio?fee_bps=8&slippage_bps=4&trades=1
```

| param | default | meaning |
|---|---|---|
| `symbols` | scan universe | comma-separated; BTC is always included |
| `cap` | `6` | max symbols — each costs a full 1H/2H/4H analysis per slot |
| `slots` | `20` | publication slots, most recent first |
| `limit` | `700` | candles pulled per timeframe (200–1000) |
| `fee_bps` | `6` | fee per leg |
| `slippage_bps` | `2` | slippage per leg |
| `trades` | off | include the per-trade ledger |

`cap` and `slots` exist because this runs inside the same **60s** function
ceiling as everything else on this plan, and a replay is 3 analyses × symbols ×
slots. Raising them is how the endpoint starts timing out.

**Reading the result.** Expectancy > 0 R after costs, profit factor > 1, and a
drawdown profile you would actually sit through. Then check the populations: a
good expectancy on 6 filled trades out of 40 published is a fill-rate problem
wearing a win-rate's clothes. And the mode — a `price_only` result is evidence
about price and structure, and about nothing else.

Tests passing means the implementation is correct. It does not mean the
strategy is profitable.

---

### Phase 4b — Indicator ablation (`backtest.py`, deprecated)

Kept for one reason: it is the only thing that runs a **per-group ablation**,
which is a real question about the indicators. It re-runs with each price
group's inputs neutralised and reports the **expectancy drop** vs baseline — a
positive drop means removing the group hurt (it added edge); ≤ 0 means the
group added no independent predictive value.

Its trades are not the strategy's trades. Responses carry
`result_kind: legacy_price_only`, `deprecated: true` and a
`not_a_strategy_test` note listing exactly what is missing, so a reader cannot
pick the number up out of context.

```
/api/backtest/BTC?tf=2H&limit=1000
/api/backtest/ETH?tf=2H&ablation=1&stride=2          # group-ablation study
```

| param | default | meaning |
|---|---|---|
| `tf` | `2H` | timeframe |
| `limit` | `500` | candles to pull (120–1000) |
| `min_strength` | `35` | strength gate to take a trade |
| `max_hold` | `24` | bars before a time-stop exit |
| `warmup` | `60` | leading bars skipped for indicator seeding |
| `fee_bps` | `6` | round-trip fee+slippage deducted per trade |
| `stride` | `1` | evaluate every Nth bar (use 2–3 with `ablation`) |
| `ablation` | off | also run the group-suppression study |
| `trades` | off | include the per-trade ledger |
