-- ============================================================================
-- 007_etf_flow_history.sql
-- A durable local record of daily spot-ETF net flows, so 6-month / 1-year
-- analysis reads our own history instead of re-fetching a provider window that
-- shifts, lags, and only reaches back so far.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Console -> SQL Editor, or `psql "$DATABASE_URL" -f <file>`.
--
-- WHY
-- ---
-- ETF flow data is fetched live from SoSoValue (see backend/etf_flows.py). The
-- provider serves a rolling window (~300 days) that can lag by a trading day and
-- occasionally revises a provisional figure. That is fine for "today's flow" but
-- useless for "how much did BTC ETFs actually buy over the last year" once the
-- window rolls past your horizon. This table snapshots each day's figure the
-- first time it is seen and keeps it, so the historical series grows past the
-- provider's window and survives any change on their side.
--
-- A RECORD, NEVER AN INPUT. Like pattern_events and the postmortem, nothing in
-- the scoring path reads this table. Live signals still read the fresh provider
-- figure; this is for after-the-fact analysis only.
--
-- WHAT IT CHANGES
-- ---------------
-- Adds one table, etf_flow_daily, keyed on (environment, symbol, flow_date) so
-- the daily snapshot cron is idempotent: re-recording the same day updates the
-- value (provider revisions win) without creating a duplicate. No existing table
-- is touched, and no foreign key points at it.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS etf_flow_daily (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which deployment recorded it. DATABASE_URL is shared across Vercel
    -- environments, so without this a preview snapshot would be
    -- indistinguishable from production's.
    environment   text        NOT NULL DEFAULT 'production',

    -- Upper-case asset ticker (BTC, ETH). CHECK enforces upper-case.
    symbol        text        NOT NULL,

    -- The trading day the flow is FOR (UTC), not when it was recorded. This is
    -- the natural key: one net figure per asset per day.
    flow_date     date        NOT NULL,

    -- Daily net flow in USD: positive = net inflow (buying), negative = net
    -- outflow (selling). Stored raw (not millions) so nothing is lost.
    net_usd       numeric(20,2) NOT NULL,

    -- Which provider the figure came from (sosovalue / coinglass).
    source        text        NOT NULL,

    -- When we first recorded this day, and when we last revised it. A revised
    -- provisional figure bumps updated_at; first_seen_at never moves.
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT etf_flow_daily_symbol_upper_chk CHECK (symbol = upper(symbol)),
    CONSTRAINT etf_flow_daily_unique UNIQUE (environment, symbol, flow_date)
);

-- The analysis query: this asset, newest first (and range scans for a window).
CREATE INDEX IF NOT EXISTS etf_flow_daily_lookup_idx
    ON etf_flow_daily (environment, symbol, flow_date DESC);

INSERT INTO schema_migrations (version, description)
VALUES ('007', 'etf_flow_daily — durable daily spot-ETF net-flow history')
ON CONFLICT (version) DO NOTHING;

COMMIT;
