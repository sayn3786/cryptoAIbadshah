-- ============================================================================
-- 002_signal_environment.sql
-- Tag every signal with the deployment that wrote it.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- DATABASE_URL is scoped to All Environments in Vercel, so a preview deploy
-- writes into the same Neon branch as production. Two problems follow:
--
--   1. Preview rows are indistinguishable from real ones.
--   2. Worse — the idempotency index does not know about environments, so a
--      preview that evaluated a candle first CLAIMS it, and production's own
--      write for that candle then returns as a duplicate and is silently
--      dropped. Preview traffic can suppress production data.
--
-- Both are fixed by making `environment` part of the row and part of the key.
--
-- SAFETY
-- ------
-- Additive with one deliberate exception: the old idempotency index is replaced
-- by a wider one. That is a widening — every pair of rows the old index kept
-- apart is still kept apart, so it cannot introduce a duplicate. No data is
-- read, rewritten, dropped or moved. Existing rows all take the default
-- 'production', which is what they are.
--
-- The whole thing runs in ONE transaction, so a failure leaves the old index in
-- place and schema_migrations unchanged.
--
-- ORDER OF OPERATIONS
-- -------------------
-- Application code probes for this column and writes without it when it is
-- absent, so migrating BEFORE or AFTER deploying both work and neither breaks
-- writes. After migrating, redeploy (or wait for a cold start) so running
-- instances pick the column up.
-- ============================================================================

BEGIN;

-- ── The label ───────────────────────────────────────────────────────────────
-- NOT NULL with a default so existing rows are valid immediately and no
-- backfill pass is needed. 'production' is the honest default: every row that
-- exists before this migration was written by production.
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'production';

-- Bounded, lowercase slug — mirrors SLUG_RE in backend/deploy_context.py. This
-- stops a stray env var from filling the column with junk, and keeps the value
-- safe to interpolate into a label anywhere.
-- conrelid, not just conname: constraint names are unique per TABLE, not per
-- database. Matching on the name alone means any other schema that happens to
-- have a constraint of the same name makes this one silently skip, leaving the
-- column unguarded. (Caught by a test that inserted 'Preview; DROP TABLE' and
-- watched it succeed.)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = to_regclass('signals')
                     AND conname  = 'signals_environment_chk') THEN
        ALTER TABLE signals
            ADD CONSTRAINT signals_environment_chk
            CHECK (environment ~ '^[a-z0-9][a-z0-9_-]{0,31}$');
    END IF;
END $$;

-- ── Idempotency, now per environment ────────────────────────────────────────
-- Create the wider index FIRST, then drop the narrower one, so the table is
-- never left without a uniqueness guarantee inside this transaction.
CREATE UNIQUE INDEX IF NOT EXISTS signals_idempotency_env_uidx
    ON signals (environment, symbol, exchange, timeframe,
                strategy_name, strategy_version, candle_close_time);

DROP INDEX IF EXISTS signals_idempotency_uidx;

-- ── Filtering by environment ────────────────────────────────────────────────
-- Reads default to "only my own environment", which is an equality filter on a
-- low-cardinality column combined with generated_at ordering.
CREATE INDEX IF NOT EXISTS signals_environment_generated_idx
    ON signals (environment, generated_at DESC);

-- ── Record this migration ───────────────────────────────────────────────────
INSERT INTO schema_migrations (version, description)
VALUES ('002', 'tag signals with the writing deployment; idempotency is per environment')
ON CONFLICT (version) DO NOTHING;

COMMIT;
