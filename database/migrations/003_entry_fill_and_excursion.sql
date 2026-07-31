-- ============================================================================
-- 003_entry_fill_and_excursion.sql
-- A published signal is a WORKING ORDER until price reaches its entry, and every
-- trade records how far it ran for and against you.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- 1. The monitor never read entry_price. A signal was treated as FILLED the
--    instant it was published, so a setup whose entry price never traded still
--    recorded a win or a loss. Entries are frequently away from the live price
--    (a limit order into a retrace), so this was not a rare case — and every
--    statistic built on it, win rate included, was measuring the wrong set of
--    trades. Orders now start PENDING and become OPEN only when price touches
--    the entry; one that never fills is CANCELLED and never counted as a trade.
--
-- 2. Outcomes said what happened at the end and nothing about the journey. MFE
--    and MAE — the furthest a trade ran in favour and against — are the standard
--    diagnostic for whether stops are too tight or targets too far. A loss that
--    first ran +1.8R is a stop-placement problem, not a signal problem.
--
-- SAFETY
-- ------
-- Additive. Four nullable columns, plus two widened CHECK constraints (a wider
-- CHECK accepts everything the old one did). Existing rows are untouched and
-- remain valid: they keep their status, and NULL excursions honestly mean "not
-- measured" rather than zero.
--
-- ORDER OF OPERATIONS  ***  DEPLOY THE CODE FIRST  ***
-- ----------------------------------------------------
-- Same rule as 002. Old code writes status='OPEN' directly, which the widened
-- CHECK still accepts, so migrating first does not break writes — but the new
-- code will not see the columns until they exist. Deploy, then migrate, then
-- redeploy or wait for a cold start.
-- ============================================================================

BEGIN;

-- ── Entry fill ──────────────────────────────────────────────────────────────
-- NULL until price actually reaches the entry. A PENDING signal has no
-- position, so it has no P/L and belongs in no performance statistic.
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS entry_filled_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS entry_fill_price NUMERIC(30,12);

-- ── Excursion ───────────────────────────────────────────────────────────────
-- Percentages, signed in the TRADE's favour: mfe_pct is how far it ran your way
-- at best, mae_pct how far against you at worst (recorded as a negative). Both
-- measured from the fill, never from the signal price — an unfilled order has no
-- excursion to speak of.
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS mfe_pct NUMERIC(18,8),
    ADD COLUMN IF NOT EXISTS mae_pct NUMERIC(18,8);

-- ── Widen the state machine ─────────────────────────────────────────────────
-- PENDING is a new START state, not a new end state. Terminal statuses are
-- unchanged, so nothing that was final becomes re-openable.
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_status_chk;
ALTER TABLE signals
    ADD CONSTRAINT signals_status_chk
    CHECK (status IN ('PENDING', 'OPEN', 'PARTIAL_TP', 'TP_HIT', 'SL_HIT',
                      'CLOSED', 'EXPIRED', 'CANCELLED'));

-- The fill is an event like any other, so the trail can answer "when did this
-- actually become a position?" without inferring it from timestamps.
ALTER TABLE signal_events DROP CONSTRAINT IF EXISTS signal_events_type_chk;
ALTER TABLE signal_events
    ADD CONSTRAINT signal_events_type_chk
    CHECK (event_type IN ('CREATED', 'ENTRY_FILLED', 'TARGET_HIT',
                          'STOP_LOSS_HIT', 'CLOSED', 'EXPIRED', 'CANCELLED',
                          'ANALYSIS_ADDED', 'ARCHIVED'));

-- ── Finding working orders ──────────────────────────────────────────────────
-- The monitor asks for "everything not yet resolved" on every run.
CREATE INDEX IF NOT EXISTS signals_pending_idx
    ON signals (status, generated_at DESC)
    WHERE status IN ('PENDING', 'OPEN', 'PARTIAL_TP');

-- ── Record this migration ───────────────────────────────────────────────────
INSERT INTO schema_migrations (version, description)
VALUES ('003', 'entry fill state (PENDING) and MFE/MAE excursion columns')
ON CONFLICT (version) DO NOTHING;

COMMIT;
