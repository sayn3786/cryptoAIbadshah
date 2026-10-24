-- Opt-in observational journal; no inferred signal ownership or trade labels.
BEGIN;
CREATE TABLE manager_execution_observations (
 id uuid PRIMARY KEY,
 correlation_id uuid NOT NULL,
 network text NOT NULL CHECK(network IN ('testnet','mainnet')),
 account_hash text NOT NULL CHECK(account_hash ~ '^[a-f0-9]{64}$'),
 phase text NOT NULL CHECK(phase IN ('INTENT','RESPONSE','UNKNOWN')),
 observed_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 UNIQUE(correlation_id,phase)
);
CREATE INDEX manager_execution_correlation ON manager_execution_observations
 (network,account_hash,correlation_id);
CREATE TRIGGER manager_execution_immutable BEFORE UPDATE OR DELETE
 ON manager_execution_observations FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('022','Append-only manager action intent and response observations; no inferred attribution');
COMMIT;
