-- ============================================================================
-- 011_app_users_user_admin_role.sql
-- Add the 'user_admin' role for separation of duties.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `python database/migrate.py up`.
--
-- WHY
-- ---
-- Three roles instead of two:
--   * user        — dashboard charts/analysis only
--   * user_admin  — manages user accounts ONLY (no charts, trades, HL)
--   * admin       — the operator: trades, publish, Hyperliquid (NO user mgmt)
-- This widens the role CHECK to accept 'user_admin'. Existing 'user'/'admin'
-- rows are unaffected.
--
-- SAFETY
-- ------
-- Additive: it only RELAXES the CHECK constraint (every previously valid role is
-- still valid). No row is read or rewritten. Runs in one transaction.
-- ============================================================================
BEGIN;

-- The CHECK from migration 009 was created inline, so Postgres named it
-- app_users_role_check. Drop and re-add it widened; IF EXISTS keeps this safe if
-- the name ever differed.
ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check;
ALTER TABLE app_users
    ADD CONSTRAINT app_users_role_check CHECK (role IN ('user', 'user_admin', 'admin'));

INSERT INTO schema_migrations (version, description)
VALUES ('011', 'app_users: add user_admin role (separation of duties)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
