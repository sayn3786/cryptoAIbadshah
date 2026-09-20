-- ============================================================================
-- 010_rollback_app_users_session_version.sql   (REVIEW ONLY — never run from migrate.py)
-- Drops the session-revocation counter. Existing cookies stop being version-
-- checked (they remain valid until expiry). Set AUTH_REQUIRED off first if you
-- are unwinding the whole login feature.
--
-- SECURITY: after this rollback, _sv_expr() synthesizes version 0 again, so a
-- cookie stolen BEFORE a password reset (version 0) becomes valid once more,
-- while the legitimate post-reset cookie (version 1) is rejected. Turning
-- AUTH_REQUIRED off does NOT invalidate client-side cookies. So before
-- re-enabling auth after this rollback, ROTATE APP_SECRET_KEY (which changes the
-- cookie signature and revokes every existing session) — do not rely on the
-- dropped counter for revocation.
-- ============================================================================
BEGIN;

ALTER TABLE app_users DROP COLUMN IF EXISTS session_version;

DELETE FROM schema_migrations WHERE version = '010';

COMMIT;
