-- ============================================================================
-- 009_rollback_app_users.sql   (REVIEW ONLY — destructive; never run from migrate.py)
-- Drops the dashboard login table. This DELETES every account. Only run if you
-- are abandoning the login feature entirely; set AUTH_REQUIRED off first so the
-- app does not try to authenticate against a table that no longer exists.
-- ============================================================================
BEGIN;

DROP INDEX IF EXISTS app_users_username_lower_uidx;
DROP TABLE IF EXISTS app_users;

DELETE FROM schema_migrations WHERE version = '009';

COMMIT;
