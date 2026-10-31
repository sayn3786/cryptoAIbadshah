// Scheduler only: Telegram delivery uses the existing Vercel private scalp sender.
export async function notifyFailure(env, result, fetcher = fetch, testOnly = false) {
  if (!env.EXECUTION_PILOT_TOKEN) return {ok:false,error_code:'TOKEN_MISSING'};
  const allowed = new Set(['INVALID_START','MISSED_COLLECTION_WINDOW','TOKEN_MISSING',
    'REQUEST_FAILED_OR_COMMIT_UNKNOWN','REDIRECT_REJECTED']);
  const slot = Number.isInteger(result.slot) && result.slot >= 1 && result.slot <= 96 ? result.slot : null;
  try {
    const response = await fetcher('https://cryptomarket-sigma.vercel.app/api/research/execution-pilot/alert', {
      method:'POST', redirect:'manual', signal:AbortSignal.timeout(25000),
      headers:{'Content-Type':'application/json','x-execution-pilot-token':env.EXECUTION_PILOT_TOKEN},
      body:JSON.stringify({test_only:testOnly,slot,
        category:allowed.has(result.error_code)?result.error_code:'COLLECTION_FAILED'}),
    });
    if (!response.ok) return {ok:false,error_code:'ALERT_RELAY_REJECTED',status:response.status};
    return {ok:(await response.json())?.ok === true};
  } catch (_) { return {ok:false,error_code:'ALERT_FAILED_OR_DELIVERY_UNKNOWN'}; }
}

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
      method: 'POST', redirect: 'manual',
      headers: {'x-execution-pilot-token': env.EXECUTION_PILOT_TOKEN},
      signal: AbortSignal.timeout(55000),
    });
    if (res.status >= 300 && res.status < 400)
      return {ok: false, slot, error_code: 'REDIRECT_REJECTED'};
    const body = await res.json();
    return {ok: res.ok && body.ok === true, status: res.status, slot,
      duplicate: body.duplicate === true, complete: body.complete === true};
  } catch (_) { return {ok: false, slot, error_code: 'REQUEST_FAILED_OR_COMMIT_UNKNOWN'}; }
}

export async function runScheduled(env, when = Date.now(), fetcher = fetch, logger = console.log) {
  const result = await tick(env, when, fetcher);
  logger(JSON.stringify(result));
  if (!result.ok) {
    logger(JSON.stringify({alert: await notifyFailure(env, result, fetcher)}));
    throw new Error('EXECUTION_PILOT_NEEDS_REVIEW');
  }
}

export default {
  async scheduled(event, env, ctx) {
    const testAt = Number(env.EXECUTION_PILOT_ALERT_TEST_UTC_SECONDS);
    // Explicit operator-armed, single scheduled timestamp. Never enables collection.
    if (env.EXECUTION_PILOT_ENABLED === 'false' && Number.isSafeInteger(testAt) &&
        testAt > 0 && testAt % 60 === 0 &&
        Math.floor(event.scheduledTime / 60000) === testAt / 60 &&
        Date.now() >= testAt * 1000 && Date.now() <= (testAt + 120) * 1000) {
      ctx.waitUntil((async () => {
        const alert = await notifyFailure(env, {}, fetch, true);
        console.log(JSON.stringify({test_only: true, alert}));
        if (!alert.ok) throw new Error('EXECUTION_PILOT_ALERT_TEST_FAILED');
      })());
      return;
    }
    ctx.waitUntil(runScheduled(env));
  },
  async fetch() { return new Response('Not found', {status: 404}); },
};
