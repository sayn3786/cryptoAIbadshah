# Execution evidence verification — 2026-10-22 (SGT)

Scope: synthetic fixtures only. No Neon reads/writes, venue account requests,
Telegram sends, trades, or held-out October research outcomes.

## Real PostgreSQL check

Built PostgreSQL 16.15 in `/private/tmp/cryptostars-pg-test-20261022` from the
[official source archive](https://ftp.postgresql.org/pub/source/v16.15/).
Archive SHA-256 matched the published checksum:
`c1575341fa7bd40f5274ea465b34390f4dc64cdd0770af327005caaeb9f6b7ed`.
The temporary server used a private Unix socket, no TCP listener, and was stopped
after testing. Nothing was installed system-wide.

Ran `tests/test_execution_evidence.py` and
`tests/test_execution_evidence_postgres.py`: **23 passed**, none skipped.
The database fixture applies migration 019 with minimal parent-table fixtures;
this does not certify an upgrade of an existing production database.

Verified migration execution, idempotent fill/link writes, transaction rollback
on changed execution identities, signal foreign keys, immutable updates/deletes,
and database-enforced network/account consistency with the originating run.

## Position-chain checks

Ran collector, reconciliation and data-dictionary tests: **36 passed**.
Synthetic checks cover both sides, partial exits, duplicate/conflicting fills,
missing fill gaps, endpoint mismatches, equal timestamps, wrong instruments,
boundary handling and non-USDC fees. The audit deliberately does not compute P&L
or promote a training label. Zero net quantity is not proof of complete history.

## Remaining gates

Full regression suite after changes: **3,003 passed, 110 skipped**. Database tests
are skipped in that default run; the four new database tests passed separately
against the temporary PostgreSQL instance as recorded above.

- CI execution and review; no push, PR, merge or deployment performed.
- Lifecycle capture; boundary-snapshot pairing, funding attribution and coverage.
- Verified publication/order linkage and a prospective development cohort.
- Explicit approval before production migration, writes or scheduling.

The data-quality checklist guided identity, timing, completeness and consistency
tests. No conclusion about actual account profitability or ML readiness follows
from synthetic test success.

## Follow-up: migration 020 and account evidence

The next implementation adds immutable funding-event records and current
first-perpetual-DEX position observations. Position timestamps are not backdated
to historical collection windows. Missing/stale positions, capped funding
responses, duplicate instrument rows and changed event identities fail closed.
Empty funding responses are explicitly recorded, but never certify completeness.

Migration 019 followed by 020 passed in the same disposable PostgreSQL 16.15
instance. Collector, account evidence, position audit and six PostgreSQL tests:
**55 passed**, none skipped. This includes atomic rollback on a funding conflict,
deduplication and immutable storage. The temporary instance was stopped afterward.
No production or live account data was accessed. Funding is captured but not yet
attributed to individual trades; full trade-outcome certification is still absent.
