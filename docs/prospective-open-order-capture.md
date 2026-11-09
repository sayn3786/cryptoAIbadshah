# Prospective opening-order identity capture

This is an inactive testnet-only observation hook in `hl_exchange.open_position`.
No caller passes the new observer by default. No scheduler, flag, account,
endpoint, trade, production migration or historical backfill is changed.

An explicitly supplied `scoped_observer` freezes at most ten signal UUIDs, an
environment, a hashed execution account, and a window of at most 24 hours.
It drops events outside that scope. Mainnet calls are not observed.

The hook records original signal UUID and candle representation, exact client
order ID, instrument, local intent/response times and a single returned venue
order ID when unambiguous. Invalid identities are not reconstructed. Exceptions
are recorded as UNKNOWN without private exception text. Rejections and malformed
acknowledgements are not promoted to links. Recorder failure never retries an
order, changes its return value or blocks protective handling.

Migration 025 provides an append-only journal, separate from venue-verified
execution_order_links. `persist_event` requires a caller-owned transaction.
Apply the migration only after disposable-database testing and explicit approval.
The sink's account is caller-supplied and still requires independent verification.
Missing INTENT or RESPONSE events, including failures across the window boundary,
remain incomplete; no reconstruction is allowed.

This captures opening orders only. Before activation: review the migration and
integration tests; supply a separately approved testnet cohort/account; wire a
durable sink; add exit-order identity capture and venue echo verification.
Do not invoke any order solely to generate evidence. Existing scalp identities
are not silently converted into signal UUIDs. Training eligibility and verified
attribution remain false. No profitability or model-readiness claim follows.

## Local verification — 2026-11-09

- 128 focused synthetic tests passed, including guarded order handling,
  rejection/unknown responses, incorrect identities, sink failure and expiry.
- 31 disposable PostgreSQL integration tests passed without skips, including
  the complete migration chain through 025, durable capture, duplicate/mutation
  rejection and signal environment isolation.
- PostgreSQL 16.15 was rebuilt under `/private/tmp/cryptostars-pg-20261109`
  from the official source archive, SHA-256
  `c1575341fa7bd40f5274ea465b34390f4dc64cdd0770af327005caaeb9f6b7ed`.
  The test runner stripped database/secret environment settings, used a new
  socket-only cluster and shut it down after testing. No Neon data was used.
- Default/mainnet execution bypasses the observer import and keeps the original
  send path. No production migration or observer activation has occurred.

Reproduce database checks with `TEST_POSTGRES_BIN` pointing to that installation's
`bin` directory and `scripts/test_disposable_postgres.py`; run the existing six
PostgreSQL test files listed by the CI workflow. Publication and CI review are
still required before merge.

## Initial exit capture — 2026-11-09

`send_exit_orders` accepts optional `evidence_signal` and `evidence_observer`
arguments. Neither is wired by existing callers. With an explicitly supplied
scoped testnet observer, each actual SL, TP1 and TP2 send gets its own correlation
ID and INTENT/RESPONSE or UNKNOWN pair. Missing legs generate no events. If a
later send raises, earlier acknowledgements remain recorded; no retry or extra
protective order is introduced. The existing exception propagation is unchanged.

The shared observation code now takes an explicit intent; `observed_open` keeps
its original interface and always uses `open`. `persist_event` accepts opening
intents only; `persist_exit_event` accepts SL/TP1/TP2 only and writes to the
separate append-only `exit_order_observations` table supplied by migration `026`.
An eventual durable sink must route by intent explicitly. Migration `026`
depends on `025`; neither has been applied to production in this work.

Scope remains initial separate exit orders, not manager-created replacements,
manual closes, bulk scalp orders or a complete lifecycle. Venue echo/account
verification and a scoped caller are still required before activation. An SDK
acknowledgement alone is not venue-verified evidence and does not create an
`execution_order_links` row or make any record eligible for training.

Verification: 101 focused synthetic tests and 32 disposable PostgreSQL tests
passed, including the migration chain through `026`, journal immutability and
opening/exit intent separation. No production data, orders or held-out outcomes
were accessed.
