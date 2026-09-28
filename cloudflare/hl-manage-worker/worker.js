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
//   every 4h at :15      market update      POST /api/patterns/alert  (→ Telegram digest)
//   00:20                TAO snapshot       POST /api/cron/tao-snapshot
//   01:30                data snapshots     POST /api/cron/etf-snapshot, /api/cron/market-snapshot
//   every 2h at :10      ML collect         POST /api/research/ml/collect  (00:10, 02:10, …)
//   every 2h at 01:15…   ML label           POST /api/research/ml/label    (01:15, 03:15, …)
//   Sunday 06:17         weekly report      POST /api/cron/weekly-report  (→ PRIVATE Telegram chat)
//
// Publish runs three times an hour. The 4H slot publishes at :02 right after
// the close, and the :12 / :32 runs retry when exchange data lagged or a cold
// start timed out. Once a slot is published, extra runs return
// SLOT_ALREADY_PUBLISHED in about a second. Every job is safe to repeat (keyed
// per slot / candle / alert), so a manual GitHub run alongside never
// double-sends or double-trades. This Worker is the only scheduler.
//
// Problem alerts: when a job fails after its retries (app down or unreachable,
// a 401 token mismatch, a 5xx, a timeout), the Worker messages the owner's
// PRIVATE Telegram chat directly. It doesn't go through the app, because a
// broken app can't report on itself. Throttled so a job that keeps failing
// doesn't message every minute. Needs the optional secrets below; without them
// failures are only logged.
//
// Configuration (Worker → Settings → Variables and secrets):
//   APP_URL          from wrangler.toml
//   HL_MANAGE_TOKEN  SECRET, opens /api/hl/manage only
//   SCHEDULER_TOKEN  SECRET, opens only the scheduled paths above (POST)
//   TELEGRAM_BOT_TOKEN      SECRET (optional), same bot as the app
//   TELEGRAM_ALERT_CHAT_ID  SECRET (optional), your PRIVATE chat id, never a channel
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
  // Every 4H close (4H/1D/1W reads, one coin-grouped digest per run).
  { name: "patterns", path: "/api/patterns/alert", ...S, retries: 1,
    due: ({ h, m }) => h % 4 === 0 && m === 15 },
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
    due: ({ h, m }) => h % 2 === 0 && m === 10 },
  { name: "ml-label", path: "/api/research/ml/label", ...S, retries: 2,
    retryOn: [0, 429, 502, 504], retryDelayMs: 10_000, body: { ...ML_BODY, limit: 10 },
    due: ({ h, m }) => h % 2 === 1 && m === 15 },
  // Sent at most once per ISO week by the app, so a retry never double-sends.
  { name: "weekly-report", path: "/api/cron/weekly-report", ...S, retries: 2,
    due: ({ d, h, m }) => d === 0 && h === 6 && m === 17 },
];

// Fields worth logging from any job's response. Never the URL, a token or the
// raw body.
const SUMMARY_KEYS = ["ok", "ran", "reason", "positions", "actions", "computed",
  "persisted", "duplicates", "skipped_reason", "error_code", "slot_current",
  "mode", "attempted", "counts", "week", "result"];

const RETRY_DELAY_MS = 20_000;       // a timed-out publish leaves the cache warmer

/** Jobs due at this instant (UTC). Exported for tests. */
export function dueJobs(date) {
  const t = { d: date.getUTCDay(), h: date.getUTCHours(), m: date.getUTCMinutes() };
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
    // Publish reports what HL auto-exec did (it runs in the publishing request
    // and, if that one was cut off, on the next calls early in the slot).
    const hl = r.body.hl_auto_execute;
    if (hl && typeof hl === "object") {
      out.hl = {};
      for (const k of ["ran", "attempted", "executed", "reason", "error_code"]) {
        if (k in hl) out.hl[k] = hl[k];
      }
    }
    if (Array.isArray(r.body.results)) {
      out.results = r.body.results.map(x => ({ coin: x.coin, action: x.action,
        placed: x.placed, closed: x.closed, error: x.error }));
    }
  }
  return out;
}

// Expected non-200s that are not problems.
const BENIGN_CODES = new Set(["FEATURE_DISABLED"]);   // ML research switched off

// How often a still-failing job may alert, keyed off the UTC minute, so a job
// that runs every minute doesn't message every minute. Other jobs run a few
// times a day and alert on every failure.
const ALERT_WHEN = {
  manage: ({ m }) => m % 30 === 0,        // every-minute job: at most twice an hour
  publish: ({ m }) => m === 32,           // the hour's last try: :02/:12 may still recover
  monitor: ({ m }) => m === 35,           // at most hourly
};

const HINTS = {
  0: "app unreachable or timed out",
  401: "token mismatch: the Cloudflare secret differs from Vercel, or Vercel wasn't redeployed",
  403: "forbidden: check the token and the endpoint",
  500: "server error in the app",
  502: "bad gateway: the app crashed or Vercel had an error",
  503: "app answered 503",
  504: "Vercel 60 s timeout",
};

function failed(r) {
  if (r.error && r.status === undefined) return true;                  // not configured
  if (r.status === 200) return false;
  if (r.error_code && BENIGN_CODES.has(r.error_code)) return false;
  return true;
}

/** The alert text for a job result at time `when`, or null. Exported for tests. */
export function alertFor(r, when) {
  if (!failed(r)) return null;
  const t = { h: when.getUTCHours(), m: when.getUTCMinutes() };
  const allow = ALERT_WHEN[r.job];
  if (allow && !allow(t)) return null;
  const at = `${String(t.h).padStart(2, "0")}:${String(t.m).padStart(2, "0")} UTC`;
  const why = r.error && r.status === undefined ? r.error
    : `HTTP ${r.status}${r.error_code ? ` ${r.error_code}` : ""}: ${HINTS[r.status] || "unexpected status"}`;
  const tries = r.attempts > 1 ? ` after ${r.attempts} attempts` : "";
  return `🚨 Scheduler: ${r.job} failed at ${at}${tries}\n${why}`;
}

async function sendAlert(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_ALERT_CHAT_ID) return false;
  try {
    const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_ALERT_CHAT_ID, text,
                             disable_web_page_preview: true }),
    });
    return res.ok;
  } catch (_) {
    return false;
  }
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
    ctx.waitUntil(runDue(when, env).then(async results => {
      for (const r of results) {
        console.log(JSON.stringify(r));
        const text = alertFor(r, when);
        if (text) r.alerted = await sendAlert(env, text);
      }
    }));
  },

  // No public HTTP surface: the Worker is only ever run by its cron trigger.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
