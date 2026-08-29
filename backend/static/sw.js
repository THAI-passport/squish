// Squish service worker.
//
// The app deliberately serves index.html with Cache-Control: no-cache so a
// redeploy is never masked by a stale page. A naive cache-FIRST worker would
// undo that -- users would keep the old UI after an upgrade with no way to
// refresh. So:
//   * navigations / HTML  -> network-FIRST, cache only as an offline fallback
//   * other static GETs   -> stale-while-revalidate (fast, but self-updating)
//   * /api/* and /vendor/ -> always network (never cached)
// and every deploy bumps CACHE so old entries are dropped on activate.
const CACHE = 'squish-v9';
const FALLBACK = ['/', '/index.html', '/favicon.svg', '/manifest.json', '/vault.js'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FALLBACK)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    // Drop caches from older versions so an upgrade cannot serve stale assets.
    for (const key of await caches.keys()) {
      if (key !== CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  // Never touch API calls, and never cache the vendored pdf.js (large, and the
  // network copy is authoritative).
  if (req.method !== 'GET' || url.pathname.startsWith('/api/') ||
      (url.pathname.startsWith('/vendor/') && !url.pathname.startsWith('/vendor/pyodide/'))) return;

  // HTML / navigations: network-first so a new deploy is picked up immediately;
  // fall back to cache only when offline.
  if (req.mode === 'navigate' ||
      (req.headers.get('accept') || '').includes('text/html')) {
    e.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const c = await caches.open(CACHE);
        c.put('/index.html', fresh.clone());
        return fresh;
      } catch {
        return (await caches.match(req)) || (await caches.match('/index.html'));
      }
    })());
    return;
  }

  // Other static GETs: serve cached fast, refresh in the background.
  e.respondWith((async () => {
    const cached = await caches.match(req);
    const network = fetch(req).then(res => {
      if (res && res.ok) caches.open(CACHE).then(c => c.put(req, res.clone()));
      return res;
    }).catch(() => cached);
    return cached || network;
  })());
});
