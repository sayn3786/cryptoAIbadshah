-- ============================================================================
-- 005_rollback_pattern_events.sql   *** REVIEW ONLY — DESTRUCTIVE ***
--
-- Drops pattern_events and everything logged in it. That data cannot be
-- rebuilt: it is a record of what detectors saw at bars that have since aged
-- out of every lookback window, so there is nothing left to recompute it from.
--
-- Nothing else depends on this table — no foreign keys point at it, and no
-- scoring path reads it — so dropping it cannot break signal generation. That
-- is the only reassurance this file offers. It is not run automatically and
-- should not be run without a reason.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS pattern_events_kind_idx;
DROP INDEX IF EXISTS pattern_events_lookup_idx;
DROP INDEX IF EXISTS pattern_events_idem_idx;
DROP TABLE IF EXISTS pattern_events;

DELETE FROM schema_migrations WHERE version = '005';

COMMIT;
