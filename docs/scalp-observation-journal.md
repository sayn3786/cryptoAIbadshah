# Inactive scalp observation journal

This change adds an explicitly injected observation path for an existing
market scalp and its initial SL/TP orders. No production caller supplies
the observer. No environment variable activates it.

Identity is environment + testnet account hash + symbol + setup slot.
The original scalp identity and deterministic client order ID are retained.
These records are NOT daily signal UUIDs and do not reference signals.id.
Provider acknowledgments are not independently verified fills.

Migration 027 adds a separate append-only table. It has not been applied
to production. The persistence helper uses a caller-owned transaction.
An eventual caller must buffer observations in memory and flush only after
protective handling; do not inject a synchronous network/DB sink.

`BufferedCapture` is the inactive single-use caller building block. Its `run`
callback receives optional order wrappers and must contain the entire protective
workflow, including emergency close handling. The batch flush callback runs
after that workflow returns or raises, must use one transaction, and is never
retried. Flush failure does not replace the workflow result or exception.
It retains at most eight events (opening plus three initial exits); overflow
discards the batch instead of persisting incomplete evidence. Inspect
`flush_status` for EMPTY, FLUSHED, FAILED or OVERFLOW; FLUSHED means only that
the supplied callback returned successfully, not independent verification.

The buffered adapter compares the configured testnet address, supplied account
snapshot and expected lowercase-address SHA256 hash. It rechecks account scope
before each observed send. This is configuration consistency, not proof of
ownership or venue identity. Low-level `order_wrappers` still requires a trusted
caller; no existing production caller is switched to `BufferedCapture` here.

Boundaries:
- testnet only; fixed symbol/slot and at most 24 hours;
- no capture of bulk limit orders, manager replacements or manual closes;
- caller-supplied account hash is not proof of account ownership;
- no active credential loading, signing, scheduler, retries or backfill;
- training eligibility and attribution verification remain false;
- process failure before flush can lose observations; do not reconstruct them.

Before activation, review/apply the schema separately, verify the actual
execution account, integrate the buffered caller around full protection and inspect independent
venue orders/fills. Do not place an order solely to generate evidence.

Local verification: 38 tests passed against a socket-only disposable PostgreSQL
16.15 cluster (including persistence); another 67 scalp regression tests passed
and one was skipped. The database test is included in the existing PostgreSQL
CI job. No production database or exchange was used.

Database test:

```sh
TEST_POSTGRES_BIN=/path/to/disposable/postgres/bin python3 scripts/test_disposable_postgres.py -q tests/test_scalp_observation_postgres.py
```
