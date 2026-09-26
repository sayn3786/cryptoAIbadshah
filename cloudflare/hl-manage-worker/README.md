# Every-minute Hyperliquid position manager (Cloudflare Worker)

Moves a position's stop to **entry** within about a minute of TP1 filling, by
calling `POST /api/hl/manage` once a minute. GitHub's schedules are
best-effort, so they only run every 5–30 minutes; Cloudflare's cron triggers
run every minute reliably. The GitHub triggers (`hl-manage.yml` every 5 min,
plus the Signal Outcome Monitor every 30 min) stay as backups.

The Worker uses a **narrow token**, `HL_MANAGE_TOKEN`. It can only call
`/api/hl/manage`, which can only tighten stops or close a remainder at
break-even. It cannot publish, place new orders or read the account. So your
`CRON_SECRET` and HL admin token never leave Vercel and GitHub.

Cost: free. The Workers free plan includes cron triggers, and 1,440 runs a day
is far below its limits.

Cloudflare deploys the Worker **from this repo**: every push to `main`
rebuilds it from this folder. `worker.js` is the code; `wrangler.toml` sets the
Worker name, `APP_URL` and the every-minute cron.

## Setup (about 10 minutes)

### 1. Create the token
On your Mac:
```
openssl rand -hex 32
```
Keep it handy. You'll paste the same value in two places. Never paste it into
chat, tickets or screenshots.

### 2. Add it to Vercel
Vercel → project → **Settings → Environment Variables** → Add
`HL_MANAGE_TOKEN` = the token, Environment **Production** → Save → then
**Deployments → latest → ⋯ → Redeploy**. The token must be at least 16
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

### 5. Add the token as a Secret
Worker → **Settings → Variables and secrets** → **+ Add variable** → set the Type
to **Secret** (not Text) → Name `HL_MANAGE_TOKEN` → the same value as in Vercel
→ **Deploy**.

It must be a **Secret**. A plain Text variable that isn't in `wrangler.toml` is
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

**Your app accepts the token.** Test the same value from your Mac:
```
curl -s -X POST -H "x-hl-manage-token: <YOUR_TOKEN>" \
  https://cryptomarket-sigma.vercel.app/api/hl/manage | python3 -m json.tool
```
`"ok": true, "ran": true` means it works. The Worker logs the same summary on
every run under **Observability** (enable Workers Logs there if prompted).

## Reading the result

| You see | Meaning |
|---|---|
| `"ran":true,"actions":0` | Working; no position needs its stop moved. |
| `"action":"move_stop","placed":true` | TP1 had filled; the stop was moved to entry. |
| `"action":"close_remainder","closed":true` | Price was already back through entry; the remainder was closed. |
| `"ran":false,"reason":"DISARMED"` | Live trading is off in Vercel (`LIVE_TRADING_ENABLED` / kill switch). Expected if you disarmed it. |
| `"status":401` / `AUTH_REQUIRED` | The token differs between Cloudflare and Vercel, or Vercel wasn't redeployed after changing it. |
| `APP_URL or HL_MANAGE_TOKEN is not configured` | The Secret is missing (see step 5). |
| Build fails in 0 seconds | Wrong root directory, or the Worker name ≠ `name` in `wrangler.toml`. |

The orange "Update your Wrangler configuration…" banners in the dashboard are
suggestions for keeping the dashboard and `wrangler.toml` in sync. You can
dismiss them; don't add secrets to `wrangler.toml`.

## Rotating the token
1. `openssl rand -hex 32`.
2. Vercel: update `HL_MANAGE_TOKEN` → **Redeploy**.
3. Cloudflare: Settings → Variables and secrets → edit the `HL_MANAGE_TOKEN`
   Secret → the new value → **Deploy**.

Between steps 2 and 3 the Worker gets `401` for a minute or two. That's
harmless: the GitHub backups keep running.

## Turning it off
Delete the Cron trigger (Settings → Trigger events), or delete the Worker.
Removing `HL_MANAGE_TOKEN` from Vercel also shuts it out. The GitHub triggers
keep running either way.

## Deploying with the CLI instead (optional)
```
cd cloudflare/hl-manage-worker
npx wrangler login
npx wrangler deploy
npx wrangler secret put HL_MANAGE_TOKEN
```
