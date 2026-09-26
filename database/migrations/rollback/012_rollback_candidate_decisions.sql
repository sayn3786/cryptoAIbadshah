-- ============================================================================
-- 012_rollback_candidate_decisions.sql  (REVIEW ONLY — never run from migrate.py)
-- Drops the candidate decision audit table and ALL evidence stored in it.
-- The writer degrades safely without the table (it checks to_regclass and
-- skips), so rolling back does not affect signal publishing.
-- ============================================================================
BEGIN;

DROP TABLE IF EXISTS candidate_decisions;

DELETE FROM schema_migrations WHERE version = '012';

COMMIT;
