-- Frozen publication intent and completion in the same database as signals.
BEGIN;
CREATE TABLE IF NOT EXISTS publication_batches (
    environment text NOT NULL,
    slot_start timestamptz NOT NULL,
    strategy_version text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    PRIMARY KEY (environment, slot_start)
);
CREATE OR REPLACE FUNCTION guard_publication_batch() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'publication batch cannot be deleted';
    END IF;
    IF NEW.environment IS DISTINCT FROM OLD.environment
       OR NEW.slot_start IS DISTINCT FROM OLD.slot_start
       OR NEW.strategy_version IS DISTINCT FROM OLD.strategy_version
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.completed_at IS NOT NULL
       OR NEW.completed_at IS NULL THEN
        RAISE EXCEPTION 'only first batch completion is allowed';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS publication_batch_guard ON publication_batches;
CREATE TRIGGER publication_batch_guard BEFORE UPDATE OR DELETE ON publication_batches
    FOR EACH ROW EXECUTE FUNCTION guard_publication_batch();
COMMIT;
