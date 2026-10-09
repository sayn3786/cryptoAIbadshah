# Prospective publication timing (disabled by default)

This feature records Telegram recommendation publication evidence, not user
delivery, read receipts, broker fills, or ML training labels. No message bodies,
chat IDs or credentials are stored: only the message SHA-256, signal links and
timing/acknowledgment fields. No historical records are backfilled.

## Semantics

An attempt is committed before sending. request_started_at is a conservative
application UTC lower bound taken before that commit, not exact wire time.
response_received_at is captured when requests.post returns. Clock synchronization
is not verified. Telegram's optional date is provider-reported second-resolution
message creation time, not recipient delivery.

ACCEPTED requires HTTP success, Telegram ok=true, and a valid message_id.
REJECTED records an explicit negative application response; transport failures,
ambiguous responses, and missing receipts stay UNKNOWN/unresolved. These must
never be promoted to actual execution outcomes.

Receipts and attempts are append-only. Identical receipt retries using the same
timestamp are no-ops; conflicting receipts fail. Concurrent writes may hit the
unique constraint but cannot overwrite a receipt. Response time before attempt
time fails the database constraint. Attempts only link existing same-environment
signal IDs through the application writer.

Missing signal IDs or ledger failure block sending when enabled. This includes
empty/no-actionable-signal messages; review that deliberate behavior before
activation. If the provider accepted but receipt storage fails, send returns
success so audit failure alone does not resend. The unresolved attempt remains.
This does not guarantee exactly-once Telegram delivery: existing caller retries
after UNKNOWN may send another attempt, and both must remain distinguishable.

The candle monitor now tags ENTRY_FILLED event metadata as candle_simulation,
broker_fill_verified=false. No fill/exit prices, timings, strategies, or historical
events are changed. Actual Hyperliquid fill ingestion is outside this PR.

## Rollout — separate approval required

1. Review migration 016 and apply to a staging database.
2. Deploy code with PUBLICATION_EVIDENCE_ENABLED unset/false.
3. Verify recommendation callers pass persisted signal_id values.
4. Separately approve production migration and enable
   PUBLICATION_EVIDENCE_ENABLED=true in the intended deployment environment.
5. Verify committed attempt/link/receipt rows; monitor unresolved attempts.

Do not invoke migrations on startup or from API requests. To disable collection,
set the flag false; preserve ledger records. Existing historical data and reserved
research evaluation periods are not inputs to this feature.

## Tests

Run tests/test_publication_evidence.py and tests/test_publication_evidence_database.py.
The database tests require a disposable local socket database and refuse other
DSNs. scripts/test_disposable_postgres.py provisions a temporary cluster using
the optional pgserver test package, strips production credentials and disables
dotenv. It shuts down the cluster after the test run. No external Telegram calls
are made in tests. pgserver is a test-only dependency, not a production dependency.
