-- Operational admission state only. Evidence tables remain append-only.
BEGIN;
CREATE TABLE execution_pilot_slots (
 pilot_id uuid NOT NULL,
 slot integer NOT NULL CHECK(slot BETWEEN 1 AND 96),
 run_id uuid NOT NULL UNIQUE,
 status text NOT NULL CHECK(status IN ('STARTED','COMPLETE','FAILED')),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz,
 PRIMARY KEY(pilot_id,slot),
 CHECK((status='STARTED' AND finished_at IS NULL) OR
       (status IN ('COMPLETE','FAILED') AND finished_at IS NOT NULL))
);
INSERT INTO schema_migrations(version,description) VALUES
 ('024','Bounded testnet observation pilot admission and completion records');
COMMIT;
