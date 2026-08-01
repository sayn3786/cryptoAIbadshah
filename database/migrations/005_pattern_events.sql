-- ============================================================================
-- 005_pattern_events.sql
-- A log of what the detectors saw, and when.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- Pattern state was entirely ephemeral. Every detector recomputes from candles
-- on each request, so "this divergence was confirmed on the 4pm bar and expired
-- eleven candles later" existed only for as long as those candles stayed inside
-- the lookback window. Once they aged out, there was no way to ask whether a
-- pattern had ever fired, how long it lasted, or whether the ones that fired
-- were followed by anything.
--
-- This table answers that. One row per (pattern, status, bar): forming at one
-- bar, confirmed at the next, expired later — the lifecycle as it happened.
--
-- WHAT THIS IS NOT
-- ----------------
-- It is a LOG, never an input. The detectors read candles and are the only
-- source of truth about pattern state; if this table ever disagreed with a
-- recomputation, the recomputation is right. Nothing in the scoring path reads
-- from here, deliberately — the same rule that keeps postmortem data from
-- modifying live strategy parameters.
--
-- SAFETY
-- ------
-- * Creates one new table and its indexes. Touches NOTHING that exists.
-- * Wrapped in a single transaction: it fully applies or does nothing.
-- * Re-running is safe (IF NOT EXISTS throughout, version row ON CONFLICT).
-- * No backfill. Rows appear from the next publication bar onward; history
--   before this migration was never recorded and cannot be invented.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pattern_events (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which deployment observed it. DATABASE_URL is shared across Vercel
    -- environments, so without this a preview deploy's observations would be
    -- indistinguishable from production's.
    environment         text        NOT NULL DEFAULT 'production',

    symbol              text        NOT NULL,
    timeframe           text        NOT NULL,

    -- rsi_divergence | choch | liquidity_grab | engulfing | flag | triangle |
    -- acc_eql_fvg — matches the keys in backend/lifecycle.py FRESH_BARS.
    pattern_kind        text        NOT NULL,
    -- The detector's own label: bullish, hidden_bearish, falling_wedge, ...
    pattern_type        text,
    direction           text,

    status              text        NOT NULL,
    -- The CLOSED candle this observation belongs to. Every row is keyed on a
    -- bar, never on wall-clock time, so re-running a publication records
    -- nothing new.
    candle_close_time   timestamptz NOT NULL,

    age_candles         integer,
    fresh_bars          integer,
    freshness           numeric(6,4),
    strength            numeric(18,8),

    -- Bounded detail: pivot prices, levels, the description. Allow-listed by
    -- the writer — never a raw provider payload.
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,

    observed_at         timestamptz NOT NULL DEFAULT now(),

    -- Derived from the bar, not the clock: (environment, symbol, timeframe,
    -- kind, status, candle_close_time). Re-observing the same state on the same
    -- bar is not a new event.
    idempotency_key     text        NOT NULL,

    CONSTRAINT pattern_events_status_chk
        CHECK (status IN ('forming', 'confirmed', 'expired', 'invalidated')),
    CONSTRAINT pattern_events_symbol_upper_chk
        CHECK (symbol = upper(symbol)),
    CONSTRAINT pattern_events_freshness_chk
        CHECK (freshness IS NULL OR (freshness >= 0 AND freshness <= 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS pattern_events_idem_idx
    ON pattern_events (idempotency_key);

-- The query the UI makes: this symbol, this timeframe, newest first.
CREATE INDEX IF NOT EXISTS pattern_events_lookup_idx
    ON pattern_events (symbol, timeframe, candle_close_time DESC);

-- "How did divergences behave this month" — the analysis question.
CREATE INDEX IF NOT EXISTS pattern_events_kind_idx
    ON pattern_events (pattern_kind, status, candle_close_time DESC);

INSERT INTO schema_migrations (version, description)
VALUES ('005', 'pattern_events — lifecycle log for detector observations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
