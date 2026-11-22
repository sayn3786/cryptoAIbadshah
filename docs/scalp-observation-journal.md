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

Boundaries:
- testnet only; fixed symbol/slot and at most 24 hours;
- no capture of bulk limit orders, manager replacements or manual closes;
- caller-supplied account hash is not proof of account ownership;
- no active credential loading, signing, scheduler, retries or backfill;
- training eligibility and attribution verification remain false;
- process failure before flush can lose observations; do not reconstruct them.

Before activation, review/apply the schema separately, verify the actual
execution account, implement a bounded buffered caller and inspect independent
venue orders/fills. Do not place an order solely to generate evidence.

Local verification: 32 tests passed against a socket-only disposable PostgreSQL
16.15 cluster (including persistence); another 67 scalp regression tests passed
and one was skipped. No production database or exchange was used.

Database test:

```sh
TEST_POSTGRES_BIN=/path/to/disposable/postgres/bin python3 scripts/test_disposable_postgres.py -q tests/test_scalp_observation_postgres.py
```
