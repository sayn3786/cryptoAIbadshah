# Two-hour collection and outcome targets

Apply `database/migrations/015_ml_two_hour_horizon.sql` in Neon BEFORE merging
or deploying this change. It expands the label constraints to allow versioned
two-hour outcomes, preserves existing four-hour rows, and records migration 015.
Do not edit migrations 013/014 or delete snapshots. The migration also accepts
old four-hour writes, so applying it before the application update is safe.
No migration runs automatically. Verify migration status after application.

New snapshots: `candles_1h_2h_slots_v3`, distinct two-hour UTC collection slots.
New labels: `next_open_2h_20bps_v1`, exactly two completed hourly candles.
Input candles remain 1H; features retain their existing formulas and lookbacks.
The inclusive neutral band remains +/-20bps, a research convention, not costs.

Cloudflare collects at even UTC hours :10 (00:10, 02:10, ...), and labels at odd
UTC hours :15 (01:15, 03:15, ...). Example: collect 00:10, target next hourly
open 01:00 to 03:00, label 03:15. A two-hour target is NOT two hours from the
request time: next-open entry creates a delay of up to an hour. Singapore is
UTC+8, so parity and minute values are the same. Up to 24 new BTC/ETH examples
per day; delays, source collisions, and failures can reduce that count.

The labeler continues handling ready `candles_1h_v2` snapshots with the old
four-hour horizon, old label version, and old four-hour retry delay. It never
turns those into two-hour targets. New-version label retries wait two hours;
the maximum three attempts/backfill rule is unchanged. Unknown feature versions
are excluded. Same-exchange labels remain mandatory, including old Binance rows.

The GitHub workflow stays manual-only. Do not reactivate its old four-hour cron.
Cloudflare and Vercel can deploy in either order AFTER migration 015: temporary
cadence mismatch may skip a slot or produce duplicate no-ops, but versions keep
outcomes honest. Confirm both deployments before judging the steady-state rate.

All research SQL/training must group or filter by feature_version AND
label_version/horizon_hours; do not pool old four-hour targets into two-hour
training. Raw spot labels are not realized trading P&L or evidence of accuracy.

Read-only verification:

```sql
SELECT s.feature_version, s.source, count(*) AS snapshots,
       count(l.snapshot_id) AS labeled, max(s.observed_at) AS latest_snapshot,
       max(l.available_at) AS latest_label
FROM ml_feature_snapshots s
LEFT JOIN ml_labels l ON l.snapshot_id = s.id
 AND l.label_version = CASE WHEN s.feature_version = 'candles_1h_2h_slots_v3'
 THEN 'next_open_2h_20bps_v1' ELSE 'next_open_4h_20bps_v1' END
WHERE s.environment = 'research' AND s.quality = 'ready'
 AND s.feature_version IN ('candles_1h_v2', 'candles_1h_2h_slots_v3')
GROUP BY s.feature_version, s.source;
```

Rollback application code if necessary, but keep migration 015 and all evidence.
Old code will not process new-version pending rows; restore the new labeler to
resume those. No live trading or signal engine settings change.
