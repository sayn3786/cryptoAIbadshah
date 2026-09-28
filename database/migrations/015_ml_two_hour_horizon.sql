-- Apply BEFORE deploying the two-hour collector. Preserve all legacy labels.
BEGIN;
ALTER TABLE ml_labels DROP CONSTRAINT IF EXISTS ml_labels_horizon_hours_check;
ALTER TABLE ml_labels DROP CONSTRAINT IF EXISTS ml_labels_check;
ALTER TABLE ml_labels ADD CONSTRAINT ml_labels_horizon_hours_check
    CHECK (horizon_hours IN (2, 4));
ALTER TABLE ml_labels ADD CONSTRAINT ml_labels_check
    CHECK (exit_at_ms = entry_at_ms + horizon_hours::bigint * 3600000);
ALTER TABLE ml_labels DROP CONSTRAINT IF EXISTS ml_labels_version_horizon_check;
ALTER TABLE ml_labels ADD CONSTRAINT ml_labels_version_horizon_check
    CHECK ((label_version = 'next_open_4h_20bps_v1' AND horizon_hours = 4)
        OR (label_version = 'next_open_2h_20bps_v1' AND horizon_hours = 2));
INSERT INTO schema_migrations (version, description)
VALUES ('015', 'Versioned two-hour research labels; preserve four-hour history')
ON CONFLICT (version) DO NOTHING;
COMMIT;
