# Manager execution observations

Implemented locally on 2026-10-23; not deployed or enabled in production.

Migration 022 adds an append-only journal for breakeven stop replacements and
remainder-close calls. It depends on the immutable-trigger function in migration
020. Apply the normal migration sequence only after review; no migration is
automatically applied by the manager.

The feature is off unless `MANAGER_EXECUTION_EVIDENCE_ENABLED=true`, with a valid
`HYPERLIQUID_ACCOUNT_ADDRESS` and explicit mainnet/testnet context. This is separate
from the read-only collector's `EXECUTION_EVIDENCE_ENABLED` flag.

## Behavior and guarantees

- Existing client order IDs, arguments, order placement, and cancellation behavior
  remain unchanged.
- Intent and response observations share a correlation UUID and preserve the
  observed reduce-only order IDs for the instrument. These IDs are candidates,
  **not proven causal parents or signal ownership**.
- Provider responses retain only resting/filled order IDs; arbitrary provider
  error text and P&L are excluded. A call exception produces an UNKNOWN event.
- Observations are buffered in memory. Database writes occur only after all
  protective actions finish, and each event is persisted independently.
- Journal failures do not prevent protective actions. The manager reports
  observed/recorded/failed counts when journaling is configured.
- A crash before flushing can lose the entire buffer; a partial flush can leave
  an unmatched intent or response. This is best-effort evidence capture, not a
  transactional outbox or a durable pre-order intent guarantee. `observed_at`
  and database `recorded_at` deliberately have different meanings.

## Limits and next gate

Every observation sets `attribution_verified=false`. Neither a successful call
nor matching an old stop alone proves which signal owns a position. A separate
resolver must combine venue-verified order links with an unambiguous position
cycle, rejecting mixed/manual positions, incomplete history, missing responses,
and ambiguous timing. No current journal event is promoted into a training label.

No real venue calls, production database writes, Telegram sends, or held-out
outcome inspection were used for verification. Local targeted tests passed:
27 manager/journal tests and 8 disposable PostgreSQL integration tests, including
append-only enforcement for migration 022. The disposable server was stopped.

Rollback is to disable the flag; retain existing journal rows for audit.

## Candidate linkage checker (2026-10-24)

`backend/manager_exit_linkage.py` now checks a supplied immutable journal pair
against account scope, payload hashes, matching action context, phase ordering,
and original protective-order links. All observed parent IDs must belong to the
same signal and instrument. It then trials the proposed exit link against the
existing flat-to-flat lifecycle checker without modifying its inputs. Foreign
fills, broken quantity chains, incomplete cycles and missing result-order fills
withhold the candidate link entirely.

This is a **candidate checker**, not verified ownership. It does not yet load
journal rows from Neon or independently fetch the result order from the venue.
The response's order ID alone cannot establish account ownership, reduce-only
semantics, or causality. Every result retains `attribution_verified=false` and
`training_eligible=false`, even when the candidate matches the supplied cycle.
Required next gates are independently observed venue order provenance, bounded
cross-clock timing, history completeness, and publication timing. Candidate
links must not be inserted directly into the verified order-link ledger.

Synthetic long/short and adversarial tests cover missing/duplicate phases,
altered hashes, account mismatches, foreign parent signals, foreign/manual
fills, quantity gaps, mismatched context, reversed timestamps, and missing
result fills. No held-out outcomes or live APIs are used.

## Independent order observation and timing checks

`backend/manager_order_provenance.py` adds an injected read-only order-status
observer and a composed checker. The observer records a hashed account identity,
network, queried client ID, request/response times, and a hash of the captured
payload. Its reader must be bound to the correct network. There is no production
caller, persistence migration, automatic collection, or trading API invocation.

The composed checker requires the earlier candidate/lifecycle checks to pass,
then checks the independently supplied venue response for the exact order ID,
client ID, instrument, reduce-only flag, and exit side. Missing venue fields fail
closed; the checker never substitutes presumed values. It also requires the
observation after the manager response, venue creation inside the action window,
and matching fills no earlier than order creation.

These comparisons assume zero clock tolerance and only establish consistency.
They do not establish synchronized clocks. Millisecond venue timestamps can be
rejected at sub-millisecond local boundaries; the checker deliberately does not
widen its window to obtain a pass. Independent clock bounds, durable observation
storage/loading, history completeness, and publication checks remain rollout
gates. All reports retain `attribution_verified=false` and
`training_eligible=false`. No actual account or October held-out outcomes were
queried during implementation.

## Durable observation storage and read-only loading

Migration 023 stores append-only independent order observations and ties each to
its manager response with an account/network-scoped foreign key. It requires
migration 022 (and the earlier immutable trigger function). It has not been
applied to production. Duplicate observation IDs are rejected, not overwritten.

`backend/manager_provenance_store.py` persists observations only through explicit
caller-owned transactions. It checks payload hashes, query identity, account
scope and request ordering before writing. The caller must explicitly approve
collection scope and persistence; there is no scheduler, implicit environment
lookup, automatic connection, or production entrypoint.

The loader selects one exact observation UUID and its two journal phases,
requires a read-only repeatable-read/serializable transaction, validates hashes
and envelope fields, and fails closed for missing or unknown phases. The composed
audit uses these database-loaded observations with a supplied lifecycle bundle.
The caller must load that lifecycle bundle from verified ledger evidence in the
same snapshot; this helper does not replace the broader lifecycle loader.

Clock bounds and independent history completeness are still unresolved. Durable
storage does not establish either guarantee, and no report becomes eligible for
training or live trading. Rollback means stopping the caller and retaining the
audit tables, not deleting their rows.
