# Phase 1–2: ML research dataset (no live model)

This is the data foundation for comparing logistic classification and Ridge
return regression later. It does not train a model, modify signal weights, place
orders, or promise greater accuracy. No new paid service is required by this code;
database storage and provider usage still consume the user's existing quotas.

## Frozen v1 research contract

- Inputs: 64 consecutive CLOSED 1H spot candles from one supported exchange.
- Collection: intended every four hours, independently of selected/published
  signals. Default universe is every current `SCAN_SYMBOLS` member, including BTC.
- Actual `observed_at` is captured after each fetch. The four-hour slot labels
  the collection run; it is NOT a fabricated historical observation timestamp.
- Entry reference: the next hourly candle open strictly after observation.
  Exit reference: the close of the fourth hourly candle from that open.
  Therefore there is up to one hour of waiting before the four-hour target starts.
  This differs from an immediate-entry next-four-hour forecast, deliberately:
  the already-closed feature candle is not an executable entry price.
- Label: raw return >20 basis points is UP; <-20 is DOWN; otherwise NEUTRAL.
  Twenty basis points is an initial research convention, not measured trading
  costs or a tuned threshold. A threshold change requires a new label version.
- This is a spot-price-direction label, not evidence of short availability,
  futures P&L, TP-before-SL, or profitable execution. Trading evaluation must add
  actual fees, spread, slippage, funding and the current exit policy separately.

Eleven features: returns over 1/4/12/24 hours, simple-window RSI14, EMA20/EMA50
price distances, EMA12-minus-EMA26 divided by price, simple ATR14 divided by
price, relative volume against the prior 20 bars, and 24-hour range divided by
price. All percentage-valued features are in percent; relative volume is a ratio.
EMA seeds use the first of the 64 bars. These formulas are versioned independently
from trading indicators. No composite signal strength, outcome, raw provider
payload, credentials, or future candles is included in features.

Funding/OI, BTC cross-asset features, patterns, model registry and predictions
are deferred. Genuine source availability and sufficient coverage must be
established before adding them. Never populate past features from current APIs.

## Guardrails

- Synthetic/CoinGecko approximations and unknown sources are rejected.
- Non-finite values, invalid OHLC geometry, duplicate, gapped, wrong-interval,
  insufficient and stale candles produce invalid observations, not training rows.
- Zero prior volume produces a missing relative-volume feature, not infinity.
- Future labels require four exact hourly bars, completed by the label's
  availability timestamp, from the SAME exchange as the features.
- Both tables use first-insert-wins keys. Retry does not overwrite history.
  A failed first observation stays failed for that slot; this is intentional.
- Feature hashes identify the normalized input window but cannot reconstruct it.
  Raw-candle archival and independent research exports remain future work.

## Rollout (explicit, not performed by this PR)

1. Review migration status, then apply only
   `database/migrations/013_ml_research_dataset.sql` to a test database first.
2. Use the existing backend requirements. Configure DATABASE_URL securely using
   the existing local setup. Do not put a URL/password into a command or commit.
3. Inspect a price-only dry run (fetches market data but writes nothing):

   ```sh
   python scripts/ml_dataset.py collect --environment research --symbols BTC,ETH
   ```

4. After reviewing the output, collect the full current scan universe:

   ```sh
   python scripts/ml_dataset.py collect --environment research --write
   ```

5. More than four hours AFTER the next hourly open, create completed labels:

   ```sh
   python scripts/ml_dataset.py label --environment research --write
   ```

   Without `--write`, labeling queries pending snapshots and fetches candles but
   does not persist labels. Jobs return nonzero on incomplete data. Error output
   is sanitized. No orders or messages are dispatched.

6. Once manually verified, arrange a separate research runner at a consistent
   four-hour cadence. This PR intentionally adds no scheduler, Vercel route or
   publication hook: collection must not consume the live publication budget.
   Include both collection and labeling, log failures and record missed runs.
   Fixed-cadence operation is NOT active merely because this PR is deployed.

The label fetch is limited to the latest 200 candles per symbol and batches
100 pending rows (maximum 500). Run regularly; old missing labels or changed
exchange sources need explicit source-matched historical backfill. Do not mark
them neutral or substitute another exchange. An oldest-first backlog can require
operator intervention; inspect pending age before relying on continuous labeling.

## Read-only coverage checks

```sql
SELECT environment, feature_version, quality, reason, COUNT(*) AS rows,
       MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen
FROM ml_feature_snapshots
GROUP BY environment, feature_version, quality, reason;

SELECT f.environment, l.label_version, l.direction, COUNT(*) AS rows
FROM ml_labels l JOIN ml_feature_snapshots f ON f.id = l.snapshot_id
GROUP BY f.environment, l.label_version, l.direction;
```

## Before training

Verify scheduled coverage, missingness by symbol/source/time and label maturity.
Use chronological timestamp-grouped train/validation/test splits. Keep all tokens
from a time bucket together, purge training outcomes overlapping validation/test,
and fit preprocessing only on training observations. Same-time token rows are
correlated and do not provide independent market-history samples. A changing
SCAN_SYMBOLS universe must be explicitly accounted for; this is not an unbiased
sample of all crypto assets or delisted tokens.

Use fixed dataset exports, document source revisions, compare class-frequency
and momentum baselines, and preserve an untouched final evaluation period.
Only then implement the model/prediction registry, train logistic/Ridge baselines,
and shadow-score without changing live rules. No promotion based on in-sample
accuracy. The next implementation phase must add dataset exports and these split
guards before fitting a model.

Rollback: stop the research runner and revert code; retain tables and records.
No automatic deletion or retention job is included. Review storage usage and
agree export/retention before enabling unattended collection.
