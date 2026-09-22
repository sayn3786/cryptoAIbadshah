-- Compact first-observed candidate evidence per strategy/symbol/publication slot.
-- Apply through the normal migration process before deploying the writer.
BEGIN;
CREATE TABLE IF NOT EXISTS candidate_decisions (
    environment text NOT NULL,
    strategy_version text NOT NULL,
    slot_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    symbol text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (environment, strategy_version, slot_at, symbol)
);
CREATE INDEX IF NOT EXISTS candidate_decisions_observed_idx
    ON candidate_decisions (environment, observed_at DESC);
INSERT INTO schema_migrations (version, description)
VALUES ('012', 'Immutable per-slot candidate decision evidence')
ON CONFLICT (version) DO NOTHING;
COMMIT;
