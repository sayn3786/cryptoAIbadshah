# Every-minute Hyperliquid position manager (Cloudflare Worker)

Moves a position's stop to **entry** within about a minute of TP1 filling, by
calling `POST /api/hl/manage` once a minute. GitHub's schedules are
best-effort, so it only runs every 5–30 minutes; Cloudflare's cron triggers
run every minute reliably. The GitHub triggers stay as backups.

The Worker uses a **narrow token**, `HL_MANAGE_TOKEN`. It can only call
`/api/hl/manage`, which can only tighten stops or close a remainder at
break-even. It cannot publish, place new orders or read the account. So your
`CRON_SECRET` and HL admin token never leave Vercel and GitHub.

Cost: free. The Workers free plan includes cron triggers, and 1,440 runs a day
is far below its limits.

## Setup (dashboard only, no CLI; about 10 minutes)

### 1. Create the token
Generate a long random value on your Mac:
```
openssl rand -hex 32
```
Keep it handy. You'll paste the same value in two places.

### 2. Add it to Vercel
Vercel → project → **Settings → Environment Variables** → Add:
- Key `HL_MANAGE_TOKEN`, Value = the token, Environment **Production**.

Then **redeploy** (Deployments → latest → ⋯ → Redeploy). The token must be at
least 16 characters, or it is ignored.

### 3. Create the Worker
1. <https://dash.cloudflare.com> → **Workers & Pages** → **Create** →
   **Create Worker**. Name it `cryptoaibadshah` (it must match `name` in `wrangler.toml`) → **Deploy**. This creates a
   placeholder.
2. **Edit code** → delete everything → paste the contents of
   [`worker.js`](./worker.js) → **Deploy**.

### 4. Configure it
In the Worker → **Settings → Variables and Secrets**:
- **Add** → Type **Text** → `APP_URL` = `https://cryptomarket-sigma.vercel.app`
- **Add** → Type **Secret** → `HL_MANAGE_TOKEN` = the same token as in Vercel
- **Deploy**.

In **Settings → Domains & Routes**, you can disable the `workers.dev` route.
The Worker has no public purpose, and it returns 404 anyway.

### 5. Schedule it
Worker → **Settings → Triggers → Cron Triggers → Add** → Cron expression
`* * * * *` (every minute) → **Add**.

### 6. Check it's working
Worker → **Logs** (or **Observability**). Within a minute or two you should see
a line like:
```
{"status":200,"ok":true,"ran":true,"positions":1,"actions":0,"results":[]}
```
- `status 401`: the token doesn't match between Vercel and the Worker, or
  Vercel wasn't redeployed after adding it.
- `"ran":false,"reason":"DISARMED"`: live trading is switched off
  (`LIVE_TRADING_ENABLED` / kill switch). That is expected if you've disarmed it.
- `"actions":1` with `"action":"move_stop","placed":true`: TP1 had filled, and
  the stop was moved to entry.

## Turning it off
Remove the cron trigger, or delete the Worker. The GitHub triggers keep running.

## Deploying with the CLI instead (optional)
```
cd cloudflare/hl-manage-worker
npx wrangler deploy
npx wrangler secret put HL_MANAGE_TOKEN
```
`wrangler.toml` already sets `APP_URL`, the every-minute cron, and no public
`workers.dev` URL.
