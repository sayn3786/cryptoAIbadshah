-- Initial SL/TP acknowledgements only; no verified fills or automatic activation.
BEGIN;
CREATE TABLE exit_order_observations (
 LIKE opening_order_observations INCLUDING ALL,
 FOREIGN KEY (signal_id) REFERENCES signals(id)
);
CREATE TRIGGER exit_order_immutable BEFORE UPDATE OR DELETE
 ON exit_order_observations FOR EACH ROW
 EXECUTE FUNCTION reject_execution_evidence_mutation();
INSERT INTO schema_migrations(version,description) VALUES
 ('026','Prospective testnet initial exit-order identity observations; no training promotion');
COMMIT;
