# Read-only execution loader — verification, 2026-10-23 SGT

Implemented a bounded ledger loader and recorded publication-before-fill checks.
No production systems or real account records were read or written.

## Changes

- Migration drafts 019/020 now include append-only run/event membership tables.
  Earlier drafts have not been deployed; these edits are not a production upgrade.
- Loading requires a PostgreSQL read-only repeatable-read/serializable transaction.
- Validates event counts, payload hashes, original order-link evidence, signal
  environment, and boundary account/network scope.
- Query limits reject oversized results; window is at most 24 hours / 100 runs.
- Positive ACCEPTED Telegram receipts must have valid request/response ordering
  and a response timestamp strictly before the earliest instrument fill.
- Clock alignment is unverified. Every report remains training-ineligible.

## Tests

PostgreSQL 16.15 disposable local Unix-socket instance: **14 tests passed**
(7 database tests and 7 publication-timing tests). Tests exercise migrations,
atomic writes, immutable membership, repeated collections, distinct event counts,
read-only enforcement, environment isolation, and publication joins. Synthetic
fixtures only. No held-out period accessed.

Full regression: **3,050 passed, 113 skipped**. The database tests were run
separately as above. The temporary PostgreSQL instance was shut down afterward.

## Limits

Hashes detect inconsistency, not cryptographic provider authenticity. Read-only
loading does not prove upstream history completeness. Snapshot rows are immutable
but not independently signed. Full migration-chain upgrade testing, independent
clock verification, lifecycle status observations, and prospective coverage
certification remain. There is no production caller, scheduled job, ML promotion,
merge or deployment in this change.
