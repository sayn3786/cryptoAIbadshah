BEGIN;
ALTER TABLE manager_execution_observations
 ADD CONSTRAINT manager_execution_identity UNIQUE(id,network,account_hash);
CREATE TABLE manager_order_observations (
 id uuid PRIMARY KEY,
 manager_response_id uuid NOT NULL,
 network text NOT NULL CHECK(network IN ('mainnet','testnet')),
 account_hash text NOT NULL CHECK(account_hash ~ '^[a-f0-9]{64}$'),
 client_order_id text NOT NULL CHECK(client_order_id ~ '^0x[0-9a-fA-F]{32}$'),
 request_started_at timestamptz NOT NULL,
 response_received_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 CHECK(response_received_at >= request_started_at),
 FOREIGN KEY(manager_response_id,network,account_hash)
  REFERENCES manager_execution_observations(id,network,account_hash)
);
CREATE INDEX manager_order_response ON manager_order_observations(manager_response_id);
CREATE TRIGGER manager_order_immutable BEFORE UPDATE OR DELETE
 ON manager_order_observations FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('023','Append-only independent manager order-status observations');
COMMIT;
