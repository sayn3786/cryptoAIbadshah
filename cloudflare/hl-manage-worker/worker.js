// CryptoAIBadshah scheduler (Cloudflare Worker).
//
// One every-minute cron trigger; the minute's UTC time decides which jobs run.
// Cloudflare fires on time, where GitHub's schedules ran 1-3 hours late or not
// at all. The app does all the work; this Worker is only the clock.
//
//   every minute         position manager   POST /api/hl/manage   (stop → entry after TP1)
//   hh:02, hh:12, hh:32  publish signals    POST /api/cron/publish  (+ HL auto-exec)
//   hh:05, hh:35         outcome monitor    POST /api/signals/monitor
//   00:07, 08:07         Telegram daily     POST /api/cron/daily
//   00:15, 08:15, 16:15  pattern alerts     POST /api/patterns/alert  (→ Telegram)
//
// Publish runs three times an hour. The 4H slot publishes at :02 right after
// the close, and the :12 / :32 runs retry when exchange data lagged or a cold
// start timed out. Once a slot is published, extra runs return
// SLOT_ALREADY_PUBLISHED in about a second. Every job is safe to repeat (keyed
// per slot / candle / alert), so overlapping with the GitHub backups never
// double-sends or double-trades.
//
// Configuration (Worker → Settings → Variables and secrets):
//   APP_URL          from wrangler.toml
//   HL_MANAGE_TOKEN  SECRET, opens /api/hl/manage only
//   SCHEDULER_TOKEN  SECRET, opens only the four scheduled paths above (POST)
// A job whose token is missing is skipped and logged; the others still run.

const JOBS = [
  { name: "manage", path: "/api/hl/manage", secret: "HL_MANAGE_TOKEN",
    header: "x-hl-manage-token", timeoutMs: 50_000, retries: 0,
    due: () => true },
  { name: "publish", path: "/api/cron/publish", secret: "SCHEDULER_TOKEN",
    header: "x-scheduler-token", timeoutMs: 75_000, retries: 2,
    due: ({ m }) => m === 2 || m === 12 || m === 32 },
  { name: "monitor", path: "/api/signals/monitor", secret: "SCHEDULER_TOKEN",
    header: "x-scheduler-token", timeoutMs: 75_000, retries: 0,
    due: ({ m }) => m === 5 || m === 35 },
  { name: "daily", path: "/api/cron/daily", secret: "SCHEDULER_TOKEN",
    header: "x-scheduler-token", timeoutMs: 75_000, retries: 2,
    due: ({ h, m }) => (h === 0 || h === 8) && m === 7 },
  { name: "patterns", path: "/api/patterns/alert", secret: "SCHEDULER_TOKEN",
    header: "x-scheduler-token", timeoutMs: 75_000, retries: 1,
    due: ({ h, m }) => (h === 0 || h === 8 || h === 16) && m === 15 },
];

// Fields worth logging from any job's response. Never the URL, a token or the
// raw body.
const SUMMARY_KEYS = ["ok", "ran", "reason", "positions", "actions", "computed",
  "persisted", "duplicates", "skipped_reason", "error_code", "slot_current"];

const RETRY_DELAY_MS = 20_000;       // a timed-out publish leaves the cache warmer

/** Jobs due at this instant (UTC). Exported for tests. */
export function dueJobs(date) {
  const t = { h: date.getUTCHours(), m: date.getUTCMinutes() };
  return JOBS.filter(j => j.due(t)).map(j => j.name);
}

function retryable(status) {
  // Timeouts / platform errors are worth another go. 4xx (auth, bad request)
  // and a 503 "not configured" are configuration problems and won't fix
  // themselves in 20 seconds.
  return status === 0 || status === 502 || status === 504 || status === 500;
}

async function callOnce(url, job, token) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), job.timeoutMs);
  try {
    const res = await fetch(url, { method: "POST", headers: { [job.header]: token },
                                   signal: ctrl.signal });
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
    if (!retryable(r.status) || attempt > job.retries) break;
    await sleep(RETRY_DELAY_MS);
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
