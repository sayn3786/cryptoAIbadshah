-- ============================================================================
-- 010_app_users_session_version.sql
-- Session revocation: bump a per-user counter so a password change invalidates
-- every session issued before it.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `python database/migrate.py up`.
--
-- WHY
-- ---
-- Sessions are stateless signed cookies. Resetting a password changes only the
-- stored hash, so a cookie stolen before the reset stays valid for the full
-- session lifetime — useless for recovering a compromised account. This adds a
-- version the login bakes into the cookie and every request revalidates: a
-- password change increments it, and older cookies (carrying the old version)
-- stop matching and are treated as signed out.
--
-- SAFETY
-- ------
-- Purely additive: one column with a default. No existing row is read or
-- rewritten (existing rows take session_version = 0). The app reads the column
-- defensively, so it works whether or not this migration has run yet.
-- ============================================================================
BEGIN;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migrations (version, description)
VALUES ('010', 'app_users.session_version — revoke sessions on password change')
ON CONFLICT (version) DO NOTHING;

COMMIT;
