-- ============================================================================
-- verify_signals.sql — inspect STORED SIGNAL DATA. Read-only.
--
-- Paste into Neon Console -> SQL Editor. Every statement here is a SELECT: it
-- cannot modify or delete anything, so it is safe against production.
--
-- (For schema/structure checks — tables, constraints, indexes — use
--  database/verify_schema.sql instead. This file is about the rows.)
-- ============================================================================


-- 1) Everything currently live -----------------------------------------------
-- The signals the app considers working. Prices are numeric, so they print at
-- full stored precision rather than a rounded float.
SELECT id, symbol, direction, timeframe, status,
       entry_price, stop_loss, confidence_score,
       candle_close_time, generated_at
FROM   signals
WHERE  status IN ('OPEN', 'PARTIAL_TP')
  AND  archived_at IS NULL
ORDER  BY generated_at DESC, symbol;


-- 2) One signal, whole picture ----------------------------------------------
-- Replace the id with one from query 1 (or from the API's `signal_id`).
-- Targets, in ladder order:
SELECT target_number, target_price, hit_at, hit_price
FROM   signal_targets
WHERE  signal_id = 'PASTE-SIGNAL-ID-HERE'
ORDER  BY target_number;

-- Its lifecycle trail (append-only):
SELECT event_type, event_time, price, metadata, idempotency_key
FROM   signal_events
WHERE  signal_id = 'PASTE-SIGNAL-ID-HERE'
ORDER  BY event_time, created_at;

-- What the strategy actually saw when it decided:
SELECT input_candle_count,
       data_quality_flags,
       indicator_values -> 'rsi'                  AS rsi,
       indicator_values -> 'structure_adjustment' AS structure_adjustment,
       indicator_values -> 'structure_factors'    AS structure_factors,
       indicator_values -> 'stop_liquidity'       AS stop_liquidity,
       indicator_values -> 'tp_anchor'            AS tp_anchor,
       source_timestamps
FROM   signal_indicator_snapshots
WHERE  signal_id = 'PASTE-SIGNAL-ID-HERE';


-- 3) Signal + its targets on one line ---------------------------------------
-- Quick sanity read of the whole published set.
SELECT s.symbol, s.direction, s.status,
       s.entry_price, s.stop_loss,
       string_agg(t.target_price::text, ' | ' ORDER BY t.target_number) AS targets,
       s.candle_close_time
FROM   signals s
LEFT   JOIN signal_targets t ON t.signal_id = s.id
GROUP  BY s.id, s.symbol, s.direction, s.status, s.entry_price,
          s.stop_loss, s.candle_close_time
ORDER  BY s.candle_close_time DESC, s.symbol;


-- 4) Price structure sanity -------------------------------------------------
-- Any row returned here is a BUG: a LONG whose stop is not below entry, or
-- whose target is not above it (mirrored for SHORT). Should be empty.
SELECT s.id, s.symbol, s.direction, s.entry_price, s.stop_loss,
       t.target_number, t.target_price
FROM   signals s
LEFT   JOIN signal_targets t ON t.signal_id = s.id
WHERE  (s.direction = 'LONG'  AND (s.stop_loss >= s.entry_price
                                   OR t.target_price <= s.entry_price))
   OR  (s.direction = 'SHORT' AND (s.stop_loss <= s.entry_price
                                   OR t.target_price >= s.entry_price));


-- 5) Idempotency: is any candle duplicated? ---------------------------------
-- Also empty. The unique index makes this impossible, so this query is really
-- checking that the index is doing its job.
SELECT symbol, exchange, timeframe, strategy_name, strategy_version,
       candle_close_time, count(*) AS rows
FROM   signals
GROUP  BY 1,2,3,4,5,6
HAVING count(*) > 1;


-- 6) Signals per candle per symbol ------------------------------------------
-- The behaviour to expect: ONE row per symbol per closed candle, and a NEW row
-- as each later candle closes. Several rows for one symbol on one day is
-- correct as long as the candle times differ.
SELECT symbol,
       candle_close_time,
       count(*)                        AS signals,
       string_agg(DISTINCT direction, ',') AS directions
FROM   signals
GROUP  BY symbol, candle_close_time
ORDER  BY candle_close_time DESC, symbol;


-- 7) Timestamps really are UTC ----------------------------------------------
-- timestamptz always stores UTC; this shows it explicitly and confirms the
-- candle window is one bar wide for the timeframe.
SELECT symbol, timeframe,
       candle_open_time  AT TIME ZONE 'UTC' AS open_utc,
       candle_close_time AT TIME ZONE 'UTC' AS close_utc,
       candle_close_time - candle_open_time AS candle_length,
       generated_at      AT TIME ZONE 'UTC' AS generated_utc
FROM   signals
ORDER  BY candle_close_time DESC
LIMIT  20;


-- 8) Completed outcomes -----------------------------------------------------
SELECT symbol, direction, status, close_reason,
       entry_price, close_price, realized_return_pct,
       closed_at, archived_at
FROM   signals
WHERE  status NOT IN ('OPEN', 'PARTIAL_TP')
ORDER  BY closed_at DESC NULLS LAST
LIMIT  50;


-- 9) Row counts and storage -------------------------------------------------
-- The same figures /api/db/usage returns, without needing the internal secret.
SELECT 'signals'                    AS table_name, count(*) FROM signals
UNION ALL SELECT 'signal_targets',              count(*) FROM signal_targets
UNION ALL SELECT 'signal_indicator_snapshots',  count(*) FROM signal_indicator_snapshots
UNION ALL SELECT 'signal_events',               count(*) FROM signal_events
UNION ALL SELECT 'signal_postmortems',          count(*) FROM signal_postmortems
ORDER  BY table_name;

SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size,
       count(*)                                    AS signals_total,
       count(*) FILTER (WHERE status IN ('OPEN','PARTIAL_TP')
                        AND archived_at IS NULL)   AS active,
       count(*) FILTER (WHERE archived_at IS NOT NULL) AS archived,
       min(generated_at)                           AS oldest,
       max(generated_at)                           AS newest
FROM   signals;


-- 10) Orphan check ----------------------------------------------------------
-- Foreign keys make orphans impossible; this catches the opposite problem —
-- a signal that is MISSING its snapshot or its CREATED event, which would mean
-- the create transaction did not land atomically. Should be empty.
SELECT s.id, s.symbol,
       (SELECT count(*) FROM signal_targets t WHERE t.signal_id = s.id) AS targets,
       (SELECT count(*) FROM signal_indicator_snapshots n WHERE n.signal_id = s.id) AS snapshots,
       (SELECT count(*) FROM signal_events e
        WHERE e.signal_id = s.id AND e.event_type = 'CREATED')          AS created_events
FROM   signals s
WHERE  (SELECT count(*) FROM signal_indicator_snapshots n WHERE n.signal_id = s.id) <> 1
   OR  (SELECT count(*) FROM signal_events e
        WHERE e.signal_id = s.id AND e.event_type = 'CREATED') <> 1;


-- 11) Strategy versions present ---------------------------------------------
-- After a scoring change, old and new signals must be distinguishable.
SELECT strategy_name, strategy_version, count(*) AS signals,
       min(generated_at) AS first_seen, max(generated_at) AS last_seen
FROM   signals
GROUP  BY strategy_name, strategy_version
ORDER  BY last_seen DESC;
