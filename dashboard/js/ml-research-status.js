/* Operator-only, on-demand metadata reader. No polling or mutation endpoints. */
(() => {
  const dialog = document.getElementById('ml-research-dialog');
  const content = document.getElementById('ml-research-content');
  const refresh = document.getElementById('ml-research-refresh');
  function node(tag, text) {
    const el = document.createElement(tag);
    el.textContent = text;
    return el;
  }
  function table(title, rows, fields) {
    content.appendChild(node('h3', title));
    if (!rows.length) {
      content.appendChild(node('p', 'No records in this snapshot window.'));
      return;
    }
    const wrap = node('div', '');
    wrap.className = 'ml-research-table-wrap';
    const t = document.createElement('table');
    const head = document.createElement('thead');
    const tr = document.createElement('tr');
    fields.forEach(([label]) => { const th = node('th', label); th.scope = 'col'; tr.appendChild(th); });
    head.appendChild(tr); t.appendChild(head);
    const body = document.createElement('tbody');
    rows.forEach(row => {
      const r = document.createElement('tr');
      fields.forEach(([, key]) => r.appendChild(node('td', row[key] == null ? 'Unknown' : String(row[key]))));
      body.appendChild(r);
    });
    t.appendChild(body); wrap.appendChild(t); content.appendChild(wrap);
  }
  async function load() {
    if (refresh.disabled) return;
    refresh.disabled = true;
    content.replaceChildren(node('p', 'Loading research metadata…'));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/research/ml/status', {
        credentials:'same-origin', cache:'no-store', signal:controller.signal
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        const messages = {
          FORBIDDEN:'Sign in as an operator admin to view research status.',
          AUTH_REQUIRED:'Your session has expired. Sign in again.',
          DB_NOT_CONFIGURED:'Research database is not configured.',
          RESEARCH_SCHEMA_MISSING:'Research tables are missing. Review migrations before deployment.'
        };
        throw new Error(messages[data.error_code] || 'Research status is unavailable. Retry later; no counts have been assumed.');
      }
      if (![data.collection, data.labels, data.jobs].every(Array.isArray)) throw new Error('Invalid status response.');
      content.replaceChildren();
      content.appendChild(node('p', `Scope: ${data.environment}. Snapshots observed from ${data.window_start} through ${data.checked_at} (UTC).`));
      content.appendChild(node('p', 'Counts are operational metadata only. Labels and pending jobs below belong to snapshots in this seven-day window; older history is excluded.'));
      table('Collected snapshots', data.collection, [['Source','source'],['Version','feature_version'],['Quality','quality'],['Count','snapshots'],['Latest observation (UTC)','latest_snapshot']]);
      table('Stored labels — not verified trade outcomes', data.labels, [['Source','source'],['Features','feature_version'],['Label version','label_version'],['Horizon (hours)','horizon_hours'],['Count','labels'],['Latest label (UTC)','latest_label']]);
      table('Unresolved label jobs', data.jobs, [['Status','status'],['Count','jobs']]);
    } catch (error) {
      content.replaceChildren(node('p', error.name === 'AbortError' ? 'Status request timed out. Retry when ready.' : error.message));
    } finally { clearTimeout(timer); refresh.disabled = false; }
  }
  document.getElementById('cm-ml-status').addEventListener('click', () => { dialog.showModal(); load(); });
  document.getElementById('ml-research-close').addEventListener('click', () => dialog.close());
  refresh.addEventListener('click', load);
})();
