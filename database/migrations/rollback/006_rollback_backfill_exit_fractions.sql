-- ============================================================================
-- 006_rollback_backfill_exit_fractions.sql   *** REVIEW ONLY ***
--
-- Restores the exit_fraction and realized_return_pct values that 006 replaced,
-- from the snapshot 006 took before touching anything. This is a genuine
-- restore, not a recomputation: every row goes back to the exact value it held,
-- including NULL.
--
-- It is therefore SAFE in a way most rollbacks are not — but it is still a
-- write over live outcome data, so read it before running it.
--
-- IMPORTANT
-- ---------
-- This restores ONLY rows 006 snapshotted, i.e. rows that existed and had a
-- NULL share when 006 ran. Signals published after 006 are untouched: they
-- were never in the snapshot, and their shares were written by the application
-- rather than by the migration. Rolling back does NOT return the database to
-- "no shares anywhere" — that state stopped existing when the application
-- started writing them, and recreating it would corrupt current data.
--
-- The snapshot tables are dropped last. Once they are gone the restore cannot
-- be repeated, so if you are unsure, run the SELECTs at the bottom of 006
-- first.
-- ============================================================================

BEGIN;

UPDATE signals s
SET    realized_return_pct = b.realized_return_pct,
       updated_at = now()
FROM   backfill_006_signal_before b
WHERE  s.id = b.signal_id
  AND  s.realized_return_pct IS DISTINCT FROM b.realized_return_pct;

UPDATE signal_targets t
SET    exit_fraction = b.exit_fraction
FROM   backfill_006_target_before b
WHERE  t.signal_id = b.signal_id
  AND  t.target_number = b.target_number
  AND  t.exit_fraction IS DISTINCT FROM b.exit_fraction;

DROP TABLE IF EXISTS backfill_006_signal_before;
DROP TABLE IF EXISTS backfill_006_target_before;

DELETE FROM schema_migrations WHERE version = '006';

COMMIT;
