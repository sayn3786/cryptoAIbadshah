# Historical replay pilot (research only)

This adds local, date-bounded OKX SPOT candle archives and a small price-only
replay. It does not change live signals, schedulers, Neon, or HL execution.
No API key is required. Do not put database/exchange credentials in commands.

## Run

From the repository root in a Python environment with requirements installed:

```sh
python scripts/historical_replay.py download --symbols BTC,ETH \
  --start 2026-09-01T00:00:00Z --end 2026-09-15T00:00:00Z \
  --archive /tmp/cryptostars-pilot/okx-september.json

python scripts/historical_replay.py replay \
  --archive /tmp/cryptostars-pilot/okx-september.json \
  --split 2026-09-09T00:00:00Z \
  --output /tmp/cryptostars-pilot/september-report.json
```

The evaluation interval is start-inclusive/end-exclusive, at most 31 days by default.
For longer research, explicitly pass `--max-days 180` (maximum 366) to download.
The exact `--start` and `--end` still determine the requested data; this flag is
only a safety ceiling. The 100-page per-symbol budget remains in force and an
incomplete run can be resumed with the same command. No multi-month download
is scheduled automatically. More symbols may be explicitly requested, e.g.
`--symbols BTC,ETH,ICP,LINK,SUI`; availability is verified by complete candle
coverage, and unavailable instruments fail closed instead of being dropped.
Start, split and end must be UTC four-hour boundaries. The downloader also
retrieves 40 days of indicator warmup and four days of outcome candles. End
must therefore be at least four days in the past. Dates and symbols are fixed
in the archive specification; use a new archive for different parameters.

Rerun the identical download command to resume: pages are checkpointed locally,
with hashes checked before reuse. Complete symbols are not downloaded again.
Each invocation is bounded to 100 pages per symbol and three attempts per HTTP
request. Missing candles, unconfirmed candles, non-finite/invalid OHLCV, stalled
pagination and provider errors stop the run. No synthetic candles or alternate
exchange fallback is permitted. Keep generated archives outside Git.

Provider reference: [OKX API documentation](https://www.okx.com/docs-v5/en/),
GET /api/v5/market/history-candles. `after` requests older bars; confirmed bars
have the final `confirm` field set to `1`.

## What is compared

1. Baseline published recommendations from the shared `portfolio_backtest`
   screen/rank/select logic, executed by its shared paper position and signal
   monitor lifecycle. Publication cadence remains 4H; this is NOT the 2H ML
   future-return target. BTC is context and ETH is the tradable pilot symbol.
2. A fixed entry-time gate: weighted target reward divided by original stop
   risk must be at least 1.0 (configurable `--rr-floor`, frozen before viewing
   holdout). The targets use the existing scale-out fractions. This is price
   geometry, not expected profit or a calibrated probability. It skips members
   of the baseline published set; it does not rerank or replace them.
3. For both sets, report per-trade net percentage returns and a capped sizing
   attribution: reference-capital risk budget 0.5%, maximum notional 100% of
   reference capital. This is NOT an executable portfolio simulation. Concurrent
   trades, balance/reservation constraints, HL minimums and lot rounding are
   not modeled, so do not quote it as account return or drawdown.

Execution cost defaults are explicit hypotheses: 6bps fee plus 2bps slippage
per leg, charged on the entry and exited fractions by the shared cost function.
They are not asserted to be current HL fees. Set both CLI cost flags explicitly
when testing other assumptions. Funding and liquidity are not modeled.

## Time isolation and provenance

Hourly candles aggregate into complete UTC 2H/4H candles. Generation is cut at
the evaluation end, and the shared engine only reads closed bars per slot.
Outcome-tail bars are used only by position execution. Discovery excludes
recommendations whose full 96-hour possible lifecycle crosses the split.
Holdout starts at the split. Thresholds are not automatically fitted or selected.
There may be zero recommendations, which is a valid inconclusive pilot result.

The archive stores actual retrieval time and normalized candle hashes. These
are historical prices retrieved later, not evidence of what an API returned at
the original historical time. No live `ml_feature_snapshots` rows are backfilled.
The report includes archive hashes and a backend-code hash for reproducibility.

## Important limitations

- Always `subset_price_only_pilot`; missing external snapshots, market caps and
  full-universe competition prevent claims of production parity.
- Baseline is the shared signal-tracking lifecycle. It does NOT apply the newer
  v54 HL testnet stop/exit variants or model actual exchange order fills.
- This two-symbol pilot validates mechanics, not the altcoin losses found in
  Neon. Explicitly add supported OKX spot symbols in a later fresh experiment.
- A once-viewed holdout must not be repeatedly tuned against and called untouched.
  This short pilot is not model-readiness or profitability evidence.
- No model is trained or connected to confluence/auto-trading here.

Tests:

```sh
python -m pytest tests/test_historical_pilot.py tests/test_production_parity_backtest.py tests/test_ml_dataset.py
```
