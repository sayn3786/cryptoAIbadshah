# CryptoAIBadshah scheduler (Cloudflare Worker)

The app's clock. One every-minute Cloudflare cron trigger, and the UTC time
decides which jobs run:

| When (UTC) | Job | Endpoint | Token |
|---|---|---|---|
| every minute | Position manager: stop → entry after TP1 | `POST /api/hl/manage` | `HL_MANAGE_TOKEN` |
| hh:02, hh:12, hh:32 | **Publish signals** (+ HL auto-exec entry) | `POST /api/cron/publish` | `SCHEDULER_TOKEN` |
| hh:05, hh:35 | Outcome monitor (TP/SL hits, expiry) | `POST /api/signals/monitor` | `SCHEDULER_TOKEN` |
| 00:07, 08:07 | Telegram daily signals (+ Twitter) | `POST /api/cron/daily` | `SCHEDULER_TOKEN` |
| 00:15, 08:15, 16:15 | Pattern alerts → Telegram | `POST /api/patterns/alert` | `SCHEDULER_TOKEN` |

Why: GitHub's schedules are best-effort. Publishing ran 1–3 hours late or not
at all, which made signals late and HL entries stale; Cloudflare fires on time.
Publish runs three times an hour: the 4H slot publishes at :02 right after the
close, and :12 / :32 retry if exchange data lagged or a cold start timed out.
Once a slot is published, extra runs return `SLOT_ALREADY_PUBLISHED` in about a
second. Timeouts and 5xx errors are retried after 20 s (publish up to 3
attempts); auth errors are not.

Every job is safe to repeat (keyed per slot / candle / alert), so the GitHub
schedules stay as **backups and alarms**. Overlaps never double-send Telegram or
double-trade. The 12:05 UTC daily run stays on Vercel's own cron.

**Narrow tokens.** Neither token is `CRON_SECRET` or the HL admin token:
- `HL_MANAGE_TOKEN` opens only `/api/hl/manage` (it can only tighten stops).
- `SCHEDULER_TOKEN` opens only the four scheduled paths above, POST only. It
  cannot place orders directly, read the account or manage users.

A job whose token is missing is skipped (and logged); the others still run.

Cost: free. The Workers free plan includes cron triggers, and the Worker only
waits on your app (almost no CPU).

Cloudflare deploys the Worker **from this repo**: every push that changes this
folder rebuilds it. `worker.js` is the code; `wrangler.toml` sets the Worker
name, `APP_URL` and the every-minute cron.

## Setup (about 10 minutes)

### 1. Create the two tokens
On your Mac, run this **twice**, once per token:
```
openssl rand -hex 32
```
One value is `HL_MANAGE_TOKEN`, the other `SCHEDULER_TOKEN`. Use **different**
values. Each goes in two places (Vercel and Cloudflare). Never paste them into
chat, tickets or screenshots.

### 2. Add them to Vercel
Vercel → project → **Settings → Environment Variables** → add
`HL_MANAGE_TOKEN` and `SCHEDULER_TOKEN` (Environment **Production**) → Save →
then **Deployments → latest → ⋯ → Redeploy**. Each must be at least 16
characters, or it is ignored.

### 3. Import the repo in Cloudflare
<https://dash.cloudflare.com> → **Workers & Pages** → **Create** →
**Import a repository** → connect GitHub → pick **sayn3786/cryptoAIbadshah**.

This creates a Worker named **`cryptoaibadshah`** (after the repo). That name
must match `name` in [`wrangler.toml`](./wrangler.toml), which it does. If you
import under a different name, change `name` in `wrangler.toml` to match, or
every build fails instantly.

### 4. Build settings
Worker → **Settings → Builds**:

| Setting | Value |
|---|---|
| Build command | *(empty)* |
| Deploy command | `npx wrangler deploy` |
| Root directory | `cloudflare/hl-manage-worker` |
| Branch control | `main` |

The **root directory** matters. Without it, Cloudflare looks at the top of the
repo, finds no `wrangler.toml`, and the build fails in 0 seconds.

### 5. Add the tokens as Secrets
Worker → **Settings → Variables and secrets** → **+ Add variable** → set the Type
to **Secret** (not Text) → Name `HL_MANAGE_TOKEN` → the same value as in Vercel
→ **Deploy**. Repeat for `SCHEDULER_TOKEN`.

Each must be a **Secret**. A plain Text variable that isn't in `wrangler.toml` is
**deleted on the next deploy from GitHub**; secrets are kept. Don't add
`APP_URL` here: it comes from `wrangler.toml`.

### 6. Deploy
Push to `main` (or open **Deployments** and retry the latest build). The build
should succeed, and **Settings → Trigger events** should show a Cron trigger
**Every minute** (`* * * * *`).

## Check it's working

**The Worker runs.** **Metrics** tab → about one **invocation** per minute,
**0 errors**, and **subrequests** (its calls to your app) climbing too.
"0 req/sec" on the Deployments page is normal: scheduled runs aren't web
requests.

**Your app accepts the tokens.** Test from your Mac:
```
curl -s -X POST -H "x-hl-manage-token: <HL_MANAGE_TOKEN>" \
  https://cryptomarket-sigma.vercel.app/api/hl/manage | python3 -m json.tool

curl -s -X POST -H "x-scheduler-token: <SCHEDULER_TOKEN>" \
  https://cryptomarket-sigma.vercel.app/api/cron/publish | python3 -m json.tool
```
The first should show `"ok": true, "ran": true`. The second should show `"ok": true`,
usually with `"skipped_reason": "SLOT_ALREADY_PUBLISHED"`. That's harmless;
it publishes only if the current slot hasn't been published yet.

The Worker logs one line per job (with a `"job"` field) under **Observability**
(enable Workers Logs there if prompted).

## Reading the result

| You see | Meaning |
|---|---|
| `"job":"manage","ran":true,"actions":0` | Working; no position needs its stop moved. |
| `"action":"move_stop","placed":true` | TP1 had filled; the stop was moved to entry. |
| `"action":"close_remainder","closed":true` | Price was already back through entry; the remainder was closed. |
| `"ran":false,"reason":"DISARMED"` | Live trading is off in Vercel (`LIVE_TRADING_ENABLED` / kill switch). Expected if you disarmed it. |
| `"status":401` / `AUTH_REQUIRED` | The token differs between Cloudflare and Vercel, or Vercel wasn't redeployed after changing it. |
| `"job":"publish","persisted":N` | The slot was published: N new signals (HL auto-exec ran on them). |
| `"job":"publish","skipped_reason":"SLOT_ALREADY_PUBLISHED"` | Already published this slot; nothing to do. |
| `"job":"publish","status":504,"attempts":3` | Every attempt hit Vercel's 60 s limit; the next :12/:32 run retries. |
| `APP_URL or … is not configured` | That token's Secret is missing (see step 5). Only its jobs are skipped. |
| Build fails in 0 seconds | Wrong root directory, or the Worker name ≠ `name` in `wrangler.toml`. |

The orange "Update your Wrangler configuration…" banners in the dashboard are
suggestions for keeping the dashboard and `wrangler.toml` in sync. You can
dismiss them; don't add secrets to `wrangler.toml`.

## Rotating a token
1. `openssl rand -hex 32`.
2. Vercel: update the token (`HL_MANAGE_TOKEN` or `SCHEDULER_TOKEN`) → **Redeploy**.
3. Cloudflare: Settings → Variables and secrets → edit that Secret → the new
   value → **Deploy**.

Between steps 2 and 3 the Worker gets `401` for a minute or two. That's
harmless: the GitHub backups keep running.

## Turning it off
Delete the Cron trigger (Settings → Trigger events), or delete the Worker.
Removing a token from Vercel shuts out that token's jobs. The GitHub triggers
keep running either way.

## Deploying with the CLI instead (optional)
```
cd cloudflare/hl-manage-worker
npx wrangler login
npx wrangler deploy
npx wrangler secret put HL_MANAGE_TOKEN
npx wrangler secret put SCHEDULER_TOKEN
```
