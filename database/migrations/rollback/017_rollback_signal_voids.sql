-- ============================================================================
-- 017_rollback_signal_voids.sql  (REVIEW ONLY — never run from migrate.py)
-- Drops the void records: every voided signal counts in the statistics again.
-- The code degrades safely without the table (it checks to_regclass).
-- ============================================================================
BEGIN;

DROP TRIGGER IF EXISTS signal_voids_immutable ON signal_voids;
DROP TABLE IF EXISTS signal_voids;
DROP FUNCTION IF EXISTS reject_signal_void_mutation();

COMMIT;
