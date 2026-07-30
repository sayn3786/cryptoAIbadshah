-- ============================================================================
-- verify_schema.sql — read-only. Safe to run any time, on any environment.
--
-- Paste into Neon Dashboard -> Query after running
-- database/migrations/001_initial_signal_schema.sql, and check the five
-- result sets below.
--
-- Expected on a correct install:
--   1) applied migrations  -> one row, version 001
--   2) tables              -> 6 rows (5 signal tables + schema_migrations)
--   3) constraints         -> CHECK/FK/UNIQUE/PK rows for the signal tables
--   4) indexes             -> the idempotency unique index plus the query indexes
--   5) column types        -> every price/percentage column numeric, never float
-- ============================================================================

-- 1) Applied migration versions ---------------------------------------------
SELECT version, description, applied_at
FROM   schema_migrations
ORDER  BY version;

-- 2) Created tables ----------------------------------------------------------
SELECT table_name
FROM   information_schema.tables
WHERE  table_schema = 'public'
  AND  table_name IN ('schema_migrations', 'signals', 'signal_targets',
                      'signal_indicator_snapshots', 'signal_events',
                      'signal_postmortems')
ORDER  BY table_name;

-- 3) Table constraints -------------------------------------------------------
SELECT tc.table_name,
       tc.constraint_type,
       tc.constraint_name,
       cc.check_clause
FROM   information_schema.table_constraints tc
LEFT   JOIN information_schema.check_constraints cc
       ON  cc.constraint_name   = tc.constraint_name
       AND cc.constraint_schema = tc.constraint_schema
WHERE  tc.table_schema = 'public'
  AND  tc.table_name IN ('signals', 'signal_targets',
                         'signal_indicator_snapshots', 'signal_events',
                         'signal_postmortems')
  AND  tc.constraint_type IN ('CHECK', 'FOREIGN KEY', 'UNIQUE', 'PRIMARY KEY')
  AND  COALESCE(cc.check_clause, '') NOT LIKE '%IS NOT NULL%'   -- hide NOT NULL noise
ORDER  BY tc.table_name, tc.constraint_type, tc.constraint_name;

-- 4) Indexes -----------------------------------------------------------------
SELECT tablename, indexname, indexdef
FROM   pg_indexes
WHERE  schemaname = 'public'
  AND  tablename IN ('signals', 'signal_targets',
                     'signal_indicator_snapshots', 'signal_events',
                     'signal_postmortems')
ORDER  BY tablename, indexname;

-- 5) Money columns must be numeric, never float ------------------------------
-- Any row returned here is a BUG: binary floating point cannot represent
-- decimal prices exactly.
SELECT table_name, column_name, data_type, numeric_precision, numeric_scale
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name IN ('signals', 'signal_targets', 'signal_postmortems')
  AND  (column_name LIKE '%price%'
        OR column_name LIKE '%_pct%'
        OR column_name IN ('stop_loss', 'confidence_score'))
ORDER  BY table_name, column_name;
