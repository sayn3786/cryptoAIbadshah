-- ============================================================================
-- 007_rollback_etf_flow_history.sql   *** REVIEW ONLY — DESTRUCTIVE ***
--
-- Drops etf_flow_daily and the entire recorded ETF-flow history with it. That
-- data cannot be rebuilt beyond the provider's rolling window — the whole point
-- of the table was to keep days the provider will eventually drop.
--
-- Nothing else depends on this table — no foreign keys point at it, and no
-- scoring path reads it — so dropping it cannot break signal generation. That is
-- the only reassurance this file offers. It is not run automatically and should
-- not be run without a reason.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS etf_flow_daily_lookup_idx;
DROP TABLE IF EXISTS etf_flow_daily;

DELETE FROM schema_migrations WHERE version = '007';

COMMIT;
