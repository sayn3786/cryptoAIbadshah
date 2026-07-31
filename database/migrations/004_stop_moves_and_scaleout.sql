-- ============================================================================
-- 004_stop_moves_and_scaleout.sql
-- A stop can move, and a trade can be taken off in pieces.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- 1. STOP MOVES. The tracker tells you to move the stop to breakeven once TP1
--    is banked — and then the record ignored its own advice. A trade that took
--    TP1 and reversed was booked as a FULL stop loss, as though the stop had
--    never moved. The original stop_loss is now immutable history and
--    current_stop_loss is where the stop actually sits; every move is an event.
--
-- 2. SCALE-OUT. Reaching TP1 banks part of the position. The realised return was
--    taken from the FINAL exit alone, so that same trade recorded the full loss
--    and none of the profit it had already taken. exit_fraction records how much
--    of the position each target takes, so the realised return can be the
--    weighted average of what actually happened.
--
-- SAFETY
-- ------
-- Additive. Two nullable columns, one column with a default on an existing
-- table, and a widened event CHECK (which accepts everything the old one did).
-- No data is read, rewritten or moved. Existing rows keep their stop and their
-- recorded returns: current_stop_loss NULL means "never moved", which is true
-- of every row written before this.
--
-- ORDER OF OPERATIONS  ***  DEPLOY THE CODE FIRST  ***
-- ----------------------------------------------------
-- Same rule as 002 and 003. The code probes for these columns and falls back to
-- the single-stop, final-exit behaviour when they are absent, so deploying
-- first cannot break anything.
-- ============================================================================

BEGIN;

-- ── Where the stop actually is ──────────────────────────────────────────────
-- NULL means it has never been moved, so the original stop_loss still applies.
-- stop_loss itself is never rewritten: it is what the strategy decided, and
-- overwriting it would destroy the only record of the risk originally taken.
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS current_stop_loss NUMERIC(30,12),
    ADD COLUMN IF NOT EXISTS stop_moved_at     TIMESTAMPTZ;

-- ── How much of the position each target takes ──────────────────────────────
-- Defaults to NULL, meaning "split the position evenly across the targets" —
-- the sane default for a 3-target ladder and what the recommendations imply.
-- An explicit value lets a strategy take half at TP1 and let the rest run.
ALTER TABLE signal_targets
    ADD COLUMN IF NOT EXISTS exit_fraction NUMERIC(9,6);

ALTER TABLE signal_targets DROP CONSTRAINT IF EXISTS signal_targets_fraction_chk;
ALTER TABLE signal_targets
    ADD CONSTRAINT signal_targets_fraction_chk
    CHECK (exit_fraction IS NULL OR (exit_fraction > 0 AND exit_fraction <= 1));

-- ── The move is an event ────────────────────────────────────────────────────
-- So the trail can answer "when did this become a risk-free trade?" instead of
-- leaving it to be inferred from a timestamp.
ALTER TABLE signal_events DROP CONSTRAINT IF EXISTS signal_events_type_chk;
ALTER TABLE signal_events
    ADD CONSTRAINT signal_events_type_chk
    CHECK (event_type IN ('CREATED', 'ENTRY_FILLED', 'TARGET_HIT',
                          'STOP_MOVED', 'STOP_LOSS_HIT', 'CLOSED', 'EXPIRED',
                          'CANCELLED', 'ANALYSIS_ADDED', 'ARCHIVED'));

-- ── Record this migration ───────────────────────────────────────────────────
INSERT INTO schema_migrations (version, description)
VALUES ('004', 'stop movement (current_stop_loss) and per-target exit fractions')
ON CONFLICT (version) DO NOTHING;

COMMIT;
