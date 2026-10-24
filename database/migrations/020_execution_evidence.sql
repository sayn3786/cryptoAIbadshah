-- Opt-in observational ledger. Never invokes orders or certifies ML labels.
BEGIN;
CREATE TABLE IF NOT EXISTS execution_evidence_runs (
 id uuid PRIMARY KEY,
 network text NOT NULL CHECK(network IN ('testnet','mainnet')),
 account_hash text NOT NULL CHECK(account_hash ~ '^[a-f0-9]{64}$'),
 start_ms bigint NOT NULL CHECK(start_ms > 0),
 end_ms bigint NOT NULL CHECK(end_ms >= start_ms),
 received_at timestamptz NOT NULL,
 fill_count integer NOT NULL CHECK(fill_count >= 0),
 history_complete boolean NOT NULL DEFAULT false CHECK(history_complete = false),
 UNIQUE(id,network,account_hash)
);
CREATE TABLE IF NOT EXISTS execution_fill_evidence (
 network text NOT NULL CHECK(network IN ('testnet','mainnet')),
 account_hash text NOT NULL,
 execution_id text NOT NULL,
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL,
 first_run_id uuid NOT NULL,
 PRIMARY KEY(network,account_hash,execution_id),
 FOREIGN KEY(first_run_id,network,account_hash)
   REFERENCES execution_evidence_runs(id,network,account_hash)
);
CREATE TABLE IF NOT EXISTS execution_order_links (
 network text NOT NULL CHECK(network IN ('testnet','mainnet')),
 account_hash text NOT NULL,
 order_id text NOT NULL,
 signal_id uuid NOT NULL REFERENCES signals(id),
 intent text NOT NULL CHECK(intent IN ('open','sl','tp1','tp2')),
 client_order_id text NOT NULL CHECK(client_order_id ~ '^0x[a-f0-9]{32}$'),
 instrument text NOT NULL,
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 evidence jsonb NOT NULL,
 run_id uuid NOT NULL,
 PRIMARY KEY(network,account_hash,order_id),
 UNIQUE(network,account_hash,client_order_id),
 FOREIGN KEY(run_id,network,account_hash)
   REFERENCES execution_evidence_runs(id,network,account_hash)
);
CREATE OR REPLACE FUNCTION reject_execution_evidence_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'execution evidence is append-only';
END $$;
CREATE TABLE execution_run_fills (
 run_id uuid NOT NULL,
 network text NOT NULL,
 account_hash text NOT NULL,
 execution_id text NOT NULL,
 PRIMARY KEY(run_id,execution_id),
 FOREIGN KEY(run_id,network,account_hash) REFERENCES execution_evidence_runs(id,network,account_hash),
 FOREIGN KEY(network,account_hash,execution_id) REFERENCES execution_fill_evidence(network,account_hash,execution_id)
);
CREATE TRIGGER execution_run_fills_immutable BEFORE UPDATE OR DELETE ON execution_run_fills
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER execution_runs_immutable BEFORE UPDATE OR DELETE ON execution_evidence_runs
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER execution_fills_immutable BEFORE UPDATE OR DELETE ON execution_fill_evidence
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER execution_links_immutable BEFORE UPDATE OR DELETE ON execution_order_links
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('020','Append-only observational execution evidence; no label promotion')
ON CONFLICT(version) DO NOTHING;
COMMIT;
