-- Requires 019. Explicit opt-in; captures observations, never certifies outcomes.
BEGIN;
CREATE TABLE execution_account_observations (
 run_id uuid PRIMARY KEY,
 network text NOT NULL,
 account_hash text NOT NULL,
 request_started_at timestamptz NOT NULL,
 position_request_started_at timestamptz NOT NULL,
 response_received_at timestamptz NOT NULL,
 venue_time_ms bigint NOT NULL CHECK(venue_time_ms > 0),
 dex text NOT NULL CHECK(dex = ''),
 positions jsonb NOT NULL CHECK(jsonb_typeof(positions) = 'object'),
 funding_count integer NOT NULL CHECK(funding_count >= 0),
 CHECK(request_started_at <= position_request_started_at),
 CHECK(position_request_started_at <= response_received_at),
 FOREIGN KEY(run_id,network,account_hash)
  REFERENCES execution_evidence_runs(id,network,account_hash)
);
CREATE TABLE execution_funding_evidence (
 network text NOT NULL,
 account_hash text NOT NULL,
 event_key text NOT NULL CHECK(event_key ~ '^[a-f0-9]{64}$'),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 first_run_id uuid NOT NULL,
 PRIMARY KEY(network,account_hash,event_key),
 FOREIGN KEY(first_run_id,network,account_hash)
  REFERENCES execution_evidence_runs(id,network,account_hash)
);
CREATE TRIGGER execution_account_observations_immutable BEFORE UPDATE OR DELETE
 ON execution_account_observations FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TABLE execution_run_funding (
 run_id uuid NOT NULL,
 network text NOT NULL,
 account_hash text NOT NULL,
 event_key text NOT NULL,
 PRIMARY KEY(run_id,event_key),
 FOREIGN KEY(run_id,network,account_hash) REFERENCES execution_evidence_runs(id,network,account_hash),
 FOREIGN KEY(network,account_hash,event_key) REFERENCES execution_funding_evidence(network,account_hash,event_key)
);
CREATE TRIGGER execution_run_funding_immutable BEFORE UPDATE OR DELETE ON execution_run_funding
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER execution_funding_evidence_immutable BEFORE UPDATE OR DELETE
 ON execution_funding_evidence FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('021','Append-only funding and current first-perp-dex position observations');
COMMIT;
