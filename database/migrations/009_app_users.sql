-- ============================================================================
-- 009_app_users.sql
-- Dashboard login: a small user table for username/password authentication.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`
-- (or `python database/migrate.py up`).
--
-- WHY
-- ---
-- The dashboard is a private, single-owner app that has so far been open to
-- anyone with the URL. This adds accounts so the UI can sit behind a login and
-- the Hyperliquid execute controls can be driven by a signed-in admin instead
-- of only a shared token.
--
-- SAFETY
-- ------
-- Purely additive: one new table and its indexes. No existing table is read,
-- rewritten or touched, so this cannot affect signals, targets or any history.
-- Enforcement is a separate, default-OFF switch (AUTH_REQUIRED) in the app, so
-- creating the table does not by itself lock anyone out. The whole thing runs
-- in ONE transaction; a failure leaves schema_migrations unchanged.
-- ============================================================================
BEGIN;

-- gen_random_uuid() comes from pgcrypto (already requested by migration 001).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS app_users (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username       TEXT         NOT NULL,
    password_hash  TEXT         NOT NULL,
    -- 'admin' can manage users and drive the Hyperliquid controls; 'user' can
    -- view the dashboard only. Kept as free text with a CHECK so a later role
    -- is a one-line change, not a type migration.
    role           TEXT         NOT NULL DEFAULT 'user'
                                CHECK (role IN ('user', 'admin')),
    disabled       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

-- Usernames are case-insensitive and unique: "Alice" and "alice" are one login.
CREATE UNIQUE INDEX IF NOT EXISTS app_users_username_lower_uidx
    ON app_users (lower(username));

-- ── Record this migration ───────────────────────────────────────────────────
INSERT INTO schema_migrations (version, description)
VALUES ('009', 'dashboard login: app_users (username/password, roles)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
