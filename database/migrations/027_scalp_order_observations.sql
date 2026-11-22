-- Separate evidence namespace. Does not enable trading or collection.
BEGIN;
CREATE TABLE scalp_order_observations (
 id uuid PRIMARY KEY,
 correlation_id uuid NOT NULL,
 environment text NOT NULL CHECK(environment ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
 network text NOT NULL CHECK(network='testnet'),
 account_hash text NOT NULL CHECK(account_hash ~ '^[a-f0-9]{64}$'),
 symbol text NOT NULL CHECK(symbol ~ '^[A-Z0-9]{1,20}$'),
 slot_ms bigint NOT NULL CHECK(slot_ms>0),
 intent text NOT NULL CHECK(intent IN ('open','sl','tp1','tp2')),
 phase text NOT NULL CHECK(phase IN ('INTENT','RESPONSE','UNKNOWN')),
 observed_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 payload jsonb NOT NULL,
 training_eligible boolean NOT NULL DEFAULT false CHECK(NOT training_eligible),
 UNIQUE(correlation_id,phase)
);
CREATE INDEX scalp_observation_scope ON scalp_order_observations
 (environment,network,account_hash,symbol,slot_ms);
CREATE TRIGGER scalp_observation_immutable BEFORE UPDATE OR DELETE
 ON scalp_order_observations FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('027','Separate testnet scalp order observations; no activation or training promotion');
COMMIT;
