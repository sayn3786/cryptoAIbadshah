-- ============================================================================
-- 011_rollback_app_users_user_admin_role.sql  (REVIEW ONLY — never run from migrate.py)
-- Narrows the role CHECK back to ('user','admin'). This FAILS if any row still
-- has role='user_admin' — reassign those accounts first (to 'user' or 'admin')
-- or the ADD CONSTRAINT is rejected and the transaction rolls back.
-- ============================================================================
BEGIN;

-- Guard: refuse to narrow while user_admin rows exist (they would violate the
-- new constraint). Reassign them first.
-- (If you intend to force it, UPDATE app_users SET role='user' WHERE role='user_admin';)
ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check;
ALTER TABLE app_users
    ADD CONSTRAINT app_users_role_check CHECK (role IN ('user', 'admin'));

DELETE FROM schema_migrations WHERE version = '011';

COMMIT;
