# Cloud testnet pilot review — 2026-10-26

## Scope and decision

Packaged locally on `codex/execution-pilot-cloud`, incorporating current main
`e4972c9`. Ready for review as a disabled feature, not automatic activation.
No public repository push, production migration, deployment, token creation,
schedule activation, venue read, Telegram send, or trade was performed in this
review. Existing trading and research jobs are unchanged relative to main.

The pilot uses an isolated Cloudflare Worker, a dedicated path-scoped token,
and an empty-body POST to the production-only Flask endpoint. The account is
configured separately from the trading account. Network is hardcoded testnet.
Its 96 slots are bounded to 24 hours, with a two-minute admission grace period.
Claims survive crashes, successful evidence and completion commit atomically,
and incomplete/failed/missing earlier slots block further collection.

## Verification

- Integrated full regression suite: 3,234 passed, 127 skipped (12,985 existing
  deprecation warnings). Database tests were run separately as described below.
- Full actual SQL migration chain 001–024 passed on disposable PostgreSQL 16.15
  with pgcrypto, preserving an existing synthetic signal.
- 21 database integration tests passed, including concurrent claims,
  rollback of failed evidence writes, replay suppression, and immutable storage.
- Worker checks cover disabled/expired behavior, late-window failure, successful
  request, and no retry on ambiguous failure; also included in pytest when Node
  is available.
- Review fixed cache prevention on endpoint error responses, late-start failure
  reporting, duplicate final-slot completion metadata, and migration docs.
- Local database cluster was stopped; production credentials were excluded.

## Deployment gates still pending

1. Publish/review the branch and require remote CI. Reconfirm migration 024 is
   still available immediately before merging.
2. Deploy backend disabled and explicitly apply migration 024 (020–023 already
   exist in the approved production database; do not rerun them blindly).
3. Configure a dedicated token privately in Vercel and the separate Worker.
   Confirm the approved new testnet account and a future quarter-hour start.
4. Configure and verify Cloudflare failure notification delivery. The Worker
   currently logs and fails invocations; it does not send its own Telegram alert.
5. Activate the bounded pilot explicitly. Verify the first persisted slot and
   independently check final completeness after expiry.

## Known limits

No independent watchdog detects a completely missed final invocation. The cron
registration remains after expiry but sends no collection requests. Failures
stop collection, not all subsequent cron invocations; these keep reporting the
blocked state until expiry or disablement. No original signal candidates are
provided, so fills remain unlinked. Zero-fill runs verify infrastructure, not
trade attribution. Cross-clock and history-completeness gates remain unresolved;
all records remain ineligible for ML training or live-trading decisions.
