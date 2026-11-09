# Signal-to-fill readiness checkpoint — 2026-11-02

## Follow-up — 2026-11-04

The disposable PostgreSQL blocker is resolved. PostgreSQL 16.15 was rebuilt
privately from the official source archive (SHA-256
`c1575341fa7bd40f5274ea465b34390f4dc64cdd0770af327005caaeb9f6b7ed`), with
pgcrypto. The runner now accepts an explicit `TEST_POSTGRES_BIN` directory,
while retaining its isolated socket-only temporary cluster and teardown.
No system-wide installation or production connection was used.

23 PostgreSQL integration tests passed, including publication batches, execution
ledger, pilot transactions, manager provenance, and the migration chain. After
the collector changes, the same 23 tests passed again. 117 selected synthetic
tests also passed.

Research collector fix: execution can hash the original aware ISO timestamp
text, whereas the collector previously accepted only integer milliseconds.
The collector now validates and preserves either original representation; it
does not convert equivalent timestamps into interchangeable IDs. Tests cover
Z/offset/space representations, invalid identities, and unchanged integer
support. Malformed nested order responses now fail with an explicit evidence
error. No order-placement code changed; eligibility remains false.

The automatic candidate loader is NOT implemented. Original execution input
provenance still needs a trustworthy source: reconstructing an ISO string from
a database timestamp does not prove it is byte-for-byte identical to the value
originally hashed. The loader must reject or quarantine missing original
identity, not try alternate representations until one happens to match.
The scalp book is separate from `signals` and must not be folded into its UUID
linkage without a separately designed identity mapping.

Next: define and test a bounded loader contract using original persisted client
order identities, explicit account/network/environment/cohort, and provenance.
Do not activate collection, change execution identity generation, or backfill
guessed links as part of that work. These changes remain local and unmerged.

## Scope and result

Local code and synthetic-test audit only. No production data queries, venue
requests, orders, deployment changes, or held-out outcomes were used in this
audit. This is not a certification of the latest deployed application.

The earlier operational pilot verified 96 consecutive COMPLETE collection
slots. Its caller supplied `candidates=[]`; that result does not demonstrate
signal attribution, trade outcomes, or ML eligibility.

## Existing safeguards

- `execution_evidence.collect` links candidate signal IDs to venue order IDs
  only after a deterministic client-order-ID and instrument match.
- The ledger rejects conflicting execution identities and order links.
- Lifecycle checks exclude foreign/unlinked fills, incomplete position chains,
  multiple cycles, ambiguous timestamps, missing funding, and unsupported fees.
- Publication timing requires an accepted receipt with a positive message ID
  and response time strictly before the first fill. Cross-clock uncertainty
  remains explicitly unresolved.
- Lifecycle reports always return `training_eligible=False`; passing linkage
  is not permission to train.

## Verification this session

86 synthetic tests passed across collector, account evidence, position
reconciliation, lifecycle, receipt timing, and manager journal modules.
Added three cases: long and short partial entry followed by stop exits, and
an order link without fills that must not create a trade.
`git diff --check` passed.

12 selected PostgreSQL tests skipped without a disposable DSN. The bundled
socket-only disposable runner could not start because `pgserver` is absent
from the current Python environment. These database checks are NOT counted as
passing in this checkpoint. Never point them at production Neon.

## Next implementation gates

1. Restore an isolated disposable PostgreSQL test runtime and rerun execution,
   manager-provenance, publication, and migration-chain integration suites.
2. Design a bounded candidate loader from original persisted signal/order
   metadata, with explicit environment/account/network/cohort filters. Do not
   derive candidates from recomputed recommendations or match by symbol alone.
3. Compose candidate/order provenance and lifecycle loading in a consistent
   read-only database snapshot. Include missing evidence and exclusions.
4. Validate cancellations, replacement protective orders, crash/uncertain
   acknowledgements, and partial journal writes end to end. Existing unit tests
   are not proof of venue behavior or durable recording across process crashes.
5. Independently establish history coverage and clock uncertainty before any
   training-eligibility decision. Keep uncertain rows quarantined.

No new collection cohort or trade is authorized by this report. No live ML
integration, production flag activation, PR publication, or merge was performed.
