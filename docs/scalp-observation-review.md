# Migration 027 and inactive collector review

PR preparation, 2026-11-24: rebased onto current main `8c20c76`.
Validation now reports 109 passed, 1 skipped for the six suites listed below.
The emergency protection test covers both closure outcomes with successful and
failed journal flushes; persistence failure preserves the protection result.
The migration is unchanged. This patch remains inactive and is for review only.

Prepared 2026-11-22. Review only; no production schema or configuration changes.

Updated 2026-11-23: inactive integration patch prepared on
`review/scalp-inactive-integration`, based on fetched main `c141646`.
`_scalp_market` now accepts an optional `observation_capture` and runs its entire
workflow inside that capture. No caller supplies it. The emergency protection
test now exercises this entry point directly, for both confirmed and failed
emergency closure. The existing low-level wrapper injection remains available.
The capture is single-use; callers must not reuse it or retry the trading
workflow after a capture error.

Updated validation: 107 passed, 1 skipped across collector, scalp workflow,
opening/exit capture, scalp lean filter and live money lean tests. `git diff
--check` passed. Migration 027 is unchanged; the disposable persistence test
result below remains applicable. No production actions were performed.

## Repository baseline

The working checkout was clean on `feat/scalp-observation-journal` at
`cfd5c4e150c941ad1cdeaa08f201c4c7424d857c` before this document was added.
Its tree matches merged PR #455 (`c3e07dff05cea3f19456110d659a58bec0a9c19d`).
Fetched `origin/main` is `c141646`; migration 027, the collector module and its
unit/persistence tests are unchanged there. Main contains subsequent trading
changes, so any future integration patch must be based on current main rather
than replaying this old feature branch. Deployment status is supplied by the
owner; this review did not independently inspect production.

## Schema decision: apply separately, only with approval

Source: `database/migrations/027_scalp_order_observations.sql`.

- Requires `schema_migrations` (001) and
  `reject_execution_evidence_mutation()` (020) in the target search path.
- One explicit transaction creates the table, scope index, mutation trigger and
  version record. There is no backfill or write to existing trading tables.
- Testnet-only rows; environment/account/symbol/slot namespace; unique
  correlation + phase; training eligibility constrained to false.
- UPDATE and DELETE are rejected by the shared trigger. This is not protection
  against privileged TRUNCATE, DDL or trigger bypass; review runtime grants.
- This is a one-time migration, not an idempotent script. A duplicate table or
  version fails the transaction. Resolve partial/manual schema discrepancies
  before execution; do not add automatic retries or silently skip conflicts.

Before any approved application, inspect target identity, search path, migration
ledger, existing relation, shared trigger function and runtime grants using a
read-only connection. Useful catalog checks (not executed against production):

```sql
SELECT current_database(), current_schema(), current_user;
SHOW search_path;
SELECT version, description FROM schema_migrations
WHERE version IN ('001', '020', '027') ORDER BY version;
SELECT to_regclass('scalp_order_observations'),
       to_regprocedure('reject_execution_evidence_mutation()');
```

After an approved application, inspect columns, checks, unique constraint,
index, trigger and ledger entry; do not insert synthetic production rows.
If later collection fails, detach the caller and retain evidence. Destructive
schema rollback is a separate decision; do not drop this append-only journal.

## Collector decision: remains inactive

No production caller constructs `BufferedCapture`; no environment variable
activates it. `_scalp_market(..., observation_order_functions=None)` preserves
the existing path by default. The low-level exit hook is testnet guarded.

The eventual caller must explicitly bind one environment, account hash,
symbol, slot and timezone-aware window of at most 24 hours. Hash the lowercase
configured address and compare it with a fresh account snapshot. Existing
guards establish configuration consistency, not independent venue identity.

Review the integration around this sequence:

1. Construct one single-use `BufferedCapture` for an already eligible scalp.
2. Pass its optional wrappers into the complete `_scalp_market` workflow.
   Keep existing sizing, eligibility, order IDs and protection handling intact.
3. Allow opening, initial exits and emergency protection/audit to finish.
4. Flush the batch using one caller-owned transaction with `persist(event, c)`
   for each event. Do not use a synchronous database sink inside order hooks.
5. Inspect `flush_status` locally. A flush failure preserves the workflow result
   or exception; never retry the trading workflow to obtain observations.

Eight events maximum cover opening plus three initial exits. Overflow discards
the batch. A process crash can lose the in-memory batch. RESPONSE records are
acknowledgments, UNKNOWN records are ambiguous, and neither proves a fill.
`FLUSHED` only means the callback returned. Attribution remains unverified and
training eligibility remains false. No reconstruction or promotion is included.

Future activation review must cover actual wrapper sends, normal protection,
failed protection/emergency close, changed account, expiry, flush rollback,
overflow and absent observer. Current emergency protection tests exercise the
real application workflow with mocked execution/wrappers, not a live venue.
Independent orders/fills verification remains separate work. No trade should
be created solely to produce evidence.

## Local validation

On this checkout, with dotenv disabled and scheduler disabled:

- `python3 -m pytest -q tests/test_scalp_order_observation.py tests/test_scalp_live.py tests/test_open_order_capture.py tests/test_exit_order_capture.py`
  — 100 passed, 1 skipped.
- `TEST_POSTGRES_BIN=/private/tmp/cstars-pg-scalp/install/bin python3 scripts/test_disposable_postgres.py -q tests/test_scalp_observation_postgres.py`
  — 1 passed on disposable PostgreSQL 16.15, socket only; cluster stopped and
  removed. Covers persistence, separate namespace, duplicate rejection,
  mutation rejection and eligibility/network rejection.

No production migration, collection activation, Telegram message or order was
performed. Schema application and collector activation each require explicit
owner approval; this document authorizes neither.
