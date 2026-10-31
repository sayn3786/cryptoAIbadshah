# Isolated cloud testnet pilot (disabled)

Cloudflare cron → authenticated Flask endpoint on Vercel → existing Neon.
This Worker does not share the trading scheduler's tokens or job list.
No laptop, signing key, order placement, or historical outcome query is needed.

## Before activation

1. Review/merge the backend, migration and Worker. Confirm migration number 024
   is still unused on current main. Deploy the backend with the feature disabled.
2. Apply `024_execution_pilot_slots.sql` after migrations 020–023, explicitly.
   This operational table is mutable; the underlying evidence remains immutable.
3. Create a dedicated `EXECUTION_PILOT_TOKEN` privately in Vercel Production and
   this Worker. Do not reuse CRON_SECRET, SCHEDULER_TOKEN or a signing key.
4. Configure Vercel Production `EXECUTION_PILOT_TESTNET_ACCOUNT` for the approved
   separate testnet address. Do not change HYPERLIQUID_ACCOUNT_ADDRESS: it belongs
   to the existing trading system. Preview is rejected using VERCEL_ENV.
5. Choose a future UTC quarter-hour boundary as
   `EXECUTION_PILOT_START_UTC_SECONDS`, identical in both deployments. Configure
   `EXECUTION_PILOT_ENABLED=true` only for the approved pilot. The backend's
   dedicated flag authorizes these writes; it does not enable generic collectors.
6. Deploy this directory as a separate Worker. Its checked-in settings are OFF.
   Do not deploy it over `cryptoaibadshah`, the existing trading Worker.
7. Disable automatic Git builds for this isolated Worker so an unrelated merge
   cannot overwrite an approved pilot window. Keep the trading Worker unchanged.
8. Reuse Vercel's existing `weekly_report.send_private` sender, exactly as the
   scalp alerts do (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_REPORT_CHAT_ID`, and existing
   private mirror recipients). Do not copy Telegram secrets to Cloudflare.
   Deploy the authenticated `/api/research/execution-pilot/alert` backend route
   BEFORE deploying this Worker. Verify a test alert before activating collection.
   Old Worker Telegram secrets and diagnostic timestamps are unused by this code.

## Bounds and failure behavior

The cron wakes at :01/:16/:31/:46. The backend accepts only an empty POST, with
the dedicated token, during the two-minute grace period after each slot boundary.
Account/network/window cannot be supplied by the caller. Network is hardcoded
testnet. There are 96 quarter-hour windows, ending start+24h. Outside that period
the Worker makes no API request; its cron registration still exists until removed.

Neon serializes admission with a transaction-scoped advisory lock and a unique
pilot/slot record. A STARTED claim commits before any venue read. Successful
evidence and COMPLETE status commit together. A crash leaves STARTED; a handled
failure leaves FAILED unless the evidence already committed. Either blocks later
slots, as does a missing earlier slot. No retries or historical backfill occur.
Repeated completed slots return duplicate without rereading the venue.

On failure: disable the dedicated flag, inspect operational slot records and
run IDs, and reconcile uncertain commits. Never delete a claim to force a retry.
A new cohort/start requires explicit operator approval. Final completion is
reported in slot 96's response and Cloudflare logs. A final missed invocation
must be detected by an operator's end-of-pilot completeness check; this is not
an independent watchdog.

Each failed invocation attempts one authenticated Vercel alert relay request
(25-second timeout, no redirects or retries), then still fails for Cloudflare monitoring. Alerts
contain only a bounded slot number, an allowlisted error category and fixed
operator guidance, never response payloads, account details, P&L or secrets.
Disabled, outside-window and successful invocations do not send alerts.
The existing sender's success result is returned; this is not independent
recipient/message-ID verification and does not prove the user read the message.
Delivery failures/uncertain timeouts
are logged without raw errors. There is no durable notification deduplication:
later failing invocations can alert again. A Worker outage or missing cron cannot
notify itself; retain independent monitoring and the final completeness check.

For an operator-approved delivery test while ENABLED is exactly false, set
`EXECUTION_PILOT_ALERT_TEST_UTC_SECONDS` to one future existing cron timestamp.
Only an event within that scheduled minute, received within 120 seconds, sends TEST ONLY
instead of running collection. It expires automatically; remove the variable
after checking the receipt. No retry is made, but platform duplicate delivery
is not durably deduplicated. Never use this setting to test actual collection.

## Verification and limitations

Inspect only execution_pilot_slots and linked run/account-observation metadata.
Expect 96 COMPLETE slots, matching run IDs and account/network scope. Check gaps,
duplicates, and unlinked events, not P&L. Zero fills is valid but does not prove
trade tracking. No signal candidates are supplied, so fills are not attributed.
All training eligibility remains false. Collection does not establish provider
history completeness, cross-clock guarantees, or trustworthy trade labels.

Venue and Telegram credentials remain in Vercel; the Worker receives only its
narrow endpoint token. The relay accepts fixed categories and a bounded slot,
never caller-provided message text or recipients. It is production-only; test
alerts require the backend pilot to be disabled and operational alerts require
it to be enabled. A Vercel outage also prevents relay alerts, so independent
Cloudflare monitoring is still required. No
production data or held-out outcomes are used by the local synthetic tests.
