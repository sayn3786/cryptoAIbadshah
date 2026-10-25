// Isolated from the trading scheduler. No public HTTP handler, no retries.
export async function tick(env, when = Date.now(), fetcher = fetch) {
  if (env.EXECUTION_PILOT_ENABLED !== 'true') return {ok: true, skipped: 'disabled'};
  const start = Number(env.EXECUTION_PILOT_START_UTC_SECONDS);
  if (!Number.isSafeInteger(start) || start <= 0 || start % 900 !== 0)
    return {ok: false, error_code: 'INVALID_START'};
  const slot = Math.floor((when / 1000 - start) / 900);
  if (slot < 1 || slot > 96)
    return {ok: true, skipped: 'outside_window'};
  if (when / 1000 - start - slot * 900 > 120)
    return {ok: false, slot, error_code: 'MISSED_COLLECTION_WINDOW'};
  if (!env.EXECUTION_PILOT_TOKEN) return {ok: false, error_code: 'TOKEN_MISSING'};
  try {
    const res = await fetcher('https://cryptomarket-sigma.vercel.app/api/research/execution-pilot/tick', {
      method: 'POST', redirect: 'error',
      headers: {'x-execution-pilot-token': env.EXECUTION_PILOT_TOKEN},
      signal: AbortSignal.timeout(55000),
    });
    const body = await res.json();
    return {ok: res.ok && body.ok === true, status: res.status, slot,
      duplicate: body.duplicate === true, complete: body.complete === true};
  } catch (_) { return {ok: false, slot, error_code: 'REQUEST_FAILED_OR_COMMIT_UNKNOWN'}; }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      const result = await tick(env);
      console.log(JSON.stringify(result));
      // Fail the invocation for Cloudflare monitoring; never expose payloads.
      if (!result.ok) throw new Error('EXECUTION_PILOT_NEEDS_REVIEW');
    })());
  },
  async fetch() { return new Response('Not found', {status: 404}); },
};
