# Decision evidence integrity

This change improves the evidence used to evaluate future tuning. It does not
change trading weights, thresholds, ranking, auto-execution, or strategy version.
It does not implement automatic learning or promise improved returns.

## Changes

- Decision snapshots use the same normalized eight-hour funding value as scoring.
  Old snapshots are not backfilled with today's data.
- Market and ETF snapshot endpoints return HTTP 503 when their collector reports
  failure, so the existing scheduled workflow retries instead of reporting success.
- Closed-trade analytical reads allow up to 1,000 rows, default 500, with explicit
  offset paging. Strength predicates run in SQL before LIMIT. Archived signals
  remain included in analytical endpoints.
- Postmortem, analytics and paper-account responses expose `sample_window`.
  These are bounded samples, not lifetime reports. `possibly_truncated` is a
  conservative flag, not proof of another page. Concurrent closures can shift
  offset pages; use a frozen export for research, not concatenated live reports.
- Strength buckets include descriptive Wilson intervals and are explicitly
  exploratory. Trades are correlated; these intervals do not account for that.
- `candidate_decisions` stores first-observed screened candidate evidence per
  environment, strategy, UTC four-hour slot and symbol. Rejected candidates retain
  their gate reason; selected candidates are NOT claimed to be published or filled.
  The insert is conflict-safe and batched. Audit failures log a stable code and do
  not change live selection.

## Deployment

1. Review and apply `database/migrations/012_candidate_decisions.sql` through the
   existing migration process on a staging database first. `python
   database/migrate.py status` shows pending migrations; `up` applies ALL pending
   migrations, so inspect status before running it.
2. Verify the table and deploy the application. Without migration 012 the audit
   writer reports `MIGRATION_012_REQUIRED`; trading behavior is unchanged.
3. Verify a new slot produces environment-scoped rows, with no duplicates on
   retry. Check for `SNAPSHOT_FAILED` and `WRITE_FAILED` logs.
4. Monitor storage growth. This table stores compact snapshots, not raw candles,
   but still grows with symbols × six slots/day × strategy versions × environments.
   No automatic deletion is introduced. Export and agree a retention policy before
   pruning; archiving in the same database does not reduce storage.

Rollback: revert application changes; leave the additive table intact to retain
evidence. No production migration or data deletion is performed by this PR.

## Important remaining limits

The candidate log records the first successful observation, not all recomputations
or a complete point-in-time replay. Symbols whose fetches all fail never reach
screening; they are not covered by this initial audit. It stores the bounded 2H
snapshot and timeframe strengths, not every raw provider response or 1H/4H input.
It cannot label rejected trades as wins or losses without a separately defined
counterfactual execution model. Daily collectors can still return partial data;
HTTP failure handling alone does not prove complete provider coverage.

Next research work must use immutable point-in-time data, timestamped source
availability, chronological train/validation/untouched test splits, realistic
fees/slippage/funding, and shadow evaluation. Repeatedly tuning against the same
historical sample is overfitting. Require explicit review before promoting any
new weights to the live strategy.
