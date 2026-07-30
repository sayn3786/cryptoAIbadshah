-- ============================================================================
-- 001_initial_signal_schema.sql
-- CryptoMonk — persistent signal tracking, phase 1.
--
-- Target: Neon Postgres (PostgreSQL 16).
-- Run ONCE, via Neon Dashboard -> Query, or `psql "$DATABASE_URL" -f <file>`.
--
-- This migration is ADDITIVE ONLY. It creates new tables and never drops,
-- truncates, renames or alters anything that already exists. Every object is
-- guarded with IF NOT EXISTS so a partial re-run is safe.
--
-- The whole thing runs in ONE transaction: if any statement fails, nothing is
-- applied and schema_migrations stays unchanged, so it can be corrected and
-- re-run cleanly.
-- ============================================================================

BEGIN;

-- gen_random_uuid() ships in core since PG13, but pgcrypto is requested
-- explicitly so the migration also works on older/managed images that expose
-- it only through the extension. Neon supports pgcrypto.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Migration bookkeeping ───────────────────────────────────────────────────
-- Created here because the project had no migration framework before this
-- change. Anything that already tracks versions can keep doing so; this table
-- is only written by our own migration runner.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT        PRIMARY KEY,
    description TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── signals ─────────────────────────────────────────────────────────────────
-- One row per PUBLISHED signal. Rejected candidates are never stored here.
--
-- Prices use numeric(30,12), never float: binary floating point cannot
-- represent decimal prices exactly, and silent rounding on an entry or a stop
-- is a real-money error.
CREATE TABLE IF NOT EXISTS signals (
    id                  UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              TEXT           NOT NULL,
    exchange            TEXT           NOT NULL,
    timeframe           TEXT           NOT NULL,
    direction           TEXT           NOT NULL,
    strategy_name       TEXT           NOT NULL,
    strategy_version    TEXT           NOT NULL,

    candle_open_time    TIMESTAMPTZ    NOT NULL,
    candle_close_time   TIMESTAMPTZ    NOT NULL,
    generated_at        TIMESTAMPTZ    NOT NULL,

    entry_price         NUMERIC(30,12) NOT NULL,
    stop_loss           NUMERIC(30,12) NOT NULL,
    confidence_score    NUMERIC(10,4),

    status              TEXT           NOT NULL,
    closed_at           TIMESTAMPTZ,
    close_price         NUMERIC(30,12),
    realized_return_pct NUMERIC(18,8),
    close_reason        TEXT,

    archived_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT signals_direction_chk
        CHECK (direction IN ('LONG', 'SHORT')),
    CONSTRAINT signals_status_chk
        CHECK (status IN ('OPEN', 'PARTIAL_TP', 'TP_HIT', 'SL_HIT',
                          'CLOSED', 'EXPIRED', 'CANCELLED')),
    CONSTRAINT signals_symbol_upper_chk
        CHECK (symbol = upper(symbol)),
    CONSTRAINT signals_candle_window_chk
        CHECK (candle_close_time > candle_open_time),
    CONSTRAINT signals_entry_positive_chk
        CHECK (entry_price > 0),
    CONSTRAINT signals_stop_positive_chk
        CHECK (stop_loss > 0),
    CONSTRAINT signals_close_price_positive_chk
        CHECK (close_price IS NULL OR close_price > 0)
);

-- Idempotency. One published signal per (instrument, strategy, closed candle).
--
-- Deliberately does NOT include direction: without that, a re-evaluation that
-- flipped LONG/SHORT would insert a SECOND row for the same candle and the
-- same strategy, leaving two contradictory live signals. Excluding direction
-- makes the first published decision for that candle the only one.
--
-- Because candle_close_time is part of the key, the SAME token still gets a
-- fresh signal on the next closed candle, several times a day, and different
-- strategy_versions are tracked independently.
CREATE UNIQUE INDEX IF NOT EXISTS signals_idempotency_uidx
    ON signals (symbol, exchange, timeframe,
                strategy_name, strategy_version, candle_close_time);

-- ── signal_targets ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_targets (
    id            UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id     UUID           NOT NULL
                                 REFERENCES signals(id) ON DELETE CASCADE,
    target_number INTEGER        NOT NULL,
    target_price  NUMERIC(30,12) NOT NULL,
    hit_at        TIMESTAMPTZ,
    hit_price     NUMERIC(30,12),
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT signal_targets_number_chk    CHECK (target_number > 0),
    CONSTRAINT signal_targets_price_chk     CHECK (target_price > 0),
    CONSTRAINT signal_targets_hit_price_chk CHECK (hit_price IS NULL OR hit_price > 0),
    CONSTRAINT signal_targets_unique        UNIQUE (signal_id, target_number)
);

-- ── signal_indicator_snapshots ──────────────────────────────────────────────
-- Exactly ONE decision-time snapshot per signal (enforced by the UNIQUE on
-- signal_id). This is what makes post-trade analysis possible: it records what
-- the strategy actually saw at the moment it decided, not what the market
-- looks like now.
--
-- Never store credentials, authorization headers, connection strings, raw
-- provider payloads or per-tick data here. See backend/signal_snapshot.py,
-- which builds these payloads from a fixed allow-list.
CREATE TABLE IF NOT EXISTS signal_indicator_snapshots (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id          UUID        NOT NULL UNIQUE
                                   REFERENCES signals(id) ON DELETE CASCADE,
    indicator_values   JSONB       NOT NULL,
    market_context     JSONB       NOT NULL,
    source_timestamps  JSONB       NOT NULL,
    input_candle_count INTEGER     NOT NULL,
    data_quality_flags JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT signal_snapshots_candle_count_chk CHECK (input_candle_count >= 0)
);

-- ── signal_events ───────────────────────────────────────────────────────────
-- Append-only lifecycle audit trail. Never UPDATE or DELETE rows here during
-- normal operation.
--
-- idempotency_key is globally unique and derived from
-- (signal_id, event_type, source timestamp), so reprocessing the same candle
-- or replaying a webhook cannot record the same event twice.
CREATE TABLE IF NOT EXISTS signal_events (
    id              UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       UUID           NOT NULL
                                   REFERENCES signals(id) ON DELETE CASCADE,
    event_type      TEXT           NOT NULL,
    event_time      TIMESTAMPTZ    NOT NULL,
    price           NUMERIC(30,12),
    metadata        JSONB          NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT           NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT signal_events_type_chk
        CHECK (event_type IN ('CREATED', 'TARGET_HIT', 'STOP_LOSS_HIT', 'CLOSED',
                              'EXPIRED', 'CANCELLED', 'ANALYSIS_ADDED', 'ARCHIVED')),
    CONSTRAINT signal_events_price_chk CHECK (price IS NULL OR price > 0)
);

-- ── signal_postmortems ──────────────────────────────────────────────────────
-- Why a signal ended the way it did. Written after the fact; NEVER read back
-- into live strategy parameters automatically. Any strategy change requires
-- separate backtesting, a new strategy_version and human approval.
CREATE TABLE IF NOT EXISTS signal_postmortems (
    id                              UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id                       UUID           NOT NULL UNIQUE
                                                   REFERENCES signals(id) ON DELETE CASCADE,
    outcome                         TEXT           NOT NULL,
    maximum_favorable_excursion_pct NUMERIC(18,8),
    maximum_adverse_excursion_pct   NUMERIC(18,8),
    duration_minutes                INTEGER,
    failed_conditions               JSONB          NOT NULL DEFAULT '[]'::jsonb,
    analysis_summary                TEXT,
    strategy_version                TEXT           NOT NULL,
    created_at                      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT signal_postmortems_duration_chk
        CHECK (duration_minutes IS NULL OR duration_minutes >= 0)
);

-- ── Indexes ─────────────────────────────────────────────────────────────────
-- Deliberately lean: every index costs free-tier storage, so these cover the
-- queries the API actually issues and nothing speculative.

-- Active-signal board. Partial: unarchived rows are the only ones ever listed
-- as active, so the index stays small as history accumulates.
CREATE INDEX IF NOT EXISTS signals_active_idx
    ON signals (status, generated_at DESC)
    WHERE archived_at IS NULL;

-- Per-symbol history.
CREATE INDEX IF NOT EXISTS signals_symbol_history_idx
    ON signals (symbol, generated_at DESC);

-- Instrument lookup (exchange + symbol + timeframe).
CREATE INDEX IF NOT EXISTS signals_instrument_idx
    ON signals (exchange, symbol, timeframe, generated_at DESC);

-- Strategy performance analysis.
CREATE INDEX IF NOT EXISTS signals_strategy_analysis_idx
    ON signals (strategy_version, timeframe, direction, status);

-- Archived vs unarchived sweeps.
CREATE INDEX IF NOT EXISTS signals_archived_idx
    ON signals (archived_at);

-- Closed-outcome reporting. Partial: only closed rows have closed_at.
CREATE INDEX IF NOT EXISTS signals_closed_idx
    ON signals (closed_at DESC)
    WHERE closed_at IS NOT NULL;

-- Targets by parent. (The UNIQUE(signal_id, target_number) index already
-- serves signal_id lookups, so no separate index is created — that would be
-- redundant storage on the free tier.)

-- Event trail per signal, in order.
CREATE INDEX IF NOT EXISTS signal_events_signal_time_idx
    ON signal_events (signal_id, event_time);

-- Postmortem grouping.
CREATE INDEX IF NOT EXISTS signal_postmortems_outcome_idx
    ON signal_postmortems (outcome, strategy_version);

-- ── Record this migration ───────────────────────────────────────────────────
INSERT INTO schema_migrations (version, description)
VALUES ('001', 'initial signal schema: signals, targets, snapshots, events, postmortems')
ON CONFLICT (version) DO NOTHING;

COMMIT;
