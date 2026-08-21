-- ============================================================================
-- 008_market_metric_history.sql
-- A durable daily record of market-state metrics — funding, open interest,
-- Fear & Greed, and BTC on-chain cycle reads (MVRV, SOPR, realized price) — so
-- the market's backdrop BETWEEN trades becomes a longitudinal dataset, not
-- something recomputed and thrown away on every request.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- Every signal already snapshots ~50 indicators at DECISION time
-- (signal_indicator_snapshots). What no table holds is the CONTINUOUS daily
-- series — the regime a trade lived through, and how funding / F&G / MVRV
-- evolved when no trade fired. That series is what lets the postmortem ask "do
-- my signals lose when funding is extreme / F&G is greedy / MVRV is hot", which
-- a per-trade snapshot alone cannot answer.
--
-- LONG/NARROW BY DESIGN. One row per (scope, metric, day) rather than a wide
-- column-per-metric table, so a new metric is a new `metric` value — never a
-- migration. `scope` is the asset (BTC/ETH) or GLOBAL for market-wide reads.
--
-- A RECORD, NEVER AN INPUT. Nothing in the scoring path reads this table; live
-- signals use the fresh figures. Like the postmortem, the pattern log and the
-- ETF history, it is for after-the-fact analysis only.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS market_metric_daily (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which deployment recorded it (DATABASE_URL is shared across envs).
    environment   text        NOT NULL DEFAULT 'production',

    -- The day the reading is FOR (UTC), not when it was recorded.
    metric_date   date        NOT NULL,

    -- What the reading is about: an upper-case asset (BTC, ETH) or GLOBAL for a
    -- market-wide series (e.g. Fear & Greed).
    scope         text        NOT NULL,

    -- The metric key: funding_rate | open_interest | fear_greed | mvrv | sopr |
    -- realized_price | ... Adding one is a new value here, never a schema change.
    metric        text        NOT NULL,

    -- The numeric reading. Non-numeric context (a label, a zone) goes in detail.
    value         numeric(30,8) NOT NULL,

    -- Bounded context, allow-listed by the writer — never a raw provider payload.
    detail        jsonb       NOT NULL DEFAULT '{}'::jsonb,

    source        text,

    first_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT market_metric_scope_upper_chk CHECK (scope = upper(scope)),
    CONSTRAINT market_metric_daily_unique
        UNIQUE (environment, scope, metric, metric_date)
);

-- The analysis query: this scope + metric, as a time series (newest first, and
-- range scans for a window).
CREATE INDEX IF NOT EXISTS market_metric_daily_series_idx
    ON market_metric_daily (environment, scope, metric, metric_date DESC);

INSERT INTO schema_migrations (version, description)
VALUES ('008', 'market_metric_daily — durable daily market-state metric history')
ON CONFLICT (version) DO NOTHING;

COMMIT;
