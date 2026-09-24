-- Additive to 013: old v1 snapshots remain untouched, not relabelled as v2.
BEGIN;
ALTER TABLE ml_feature_snapshots ADD COLUMN IF NOT EXISTS fetch_started_at timestamptz;
CREATE TABLE IF NOT EXISTS ml_label_jobs (
    snapshot_id uuid NOT NULL REFERENCES ml_feature_snapshots(id),
    label_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('retry', 'backfill_needed')),
    attempts integer NOT NULL CHECK (attempts > 0),
    last_reason text NOT NULL,
    last_attempt_at timestamptz NOT NULL,
    next_attempt_at timestamptz,
    PRIMARY KEY (snapshot_id, label_version),
    CHECK ((status = 'retry' AND next_attempt_at IS NOT NULL)
        OR (status = 'backfill_needed' AND next_attempt_at IS NULL))
);
CREATE INDEX IF NOT EXISTS ml_label_jobs_due_idx ON ml_label_jobs (status, next_attempt_at);
INSERT INTO schema_migrations (version, description)
VALUES ('014', 'ML pre-fetch cutoff and bounded label retries/backfill queue')
ON CONFLICT (version) DO NOTHING;
COMMIT;
