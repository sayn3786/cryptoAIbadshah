# Manual ML research testing on Vercel

POST `/api/research/ml/collect` and POST `/api/research/ml/label` run separate,
synchronous research jobs using the deployment's existing DATABASE_URL. The
routes perform no signal generation, orders, or notifications. Migrations
013 and 014 must already be applied for database operations. No new migration.

## Enable explicitly after deployment

Set `ML_RESEARCH_ENABLED=true` in the intended Vercel environment and redeploy.
The existing CRON_SECRET must be configured. Every request requires either
`Authorization: Bearer <CRON_SECRET>` or `x-cron-secret: <CRON_SECRET>`. Never put
this secret in a URL, public frontend bundle, screenshot or chat. Dashboard login
alone is not sufficient. Missing secret or switch keeps these endpoints closed.

Use a trusted HTTP client with secret-header storage. These are POST requests,
not browser address-bar links. Start with JSON:

```json
{"symbols":["BTC","ETH"],"source":"okx","write":false}
```

Send this to `/api/research/ml/collect`. After inspecting successful dry-run
results, change `write` to true to record snapshots. The label endpoint accepts
the same body plus `limit` (default and maximum 10). Run it after the next hourly
open plus four hours. A successful response with `attempted: 0` means no matching,
mature, retry-eligible rows; it does NOT mean outcomes have been inserted.

Symbols are limited to BTC/ETH, at most two distinct symbols. Source is one of
okx (default), binance or bybit. Each source uses one HTTP request per symbol,
with connect/read timeouts and no pagination, fallback exchange or synthetic
prices. Source errors return a non-success status. An exchange may be unavailable
from a Vercel region. A failed write collection records an invalid observation;
first-insert-wins means it cannot be overwritten within that slot/version.

For labels, choose the SAME source as the saved snapshots (inspect the `source`
column). The cloud job filters by source BEFORE batching and never changes an
observation's exchange. Historical CLI records from other exchanges remain for
the CLI/backfill workflow. A record beyond the retrieval window goes to backfill;
other label failures use the existing retry queue. Dry runs never alter queue
state, while label dry runs still read the database.

Production deployments use namespace `research`, matching the manual test.
Other deployments use `research_<deployment environment>`; callers cannot set
this through JSON. Ensure SIGNAL_ENVIRONMENT does not incorrectly override a
preview as production. All previews share their preview research namespace.

Responses: 200 completed/no eligible labels; 400 malformed input; 401 invalid
secret; 503 disabled, unavailable DB/provider, invalid data, or job failure.
Partial/invalid collection may have stored invalid rows when write=true; check
counts rather than treating a 503 as proof of no writes. Database exceptions are
sanitized. Duplicate writes insert zero without overwriting prior evidence.

The current app config has a 60-second maxDuration. Bounded batches reduce timeout
risk but network/DB latency can still exceed it. Retry is data-idempotent; inspect
the database after a timeout. Do not increase scope to the full universe inside
this endpoint. Requests are synchronous; no unawaited background work is launched.
No cloud endpoint or live production DB was invoked during unit tests.

For the opt-in deployment/merge of the automatic GitHub Actions caller, see
[research scheduling](ml-research-scheduling.md). The endpoint itself does not
start a scheduler.

Disable by removing ML_RESEARCH_ENABLED or setting it to false and redeploying.
No schema changes are needed merely to deploy these routes. Before enabling the
separate scheduler, complete an authenticated cloud collect/label test and
configure its GitHub Actions credentials as described in the scheduling guide.
