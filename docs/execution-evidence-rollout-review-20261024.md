# Execution evidence rollout review — 2026-10-24 SGT

## Decision

Ready to package for a disabled-feature review PR, **not for automatic production
activation or ML training**. No push, PR, merge, production migration, deployment,
flag change, live venue call or outcome inspection was performed in this review.

Base commit: `6828c6e76dc15411ecf82c79d831af58962c57af`.
Local branch: `feat/execution-evidence-ledger`.
The review archive is an overlay of changed/new files, not a standalone checkout.
Earlier dated reports describe earlier checkpoints; this report supersedes their
migration-chain status and includes the manager evidence additions through 022.

## Review fixes and verification

- Fixed migration 017 bookkeeping: its existing repeatable script now registers
  version 017. Previously its table could exist while the runner continued to
  report the migration as pending. The change adds no signal/outcome mutation.
- Fixed null/malformed nested venue order responses to produce explicit
  quarantine issues instead of an unhandled attribute error.
- Added full-chain PostgreSQL testing of the actual 001–022 SQL scripts, without
  removing extension statements or substituting simplified tables. A synthetic
  signal seeded after 018 remains byte-for-byte equivalent as JSON after 019–022.
  Re-running 017 leaves one version record.
- All 20 selected PostgreSQL tests passed: full migration chain, publication
  batches, execution ledger, and manager provenance storage/loading. PostgreSQL
  16.15 with pgcrypto was used locally; the disposable server was stopped.
- CI configuration includes these tests. Remote CI is not yet run because these
  changes have not been pushed.
- Final non-database regression run: **3,086 passed, 124 skipped**, with 12,985
  existing warnings. Database tests were run separately as described above.
  `git diff --check` passed.

## What is implemented

The explicit read-only venue collector can capture fills and optional funding/
position observations. Writes require an explicit flag plus CLI option. The
manager journal is separately disabled by default and preserves original order
IDs and protective calls. Candidate linkage, independent order observation,
append-only persistence and read-only snapshot loaders are available as helpers.

There is **no automatic production pipeline** connecting all these helpers.
In particular, independent manager-order observation has no deployed job/caller.
The composed manager checker still accepts a caller-supplied lifecycle bundle;
that caller must load verified ledger data in the same consistent snapshot.

## Unresolved gates

1. Independently bounded clock uncertainty and complete provider history remain
   unproven. Timestamp consistency and balanced quantities do not establish them.
2. Manager observations are buffered until protective calls finish. Crashes can
   lose the buffer, and separate event writes can leave partial journals. Those
   records must remain quarantined; this is not a transactional order outbox.
3. A prospective development cohort, account/network, retention policy and
   narrowly scoped collection caller must be agreed before activation.
4. No end-to-end production signal-to-exit verification has been completed.
   No code promotes these reports to verified attribution or ML eligibility.
5. The sealed October research period remains untouched. Do not use its outcomes
   to debug or tune these changes.

## Controlled rollout checklist — each external stage needs approval

1. Publish a review PR, compare against current main, resolve any concurrent
   migration-number/schema changes, and require CI plus code review.
2. Inspect production migration status without modifying data. Do not blindly
   run the complete pending queue. The local base may lag current production.
3. Review the 017 bookkeeping correction and new migrations 019 → 020 → 021 →
   022 against the actual deployed schema. Apply only the approved migrations.
4. Deploy code with both evidence flags disabled. Verify existing behavior and
   deployment health before considering any collector writes.
5. Define a bounded, outcome-blind prospective pilot. Test a dry-run first, then
   separately authorize storage and the manager journal. Do not place a trade
   or invoke a trading cron solely to manufacture test evidence.
6. Check missing evidence and exclusions as well as successful records. Only
   then propose scheduling. Keep ML and new live-trading decisions disabled.

Rollback activation by stopping collectors and disabling their flags. Retain
append-only audit tables; do not delete evidence or undo schema blindly.
