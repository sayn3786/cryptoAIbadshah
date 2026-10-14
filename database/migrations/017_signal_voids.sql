-- ============================================================================
-- 017_signal_voids.sql
-- Data-error voids: a CLOSED signal whose outcome came from bad data (e.g. the
-- monitor reading synthetic demo candles, fixed in #311) is excluded from every
-- statistic. Nothing on the signal row changes — a terminal row stays terminal;
-- this append-only table IS the audit record (who/why/when, never edited).
-- Safe on either side of the deploy: the code checks to_regclass and, without
-- the table, excludes nothing and refuses to void.
-- ============================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS signal_voids (
    signal_id  uuid        PRIMARY KEY REFERENCES signals(id),
    voided_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    reason     text        NOT NULL CHECK (length(reason) BETWEEN 3 AND 300)
);

CREATE OR REPLACE FUNCTION reject_signal_void_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'signal_voids is append-only';
END $$;

DROP TRIGGER IF EXISTS signal_voids_immutable ON signal_voids;
CREATE TRIGGER signal_voids_immutable BEFORE UPDATE OR DELETE ON signal_voids
    FOR EACH ROW EXECUTE FUNCTION reject_signal_void_mutation();

COMMIT;
