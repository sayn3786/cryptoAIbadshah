-- Explicitly injected prospective testnet journal. Not a verified order link.
BEGIN;
CREATE TABLE opening_order_observations (
 id uuid PRIMARY KEY,
 correlation_id uuid NOT NULL,
 signal_id uuid NOT NULL REFERENCES signals(id),
 environment text NOT NULL CHECK(environment ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
 network text NOT NULL CHECK(network='testnet'),
 account_hash text NOT NULL CHECK(account_hash ~ '^[a-f0-9]{64}$'),
 phase text NOT NULL CHECK(phase IN ('INTENT','RESPONSE','UNKNOWN')),
 observed_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 UNIQUE(correlation_id,phase)
);
CREATE INDEX opening_order_scope ON opening_order_observations
 (environment,network,account_hash,signal_id,observed_at);
CREATE TRIGGER opening_order_immutable BEFORE UPDATE OR DELETE
 ON opening_order_observations FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('025','Prospective testnet opening-order identity observations; no training promotion');
COMMIT;
