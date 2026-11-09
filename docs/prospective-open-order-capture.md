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
