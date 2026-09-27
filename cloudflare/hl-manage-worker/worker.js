// CryptoAIBadshah scheduler (Cloudflare Worker).
//
// One every-minute cron trigger; the minute's UTC time decides which jobs run.
// Cloudflare fires on time, where GitHub's schedules ran 1-3 hours late or not
// at all. The app does all the work; this Worker is only the clock.
//
//   every minute         position manager   POST /api/hl/manage   (stop → entry after TP1)
//   hh:02, hh:12, hh:32  publish signals    POST /api/cron/publish  (+ HL auto-exec)
//   hh:05, hh:35         outcome monitor    POST /api/signals/monitor
//   00:07, 08:07, 12:07  Telegram daily     POST /api/cron/daily
//   00:15, 08:15, 16:15  pattern alerts     POST /api/patterns/alert  (→ Telegram)
//   00:20                TAO snapshot       POST /api/cron/tao-snapshot
//   01:30                data snapshots     POST /api/cron/etf-snapshot, /api/cron/market-snapshot
//   every 4h at :10      ML collect         POST /api/research/ml/collect  (00:10, 04:10, …)
//   every 4h at 01:15…   ML label           POST /api/research/ml/label    (01:15, 05:15, …)
//
// Publish runs three times an hour. The 4H slot publishes at :02 right after
// the close, and the :12 / :32 runs retry when exchange data lagged or a cold
// start timed out. Once a slot is published, extra runs return
// SLOT_ALREADY_PUBLISHED in about a second. Every job is safe to repeat (keyed
// per slot / candle / alert), so a manual GitHub run alongside never
// double-sends or double-trades. This Worker is the only scheduler.
//
// Configuration (Worker → Settings → Variables and secrets):
//   APP_URL          from wrangler.toml
//   HL_MANAGE_TOKEN  SECRET, opens /api/hl/manage only
//   SCHEDULER_TOKEN  SECRET, opens only the scheduled paths above (POST)
// A job whose token is missing is skipped and logged; the others still run.

// Retry on timeouts / platform errors by default. 4xx (auth, bad request) and
// 503 ("not configured" / feature off) are configuration problems that won't fix
// themselves in seconds.
const DEFAULT_RETRY_ON = [0, 500, 502, 504];
const ML_BODY = { symbols: ["BTC", "ETH"], source: "okx", write: true };

const S = { secret: "SCHEDULER_TOKEN", header: "x-scheduler-token", timeoutMs: 75_000 };

const JOBS = [
  { name: "manage", path: "/api/hl/manage", secret: "HL_MANAGE_TOKEN",
    header: "x-hl-manage-token", timeoutMs: 50_000, retries: 0,
    due: () => true },
  { name: "publish", path: "/api/cron/publish", ...S, retries: 2,
    due: ({ m }) => m === 2 || m === 12 || m === 32 },
  { name: "monitor", path: "/api/signals/monitor", ...S, retries: 0,
    due: ({ m }) => m === 5 || m === 35 },
  { name: "daily", path: "/api/cron/daily", ...S, retries: 2,
    due: ({ h, m }) => (h === 0 || h === 8 || h === 12) && m === 7 },
  { name: "patterns", path: "/api/patterns/alert", ...S, retries: 1,
    due: ({ h, m }) => (h === 0 || h === 8 || h === 16) && m === 15 },
  { name: "tao-snapshot", path: "/api/cron/tao-snapshot", ...S, retries: 1,
    due: ({ h, m }) => h === 0 && m === 20 },
  { name: "etf-snapshot", path: "/api/cron/etf-snapshot", ...S, retries: 2,
    due: ({ h, m }) => h === 1 && m === 30 },
  { name: "market-snapshot", path: "/api/cron/market-snapshot", ...S, retries: 2,
    due: ({ h, m }) => h === 1 && m === 30 },
  // ML research: a 503 may already have recorded a failure in the label retry
  // queue or an invalid feature row, so it is NOT retried (same rule as the old
  // scripts/ml_research_schedule.py). A disabled feature (ML_RESEARCH_ENABLED
  // off) answers 503 and is simply logged.
  { name: "ml-collect", path: "/api/research/ml/collect", ...S, retries: 2,
    retryOn: [0, 429, 502, 504], retryDelayMs: 10_000, body: ML_BODY,
    due: ({ h, m }) => h % 4 === 0 && m === 10 },
  { name: "ml-label", path: "/api/research/ml/label", ...S, retries: 2,
    retryOn: [0, 429, 502, 504], retryDelayMs: 10_000, body: { ...ML_BODY, limit: 10 },
    due: ({ h, m }) => h % 4 === 1 && m === 15 },
];

// Fields worth logging from any job's response. Never the URL, a token or the
// raw body.
const SUMMARY_KEYS = ["ok", "ran", "reason", "positions", "actions", "computed",
  "persisted", "duplicates", "skipped_reason", "error_code", "slot_current",
  "mode", "attempted", "counts"];

const RETRY_DELAY_MS = 20_000;       // a timed-out publish leaves the cache warmer

/** Jobs due at this instant (UTC). Exported for tests. */
export function dueJobs(date) {
  const t = { h: date.getUTCHours(), m: date.getUTCMinutes() };
  return JOBS.filter(j => j.due(t)).map(j => j.name);
}

function retryable(job, status) {
  return (job.retryOn || DEFAULT_RETRY_ON).includes(status);
}

async function callOnce(url, job, token) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), job.timeoutMs);
  try {
    const headers = { [job.header]: token };
    const init = { method: "POST", headers, signal: ctrl.signal };
    if (job.body) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(job.body);
    }
    const res = await fetch(url, init);
    let body = null;
    try { body = await res.json(); } catch (_) { /* e.g. a platform error page */ }
    return { status: res.status, body };
  } catch (err) {
    return { status: 0, error: err.name === "AbortError" ? "timeout" : "fetch failed" };
  } finally {
    clearTimeout(timer);
  }
}

async function runJob(job, env, sleep) {
  const token = env[job.secret];
  if (!env.APP_URL || !token) {
    return { job: job.name, ok: false, error: `APP_URL or ${job.secret} is not configured` };
  }
  const url = env.APP_URL.replace(/\/+$/, "") + job.path;
  let r;
  for (let attempt = 1; attempt <= 1 + job.retries; attempt++) {
    r = await callOnce(url, job, token);
    r.attempt = attempt;
    if (!retryable(job, r.status) || attempt > job.retries) break;
    await sleep((job.retryDelayMs || RETRY_DELAY_MS) * (job.retryDelayMs ? attempt : 1));
  }
  const out = { job: job.name, status: r.status, attempts: r.attempt };
  if (r.error) out.error = r.error;
  if (r.body && typeof r.body === "object") {
    for (const k of SUMMARY_KEYS) if (k in r.body) out[k] = r.body[k];
    if (Array.isArray(r.body.results)) {
      out.results = r.body.results.map(x => ({ coin: x.coin, action: x.action,
        placed: x.placed, closed: x.closed, error: x.error }));
    }
  }
  return out;
}

/** Run everything due at `when`; returns one summary per job. Exported for tests. */
export async function runDue(when, env, sleep = ms => new Promise(r => setTimeout(r, ms))) {
  const names = new Set(dueJobs(when));
  const jobs = JOBS.filter(j => names.has(j.name));
  return Promise.all(jobs.map(j => runJob(j, env, sleep)));
}

export default {
  // Cron Trigger "* * * * *" (wrangler.toml). scheduledTime is the minute the
  // trigger was meant for, so a slightly late start still runs the right jobs.
  async scheduled(event, env, ctx) {
    const when = new Date(event.scheduledTime || Date.now());
    ctx.waitUntil(runDue(when, env).then(results => {
      for (const r of results) console.log(JSON.stringify(r));
    }));
  },

  // No public HTTP surface: the Worker is only ever run by its cron trigger.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
