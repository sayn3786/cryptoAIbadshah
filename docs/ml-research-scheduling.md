# Automatic research dataset

The `ML Research Dataset` GitHub Actions workflow calls the existing protected
Vercel POST endpoints. This reuses the repository's existing scheduler approach;
it adds no Vercel cron, database migration, trading action, or model training.
Only BTC/ETH, OKX, `write: true`, and label batch limit 10 are used.

## Activation

1. Merge the workflow and client into the default branch. Scheduled workflows
   execute from that branch. This enables the schedule (no extra feature flag).
2. Repository Settings → Secrets and variables → Actions must contain `APP_URL`
   set to `https://cryptomarket-sigma.vercel.app` (no path), and `CRON_SECRET`
   matching the CURRENT production Vercel secret. Updating Vercel does not update
   GitHub secrets. These names already exist; their values cannot be verified
   by listing secrets. A mismatch fails visibly, without database access.
3. Vercel production must include the research endpoints, migrations 013/014,
   `DATABASE_URL`, and `ML_RESEARCH_ENABLED=true`. Confirm the production domain
   points at the intended main deployment. No DB credentials belong in Actions.
4. Actions → ML Research Dataset → Run workflow → select `label` for an initial
   authenticated check. No eligible labels is a successful no-op, not proof that
   every snapshot has been labeled. Use `collect` in a new slot to test inserts.

## Timing (not exact delivery guarantees)

Collection: 00:10, 04:10, 08:10, 12:10, 16:10, 20:10 UTC every day.
Singapore: 08:10, 12:10, 16:10, 20:10, 00:10, 04:10 respectively.
Labeling: 01:15, 05:15, 09:15, 13:15, 17:15, 21:15 UTC.
A 00:10 collection targets 01:00–05:00, so the 05:15 label run handles it.
The 01:15 run handles prior-day observations. Label selection always checks the
actual maturity timestamp; a delayed collector cannot cause premature labels.

GitHub can delay/drop scheduled jobs; this is not a precise trading clock.
Delayed collections record actual observation times, never retrospective
features. Missed slots are not fabricated. Subsequent label runs can catch up
10 eligible rows at a time within the provider retrieval window. Very old or
repeated failures require backfill, not invented labels. Public-repository
schedule inactivity rules and account Actions availability also apply.

## Failure handling and visibility

Runs are serialized, never cancelled mid-write, and limited to five minutes.
Each HTTP request is limited to 75 seconds. Transport errors, 429, 502, and 504
get up to three attempts with 10/20-second delays. Retried inserts are idempotent.
Application 503s are NOT retried immediately: the endpoint may have already
written immutable invalid observations or advanced its four-hour label retry
queue. Inspect the failed Actions run and sanitized Vercel logs. Existing queue
limits/backfill rules remain authoritative. No exchange substitution occurs.

The client rejects wrong namespace/source/mode and incomplete counts even on
HTTP 200. Only allowlisted status/counts are logged, never raw responses/secrets.
Duplicate collection generates a warning: a Binance or invalid record can own
the same slot, and a ready HTTP response doesn't prove an OKX row was inserted.
Keep the old Mac collector off. Existing Binance rows require same-source manual
labeling; this workflow intentionally does not process them.

Enable GitHub Actions failure notifications in your GitHub notification settings.
Run history detects failed calls, but cannot alert when GitHub never starts a
run; there is no independent missing-run watchdog in this change. Periodically
check the following in Neon (read-only):

```sql
SELECT source, quality, count(*), max(observed_at) AS latest_observation
FROM ml_feature_snapshots
WHERE environment = 'research' AND observed_at > now() - interval '24 hours'
GROUP BY source, quality;

SELECT s.symbol, s.source, s.observed_at,
       to_timestamp(s.entry_at_ms / 1000.0) + interval '4 hours' AS mature_at
FROM ml_feature_snapshots s
LEFT JOIN ml_labels l ON l.snapshot_id = s.id
 AND l.label_version = 'next_open_4h_20bps_v1'
WHERE s.environment = 'research' AND s.source = 'okx'
 AND s.feature_version = 'candles_1h_v2' AND s.quality = 'ready'
 AND l.snapshot_id IS NULL
 AND to_timestamp(s.entry_at_ms / 1000.0) + interval '5 hours' < now()
ORDER BY s.observed_at;
```

Healthy uninterrupted collection yields up to 12 new snapshots/day. Check gaps
and stale observations, not just aggregate count. Two labels don't establish
predictive accuracy. Stop by disabling this workflow in GitHub Actions; disabling
`ML_RESEARCH_ENABLED` in Vercel also blocks calls but scheduled runs will fail.
