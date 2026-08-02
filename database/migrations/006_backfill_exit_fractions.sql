-- ============================================================================
-- 006_backfill_exit_fractions.sql
-- Score the history the way the advice was actually given.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- signal_targets.exit_fraction was added by migration 004 and read from day
-- one, but nothing wrote it until 2026-08-02. Every row before that is NULL,
-- and the reader's documented fallback is an even split — so the whole outcome
-- history was scored as thirds while the dashboard told the reader to sell 50%
-- at TP1, 30% at TP2, 20% at TP3.
--
-- Signals published from 2026-08-02 02:30 UTC onward carry the real shares.
-- Leaving history alone means expectancy queries silently mix two scoring
-- conventions, and every version comparison across that boundary is wrong in a
-- direction nobody can see. This migration removes that seam.
--
-- WHAT IT CHANGES
-- ---------------
-- 1. signal_targets.exit_fraction — filled in as 0.5 / 0.3 / 0.2 for every
--    ladder of EXACTLY three rungs that still has NULLs. `WHERE exit_fraction
--    IS NULL` is self-limiting: the application always writes the shares now,
--    so only pre-fix rows can match.
--
-- 2. signals.realized_return_pct — RECOMPUTED for closed three-rung trades.
--    This is the half that actually matters. Filling in the fractions alone
--    would change nothing for a trade that is already closed, because its
--    realised return was computed and stored at close time. Only trades that
--    banked at least one target can move; a trade that never reached TP1 has
--    the whole position closing at close_price under either convention and its
--    number is untouched.
--
-- Nothing else is modified. No row is deleted, no status changes, no signal
-- reopens. The lifecycle state machine is not involved.
--
-- SAFETY
-- ------
-- * Wrapped in a single transaction: it fully applies or does nothing.
-- * BEFORE values are snapshotted into two backup tables, so the rollback
--   restores exactly what was there rather than guessing. Without that, a
--   rollback could not tell a backfilled row from one the application wrote.
-- * Deterministic and idempotent: re-running recomputes the same numbers from
--   the same inputs. The backup tables are only written on the FIRST run
--   (ON CONFLICT DO NOTHING), so a second run cannot overwrite the originals
--   with already-migrated values.
-- * Ladders that are not exactly three rungs are left entirely alone — there
--   is no published plan for them, and NULL correctly means "split evenly".
--
-- WHAT IT CANNOT FIX
-- ------------------
-- Republished duplicates. The ONDO cluster of 2026-07-30 is one setup recorded
-- six times across two strategy versions; rescoring each copy correctly still
-- leaves six copies. Sample-size caveats on the early history survive this.
-- ============================================================================

BEGIN;

-- ── Snapshot: what the rollback restores ────────────────────────────────────

CREATE TABLE IF NOT EXISTS backfill_006_target_before (
    signal_id       uuid    NOT NULL,
    target_number   integer NOT NULL,
    exit_fraction   numeric(9,6),
    PRIMARY KEY (signal_id, target_number)
);

CREATE TABLE IF NOT EXISTS backfill_006_signal_before (
    signal_id           uuid PRIMARY KEY,
    realized_return_pct numeric(18,8)
);

-- Every three-rung ladder carrying a NULL share. Captured before anything is
-- written, and only once — a re-run must not snapshot migrated values.
INSERT INTO backfill_006_target_before (signal_id, target_number, exit_fraction)
SELECT t.signal_id, t.target_number, t.exit_fraction
FROM   signal_targets t
WHERE  t.signal_id IN (
           SELECT signal_id FROM signal_targets
           GROUP BY signal_id HAVING count(*) = 3
       )
  AND  EXISTS (
           SELECT 1 FROM signal_targets x
           WHERE x.signal_id = t.signal_id AND x.exit_fraction IS NULL
       )
ON CONFLICT (signal_id, target_number) DO NOTHING;

INSERT INTO backfill_006_signal_before (signal_id, realized_return_pct)
SELECT s.id, s.realized_return_pct
FROM   signals s
WHERE  s.realized_return_pct IS NOT NULL
  AND  s.id IN (SELECT signal_id FROM backfill_006_target_before)
ON CONFLICT (signal_id) DO NOTHING;

-- ── 1. The shares the reader was actually shown ─────────────────────────────

UPDATE signal_targets t
SET    exit_fraction = CASE t.target_number
                           WHEN 1 THEN 0.5
                           WHEN 2 THEN 0.3
                           WHEN 3 THEN 0.2
                       END
WHERE  t.exit_fraction IS NULL
  AND  t.signal_id IN (
           SELECT signal_id FROM signal_targets
           GROUP BY signal_id HAVING count(*) = 3
       );

-- ── 2. Rescore the closed trades ────────────────────────────────────────────
--
-- Mirrors signal_store.weighted_return exactly: each rung already hit takes its
-- share at the price it was hit, and whatever is left closes at close_price.
-- A leg is (exit - entry) / entry for a LONG and (entry - exit) / entry for a
-- SHORT, in percent.
--
-- The remainder is only priced when close_price exists. If it does not, the
-- realised figure stays whatever was already banked — inventing a price for the
-- open part of a position is how a measurement becomes a fabrication.

WITH legs AS (
    SELECT s.id                              AS signal_id,
           s.direction,
           s.entry_price,
           s.close_price,
           sum(t.exit_fraction) FILTER (WHERE t.hit_at IS NOT NULL) AS taken,
           sum(
               t.exit_fraction * (
                   CASE WHEN s.direction = 'LONG'
                        THEN (COALESCE(t.hit_price, t.target_price) - s.entry_price)
                        ELSE (s.entry_price - COALESCE(t.hit_price, t.target_price))
                   END / s.entry_price * 100
               )
           ) FILTER (WHERE t.hit_at IS NOT NULL)                    AS banked
    FROM   signals s
    JOIN   signal_targets t ON t.signal_id = s.id
    WHERE  s.realized_return_pct IS NOT NULL
      AND  s.entry_price > 0
      AND  s.id IN (SELECT signal_id FROM backfill_006_target_before)
    GROUP  BY s.id, s.direction, s.entry_price, s.close_price
),
rescored AS (
    SELECT signal_id,
           round(
               COALESCE(banked, 0)
               + CASE
                     WHEN COALESCE(taken, 0) < 1 AND close_price IS NOT NULL
                     THEN (1 - COALESCE(taken, 0)) * (
                              CASE WHEN direction = 'LONG'
                                   THEN (close_price - entry_price)
                                   ELSE (entry_price - close_price)
                              END / entry_price * 100
                          )
                     ELSE 0
                 END
           , 8) AS realized
    FROM   legs
    -- Nothing banked and nothing to close the remainder at is not a number.
    WHERE  COALESCE(taken, 0) > 0 OR close_price IS NOT NULL
)
UPDATE signals s
SET    realized_return_pct = r.realized,
       updated_at = now()
FROM   rescored r
WHERE  s.id = r.signal_id
  AND  s.realized_return_pct IS DISTINCT FROM r.realized;

INSERT INTO schema_migrations (version, description)
VALUES ('006', 'backfill exit_fraction 0.5/0.3/0.2 and rescore closed three-rung trades')
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- ── After running, check these ──────────────────────────────────────────────
--
--   -- No three-rung ladder should have a NULL share left.
--   SELECT count(*) FROM signal_targets t
--   WHERE t.exit_fraction IS NULL
--     AND t.signal_id IN (SELECT signal_id FROM signal_targets
--                         GROUP BY signal_id HAVING count(*) = 3);
--
--   -- What actually moved, and by how much.
--   SELECT s.symbol, s.direction, s.strategy_version,
--          b.realized_return_pct AS before, s.realized_return_pct AS after
--   FROM   signals s
--   JOIN   backfill_006_signal_before b ON b.signal_id = s.id
--   WHERE  s.realized_return_pct IS DISTINCT FROM b.realized_return_pct
--   ORDER  BY s.closed_at;
--
--   -- Only trades that banked a target should appear above. If a trade with no
--   -- target hits changed, something is wrong — stop and roll back.
