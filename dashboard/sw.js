/* CryptoBadshah — Service Worker
   Strategy:
   - HTML / CSS / JS   → Network-first (always fresh), cache as offline fallback
   - API calls (/api/) → Network-only, never cached (live market data)
   - Icons / manifest  → Cache-first (static assets, rarely change)
   - Google Fonts      → Cache-first
*/

const CACHE  = 'cryptobadshah-v3';
const STATIC = [
  '/dashboard/manifest.json',
  '/dashboard/icons/icon-192.png',
  '/dashboard/icons/icon-512.png',
];

// ── Install: pre-cache only static assets that never change ──────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(STATIC))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: remove old caches ──────────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API — always network, no cache
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ error: 'Offline — no cached data available' }),
        { status: 503, headers: { 'Content-Type': 'application/json' } })
    ));
    return;
  }

  // Google Fonts + static icons/manifest — cache-first
  if (url.hostname.includes('fonts.g') || STATIC.some(p => url.pathname === p)) {
    e.respondWith(
      caches.match(e.request).then(cached =>
        cached || fetch(e.request).then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
      )
    );
    return;
  }

  // HTML / CSS / JS — network-first, fall back to cache when offline
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return res;
    }).catch(() => caches.match(e.request))
  );
});
