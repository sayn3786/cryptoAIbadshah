// Run with Node + Playwright available. All requests are intercepted synthetic fixtures.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const shell = fs.readFileSync(path.join(root, 'dashboard/index.html'), 'utf8');
const panel = shell.slice(shell.indexOf('<dialog id="ml-research-dialog"'), shell.indexOf('<!-- ── USER MANAGEMENT'));
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const page = await browser.newPage();
    let mode = 'populated', calls = 0;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:
        '<!doctype html><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
        '<body style="background:#101827"><p style="color:white">Synthetic test only</p><button id="cm-ml-status">ML research</button>'+panel});
      if (url.pathname === '/api/research/ml/status') {
        calls++;
        assert.equal(route.request().method(),'GET');
        if (mode === 'error') return route.fulfill({status:503,json:{ok:false,error_code:'RESEARCH_SCHEMA_MISSING'}});
        return route.fulfill({json:{ok:true,environment:'research (synthetic)',window_start:'2026-11-07T00:00:00Z',checked_at:'2026-11-14T00:00:00Z',
          collection:mode === 'empty' ? [] : [{source:'okx',feature_version:'candles_1h_v2',quality:'ready',snapshots:24,latest_snapshot:'2026-11-14T00:00:00Z'}],
          labels:[],jobs:[]}});
      }
      if (['/dashboard/js/ml-research-status.js','/dashboard/css/ml-research-status.css'].includes(url.pathname)) {
        return route.fulfill({body:fs.readFileSync(path.join(root,url.pathname)),contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css'});
      }
      return route.abort();
    });
    await page.goto('http://127.0.0.1:4173/');
    assert.equal(calls,0);
    await page.getByRole('button',{name:'ML research',exact:true}).click();
    await page.getByRole('cell',{name:'24',exact:true}).waitFor();
    assert.equal(calls,1);
    assert.match(await page.locator('dialog').innerText(), /Trading readiness: not assessed/);
    await page.screenshot({path:'/private/tmp/ml-research-status-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'/private/tmp/ml-research-status-mobile.png'});
    assert(await page.locator('dialog').evaluate(el=>el.getBoundingClientRect().right<=innerWidth));
    mode='empty';
    await page.getByRole('button',{name:'Refresh status'}).click();
    await page.getByText('No records in this snapshot window.').first().waitFor();
    assert.match(await page.locator('#ml-research-content').innerText(),/No records/);
    mode='error';
    await page.getByRole('button',{name:'Refresh status'}).click();
    await page.getByText('Research tables are missing. Review migrations before deployment.').waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    assert.equal(await page.locator('dialog').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: on-demand GET, populated/empty/error, refresh, close, mobile bounds; no JS errors');
  } finally { await browser.close(); }
})();
