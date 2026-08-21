-- ============================================================================
-- 008_rollback_market_metric_history.sql   *** REVIEW ONLY — DESTRUCTIVE ***
--
-- Drops market_metric_daily and the whole recorded market-state series with it.
-- That data cannot be rebuilt — it is a daily record of readings the providers
-- do not keep indefinitely, and there is nothing left to recompute it from.
--
-- Nothing else depends on this table — no foreign keys point at it, and no
-- scoring path reads it — so dropping it cannot break signal generation. It is
-- not run automatically and should not be run without a reason.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS market_metric_daily_series_idx;
DROP TABLE IF EXISTS market_metric_daily;

DELETE FROM schema_migrations WHERE version = '008';

COMMIT;
