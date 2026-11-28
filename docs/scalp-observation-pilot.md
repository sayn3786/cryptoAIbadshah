# Scalp observation pilot — approval draft

Prepared 2026-11-28. Inactive. This is the scalp order journal pilot, separate
from the account-info runner in `testnet-observation-pilot.md`.

## Release gate

PR #481 code review completed without findings at `8a43719`; its test workflow
passed. Its preview failed before usable build logs with Vercel's
`BUILD_FAILED: Resource provisioning failed`. Preview validation is unresolved;
do not treat passing unit tests as deployment validation. No merge or release
is authorized by this runbook. Review the final commit and CI after any update.

## Proposed first cohort

One explicitly selected testnet account, one symbol, one setup slot and one
capture instance; at most eight events. Propose a one-hour UTC window (the
adapter's hard ceiling is 24 hours). Capture only an already eligible market
scalp and its initial SL/TP orders. If no eligible scalp occurs, finish EMPTY;
do not manufacture a trade, loosen a trading filter or extend the window.

Before activation the owner and operator must record these concrete values:

| Field | Required value before approval |
| --- | --- |
| Code | Reviewed deployed commit containing the integration |
| Schema | Verified migration 027 ledger entry and catalog checks |
| Environment | Explicit deployment namespace |
| Network | testnet |
| Account | Privately verified execution address; lowercase-address SHA256 in journal |
| Symbol and slot | One supported symbol and existing eligible setup slot in milliseconds |
| Window | Exact timezone-aware UTC start/end, no more than one hour proposed |
| Operator | One named operator observing completion; no concurrent instance |

No address, credentials, scheduled start or slot has been selected here.
The application does not construct a capture today. Scheduler-level selection,
cross-process single-use enforcement and operational status reporting still
need a reviewed implementation before unattended activation. The single-use
Python object alone cannot enforce one capture across serverless invocations.

## Schema approval and verification

Follow `scalp-observation-review.md` for read-only preflight and migration
dependencies. Obtain explicit approval to apply 027 to the verified target.
Apply the exact reviewed SQL once, then inspect its version entry, checks,
scope index and immutable trigger. Do not insert synthetic production evidence.
Schema approval does not approve collection, deployment or test trades.

## Inactive integration contract

The approved future caller will pass a fresh `BufferedCapture` through
`_scalp_market(..., observation_capture=capture)`. The complete existing
workflow, including emergency protection, must remain inside that call.
Supply a fresh testnet account snapshot and the reviewed fixed scope. The
scope check verifies configuration consistency; independently verify venue
account identity before approving collection.

The flush callback must have this transaction shape (illustration only;
not connected to a scheduler, engine or exchange):

```python
def flush(batch):
    with engine.begin() as connection:
        for event in batch:
            persist(event, connection)
```

The engine must be supplied explicitly by the reviewed caller; never create
a connection or persist within an order hook. Never catch individual insert
errors and continue the transaction. Never retry a failed/ambiguous batch or
the trading workflow to recreate evidence. A failed transaction must leave
zero rows from that batch; the disposable PostgreSQL test covers this.

## Completion and stop conditions

The operator records workflow protection outcome and `flush_status` separately.

| Status | Interpretation and action |
| --- | --- |
| EMPTY | No captured events; inspect eligibility/scope/window without retrying trades |
| FLUSHED | Callback returned; read back the scoped batch before accepting storage completeness |
| FAILED | Transaction failed or outcome ambiguous; stop further capture and reconcile read-only |
| OVERFLOW | More than eight events; whole batch discarded; stop and inspect scope |
| NOT_RUN | No completed capture invocation; investigate without assuming orders occurred |

Stop further observation on account mismatch, unexpected symbol/slot/network,
duplicate invocation, expiry, incomplete batch or persistence failure. Leave
existing trading/protection running unchanged; never cancel protection to stop
observation. Account mismatch/expiry may suppress individual events without
discarding earlier ones, so FLUSHED does not establish a complete cohort.
Process loss before flush may leave no journal; do not reconstruct missing rows.

## Evidence acceptance

Read back only the approved environment/account/symbol/slot/window. Group by
correlation ID; each observed call should have INTENT plus RESPONSE or UNKNOWN.
Document gaps and UNKNOWN outcomes explicitly. Match deterministic client IDs,
venue order IDs, sides and timestamps against independent venue orders/fills
before claiming attribution. Acknowledgments alone do not prove execution.

Record expected calls versus observed pairs, missing/ambiguous calls, batch
commit status and protection outcome. A single cohort only validates plumbing;
it cannot establish reliability rates or profitability. All rows remain
`training_eligible=false` and attribution unverified; no automatic promotion.
This pilot does not collect full lifecycle exits, manager replacements, manual
closes or complete costs, so it cannot yet produce trustworthy ML P&L labels.

After review, the next deliverable is a complete, independently verified
trade-lifecycle dataset with costs and coverage reporting, followed by offline
comparison against unchanged trading decisions. No ML trading change belongs
in this pilot.

## Approval boundary

Still pending: preview validation, merge/release decision, exact cohort values,
operational caller implementation, production schema approval and separate
collection approval. No Telegram delivery, order placement, migration or
collection activation is performed by this document or its tests.
