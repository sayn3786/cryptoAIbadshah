-- ============================================================================
-- 010_rollback_app_users_session_version.sql   (REVIEW ONLY — never run from migrate.py)
-- Drops the session-revocation counter. Existing cookies stop being version-
-- checked (they remain valid until expiry). Set AUTH_REQUIRED off first if you
-- are unwinding the whole login feature.
-- ============================================================================
BEGIN;

ALTER TABLE app_users DROP COLUMN IF EXISTS session_version;

DELETE FROM schema_migrations WHERE version = '010';

COMMIT;
