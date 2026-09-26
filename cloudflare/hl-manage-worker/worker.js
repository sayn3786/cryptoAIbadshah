// Hyperliquid position manager — every-minute trigger (Cloudflare Worker).
//
// Calls POST {APP_URL}/api/hl/manage once a minute so a position's stop moves
// to ENTRY within about a minute of TP1 filling. The endpoint does all the
// work and is idempotent; this Worker only knocks on the door.
//
// Configuration (Cloudflare dashboard → Worker → Settings → Variables):
//   APP_URL          plain variable, e.g. https://cryptomarket-sigma.vercel.app
//   HL_MANAGE_TOKEN  SECRET — must equal HL_MANAGE_TOKEN in Vercel. It can only
//                    call /api/hl/manage; it cannot publish or place orders.
//
// The GitHub Actions triggers (every 5 min + every 30 min via the Signal
// Outcome Monitor) stay in place as backups.

const TIMEOUT_MS = 50_000;

async function runManager(env) {
  if (!env.APP_URL || !env.HL_MANAGE_TOKEN) {
    return { ok: false, error: "APP_URL or HL_MANAGE_TOKEN is not configured" };
  }
  const url = env.APP_URL.replace(/\/+$/, "") + "/api/hl/manage";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "x-hl-manage-token": env.HL_MANAGE_TOKEN },
      signal: ctrl.signal,
    });
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON (e.g. a platform error page) */ }
    // Log only an allow-listed summary — never the URL, token or raw body.
    const summary = { status: res.status };
    if (body) {
      for (const k of ["ok", "ran", "reason", "positions", "actions", "error_code"]) {
        if (k in body) summary[k] = body[k];
      }
      summary.results = (body.results || []).map(r => ({
        coin: r.coin, action: r.action, placed: r.placed, closed: r.closed, error: r.error,
      }));
    }
    return summary;
  } catch (err) {
    return { ok: false, error: err.name === "AbortError" ? "timeout" : "fetch failed" };
  } finally {
    clearTimeout(timer);
  }
}

export default {
  // Cron Trigger: "* * * * *" (see wrangler.toml / the dashboard's Triggers tab).
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runManager(env).then(s => console.log(JSON.stringify(s))));
  },

  // No public HTTP surface: the Worker is only ever run by its cron trigger.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
