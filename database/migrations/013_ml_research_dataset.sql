-- Research only. No live strategy reads these tables. Apply explicitly.
BEGIN;
CREATE TABLE IF NOT EXISTS ml_feature_snapshots (
    id uuid PRIMARY KEY,
    environment text NOT NULL,
    symbol text NOT NULL,
    feature_version text NOT NULL,
    slot_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    source text NOT NULL,
    entry_at_ms bigint NOT NULL,
    features jsonb NOT NULL,
    quality text NOT NULL CHECK (quality IN ('ready', 'invalid')),
    reason text,
    last_candle_close_ms bigint,
    input_hash text,
    UNIQUE (environment, feature_version, slot_at, symbol),
    CHECK (observed_at >= slot_at),
    CHECK (to_timestamp(entry_at_ms / 1000.0) > observed_at)
);
CREATE INDEX IF NOT EXISTS ml_features_research_idx
    ON ml_feature_snapshots (environment, feature_version, observed_at);
CREATE TABLE IF NOT EXISTS ml_labels (
    snapshot_id uuid NOT NULL REFERENCES ml_feature_snapshots(id),
    label_version text NOT NULL,
    horizon_hours integer NOT NULL CHECK (horizon_hours = 4),
    neutral_bps double precision NOT NULL CHECK (neutral_bps >= 0),
    entry_at_ms bigint NOT NULL,
    exit_at_ms bigint NOT NULL,
    available_at timestamptz NOT NULL,
    entry_price double precision NOT NULL CHECK (entry_price > 0),
    exit_price double precision NOT NULL CHECK (exit_price > 0),
    return_bps double precision NOT NULL,
    direction text NOT NULL CHECK (direction IN ('UP', 'DOWN', 'NEUTRAL')),
    source text NOT NULL,
    input_hash text NOT NULL,
    PRIMARY KEY (snapshot_id, label_version),
    CHECK (exit_at_ms = entry_at_ms + 14400000),
    CHECK (available_at >= to_timestamp(exit_at_ms / 1000.0))
);
INSERT INTO schema_migrations (version, description)
VALUES ('013', 'Research-only immutable candle features and four-hour labels')
ON CONFLICT (version) DO NOTHING;
COMMIT;
