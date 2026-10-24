# Execution evidence — observational, disabled by default

This collector fixes the missing durable fill-capture mechanism, not the missing
historical evidence itself. It never places orders, changes risk settings, sends
Telegram messages or promotes ML labels. No scheduler or API route enables it.

## What it records

Migration 020 adds immutable collection runs, normalized fill evidence and
venue-verified order-to-signal links. Network and hashed account identity isolate
mainnet/testnet and accounts. Repeat fills deduplicate; changed payloads for an
existing execution ID abort the entire transaction. Decimal strings preserve
prices and quantities. Each fill retains fee currency, fee and starting position.

Original persisted signal UUID, causing candle timestamp, intent (open/sl/tp1/tp2)
and mapped venue instrument are supplied as candidate metadata. The collector
derives the existing deterministic client order ID and checks the venue's echoed
client ID/instrument before linking. Symbol alone is never an identity join.

Only the Hyperliquid info endpoint is used. The official API limits responses to
2,000 fills and retains only the most recent 10,000. Capped responses fail closed;
an uncapped response still does not certify full history. No cursor is advanced.
Source: https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint

## Rollout gates

1. Pass disposable PostgreSQL integration tests in CI. Local PostgreSQL 16.15
   verification passed on 2026-10-22 (4 database tests, plus 19 collector tests).
   CI is still pending. Never use production Neon for tests.
2. Review and apply migration 020 explicitly. No automatic migrations.
3. Configure account address and explicit network. Do not provide signing keys.
4. Run a short prospective dry-run with original candidate metadata.
5. Inspect outcome-blind counts; then authorize writes and scheduling separately.

Example (environment variables are already configured privately):

```sh
python scripts/collect_execution_evidence.py --network testnet \
  --start-ms START_MS --end-ms END_MS --candidates candidates.json
```

Windows are inclusive, at most 24 hours, and may not extend into the future.
Candidates is a JSON list with signal_id, candle_ts, intent and instrument.
Writes require both `EXECUTION_EVIDENCE_ENABLED=true` and `--write`; output contains
counts only. CLI does not implicitly load .env. Never print the database URL or
account credentials. Collection reads financial events: do not invoke it for
the sealed October evaluation period as part of development. Synthetic tests
are safe; prospective collection scope must be decided before rollout.

## Funding and current position capture (migration 021)

Pass `--account-evidence` to additionally call `userFunding` for the requested
window and `clearinghouseState` for current positions on the first perpetual DEX.
Writes require migrations 020 and 021, the existing opt-in flag, and `--write`.
All evidence writes share one transaction. A missing table or changed funding
event rolls back the entire run, including fills and order links.

Funding keys combine provider hash, event timestamp, and instrument. Signed USDC
amounts, position size, rate and optional sample count are retained; no conversion
into trade P&L is performed. Responses with 500 or more funding rows fail closed
and require a narrower window. An uncapped response does not prove completeness.
An explicit collection observation records zero funding rows as well.

Position observations retain venue time, request/response timestamps, DEX scope
and signed sizes only. They omit balances, entry prices and unrealized P&L.
They are current observations, NOT reconstructed historical boundaries. Stale
(over 60 seconds) or future-dated venue snapshots are rejected. Duplicate assets
and instruments outside the first perpetual DEX are rejected. HIP-3 and spot
positions are not covered. Funding responses may contain other DEX instruments;
these must not be reconciled against this narrower position snapshot.

Source: [Hyperliquid perpetual info endpoints](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals).

## Still required before an actual-trade dataset

Funding capture is implemented; funding attribution and complete position/quantity
reconciliation are NOT. Partial fills, fees in non-quote currencies, missing history,
manual/external orders and absent publication links must remain quarantined.
All records currently report training_eligible=false. No executed-trade returns
or ML labels are generated. Existing October outcomes remain sealed.

`backend/execution_reconciliation.py` now provides a pure, outcome-blind
position-chain audit with synthetic long/short and partial-exit tests. It checks
fills in (start, end] against supplied boundary positions, rejects conflicting
duplicates and mixed instruments, and quarantines tied timestamps rather than
assuming execution IDs give causal order. Boundary snapshots are inputs, not
independently captured or certified by this helper. Matching quantities alone
cannot establish history completeness. This helper is not wired to production.

`backend/execution_lifecycle_evidence.py` now checks one supplied flat-to-flat
trade interval. It matches fill order IDs to the original signal, reconstructs
signed quantities, and attributes funding event IDs only when position sizes
agree. It rejects ambiguous timestamps, foreign/manual fills, reversals,
multiple position cycles, missing funding observations, and request-window gaps.
All assignments are withheld if any check fails. Matching linkage still returns
`training_eligible=false`: request coverage is not proof of provider completeness.
This is a pure in-memory checker, not a DB loader or production job. It does not
compute returns or inspect historical research outcomes.

`backend/execution_evidence_loader.py` loads ledger bundles in a caller-owned
read-only, repeatable-read transaction. It bounds windows to 24 hours, 100 runs,
and per-query event limits. Immutable `execution_run_fills` (020) and
`execution_run_funding` (021) preserve each run's membership despite deduplication.
The loader validates counts, stored payload hashes, venue order-link evidence,
account/network scope, and signal/publication environment. A positive accepted
Telegram receipt must precede the first fill strictly. The output distinguishes
recorded timestamp ordering from verified cross-clock ordering; training remains
disabled. This is an internal utility with no CLI, endpoint or scheduled caller.

Next gates: code review/packaging, order lifecycle observations, independent
history-coverage and clock-alignment verification, and an explicitly selected
prospective cohort. No training eligibility can be enabled by passing these
checks alone. Never invoke the loader on reserved research periods during tuning.
Only after those gates can a prospective development cohort be certified.
