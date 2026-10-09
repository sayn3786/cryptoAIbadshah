-- Prospective evidence only. Never backfill delivery from signal creation time.
BEGIN;
CREATE TABLE IF NOT EXISTS publication_attempts (
 id uuid PRIMARY KEY,
 environment text NOT NULL,
 channel text NOT NULL CHECK(channel='telegram'),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
 request_started_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 clock_source text NOT NULL DEFAULT 'application_utc_unverified'
);
CREATE TABLE IF NOT EXISTS publication_attempt_signals (
 attempt_id uuid NOT NULL REFERENCES publication_attempts(id),
 signal_id uuid NOT NULL REFERENCES signals(id),
 PRIMARY KEY(attempt_id,signal_id)
);
CREATE TABLE IF NOT EXISTS publication_receipts (
 attempt_id uuid PRIMARY KEY REFERENCES publication_attempts(id),
 outcome text NOT NULL CHECK(outcome IN ('ACCEPTED','REJECTED','UNKNOWN')),
 response_received_at timestamptz NOT NULL,
 provider_message_id bigint,
 provider_reported_at timestamptz,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((outcome='ACCEPTED' AND provider_message_id IS NOT NULL) OR
       (outcome<>'ACCEPTED' AND provider_message_id IS NULL AND provider_reported_at IS NULL))
);
CREATE OR REPLACE FUNCTION reject_publication_evidence_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'publication evidence is append-only';
END $$;
DROP TRIGGER IF EXISTS publication_attempts_immutable ON publication_attempts;
CREATE TRIGGER publication_attempts_immutable BEFORE UPDATE OR DELETE ON publication_attempts
FOR EACH ROW EXECUTE FUNCTION reject_publication_evidence_mutation();
DROP TRIGGER IF EXISTS publication_links_immutable ON publication_attempt_signals;
CREATE TRIGGER publication_links_immutable BEFORE UPDATE OR DELETE ON publication_attempt_signals
FOR EACH ROW EXECUTE FUNCTION reject_publication_evidence_mutation();
DROP TRIGGER IF EXISTS publication_receipts_immutable ON publication_receipts;
CREATE TRIGGER publication_receipts_immutable BEFORE UPDATE OR DELETE ON publication_receipts
FOR EACH ROW EXECUTE FUNCTION reject_publication_evidence_mutation();
CREATE OR REPLACE FUNCTION validate_publication_receipt_time() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.response_received_at < (SELECT request_started_at FROM publication_attempts WHERE id=NEW.attempt_id) THEN
  RAISE EXCEPTION 'publication receipt precedes attempt';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS publication_receipt_time ON publication_receipts;
CREATE TRIGGER publication_receipt_time BEFORE INSERT ON publication_receipts
FOR EACH ROW EXECUTE FUNCTION validate_publication_receipt_time();
INSERT INTO schema_migrations(version,description) VALUES
 ('016','Append-only prospective Telegram publication timing evidence')
ON CONFLICT(version) DO NOTHING;
COMMIT;
