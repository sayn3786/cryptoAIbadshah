# Execution evidence package review — 2026-10-23 SGT

## Verdict

Suitable for review as disabled-by-default evidence infrastructure. **Not ready
for automatic production collection, certified trade-outcome training, or ML
execution.** All changes remain local. No push, merge, migration or deployment
was performed as part of this review.

Package base commit: `6828c6e76dc15411ecf82c79d831af58962c57af`.
Branch: `feat/execution-evidence-ledger`. The archive contains only the explicitly
listed changed source, tests, migration drafts and documentation; it is an overlay
for review, not a standalone application or an automatically applied patch.

Final verification: **3,050 passed, 113 skipped** in the full regression suite;
**14 passed** in the separate loader/PostgreSQL run (7 database tests). Temporary
PostgreSQL was stopped afterward. `git diff --check` passed.

## Findings and disposition

1. **Fixed: run membership lost after deduplication.** The 019/020 drafts now
   include immutable run-to-fill/funding membership, count checks and replay tests.
2. **Fixed: loader queried unrelated historical order links.** Candidate link
   loading is restricted to instrument fills in the selected boundary window.
   The requested signal's own links are used only to identify its instrument.
3. **Fixed: fill-run receipt timestamp was taken before provider calls.** It is
   now captured after collection (the explicit synthetic-test clock is retained).
4. **Open, blocks complete execution attribution:** `hl_manage.py` derives
   breakeven and remainder-close IDs from symbol and price rather than a signal
   UUID. The new collector only supports open/sl/tp1/tp2 signal-derived IDs.
   Manager-generated exits are therefore unlinked and quarantined. Do not infer
   ownership by symbol alone or modify live order identifiers during this rollout.
5. **Open, blocks certification:** complete provider history, cross-clock
   alignment, lifecycle status evidence, full schema-chain upgrade validation,
   and a prospectively selected development cohort are not established.

## Review boundaries

Synthetic fixtures and disposable local PostgreSQL only. No account queries,
production data access or held-out research outcomes. Hashes establish payload
consistency, not provider authenticity. An accepted Telegram API receipt does
not prove that a user read the message. No code sets training_eligible=true.

## Proposed rollout (requires separate approval)

1. Publish this disabled feature branch as a review PR; run CI and review diffs.
2. Validate the full existing schema plus 019 then 020 in a disposable database.
   Do not use `migrate.py up` against an uninspected production migration queue.
3. Agree account/network, permitted prospective window and data retention.
4. Apply reviewed migrations explicitly; deploy with collection disabled.
5. Run a narrowly scoped dry-run; inspect only operational counts and exclusions.
6. Authorize a small write pilot, then scheduling separately. No ML promotion.

Rollback of activation means stop the external caller and unset the evidence
write flag. Preserve append-only evidence; do not drop tables to roll back code.
The CLI is dry-run by default and --write additionally requires the evidence flag.

## Next engineering priority

Design a durable causal mapping for manager-created exit orders, preserving
existing trading behavior. First add observational order-lifecycle records and
synthetic attribution tests; do not place, cancel or replace live orders merely
to create cleaner evidence. This limitation should be resolved before describing
the dataset as covering complete managed trades.
