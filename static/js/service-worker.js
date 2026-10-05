const APP_CACHE = 'dics-app-shell-v7';
const CDN_CACHE = 'dics-cdn-assets-v1';
const APP_SHELL = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.webmanifest',
  '/static/offline.html',
  '/static/icon-192.svg',
  '/static/icon-512.svg'
];
const CDN_ASSETS = [
  'https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/5.3.0/css/bootstrap.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/5.3.0/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/fonts/bootstrap-icons.woff2'
];

function isCacheableCdnAsset(url) {
  return (
    (url.origin === 'https://cdnjs.cloudflare.com' &&
      url.pathname.startsWith('/ajax/libs/twitter-bootstrap/5.3.0/')) ||
    (url.origin === 'https://cdn.jsdelivr.net' &&
      url.pathname.startsWith('/npm/bootstrap-icons@1.10.5/font/'))
  );
}

async function cachePublicHotline() {
  const url = new URL('/emergency-assistance', self.location.origin);
  const request = new Request(url.href, { credentials: 'omit' });
  try {
    const response = await fetch(request);
    const cacheControl = response.headers.get('Cache-Control') || '';
    if (response.ok && /(?:^|,)\s*public(?:,|$)/i.test(cacheControl)) {
      const cache = await caches.open(APP_CACHE);
      await cache.put(request, response.clone());
    }
  } catch (error) {
    // The offline shell still installs when the public hotline cannot be fetched.
  }
}

async function cacheCdnAssets() {
  const cache = await caches.open(CDN_CACHE);
  await Promise.all(CDN_ASSETS.map(async assetUrl => {
    const request = new Request(assetUrl, { mode: 'cors' });
    try {
      const response = await fetch(request);
      if (response.ok) await cache.put(request, response.clone());
    } catch (error) {
      // Online visits can populate this cache later if install-time fetch fails.
    }
  }));
}

async function clearPrivateCaches() {
  const keys = await caches.keys();
  await Promise.all(keys.filter(key => key.startsWith('dics-map-cache-')).map(key => caches.delete(key)));
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => Promise.all([cacheCdnAssets(), cachePublicHotline()]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== APP_CACHE && key !== CDN_CACHE).map(key => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  const isSameOrigin = url.origin === self.location.origin;

  if (!isSameOrigin) {
    if (!isCacheableCdnAsset(url)) return;
    event.respondWith(
      caches.open(CDN_CACHE).then(async cache => {
        const cached = await cache.match(request, { ignoreSearch: true });
        if (cached) return cached;
        try {
          const response = await fetch(request);
          if (response.ok || response.type === 'opaque') {
            await cache.put(request, response.clone());
          }
          return response;
        } catch (error) {
          return cached || Response.error();
        }
      })
    );
    return;
  }

  if (url.pathname === '/logout' || url.pathname === '/login') {
    event.respondWith(
      clearPrivateCaches().then(() => fetch(request, { cache: 'no-store' }))
    );
    return;
  }

  if (url.pathname === '/emergency-assistance') {
    event.respondWith(
      fetch(request, { cache: 'no-store' }).then(async response => {
        const cacheControl = response.headers.get('Cache-Control') || '';
        if (response.ok && /(?:^|,)\s*public(?:,|$)/i.test(cacheControl)) {
          const cache = await caches.open(APP_CACHE);
          await cache.put(request, response.clone());
        }
        return response;
      }).catch(async () => {
        const cached = await caches.match('/emergency-assistance');
        return cached || await caches.match('/static/offline.html') || Response.error();
      })
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request, { cache: 'no-store' }).catch(async () => {
        return await caches.match('/static/offline.html') || Response.error();
      })
    );
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(APP_CACHE).then(async cache => {
        const cached = await cache.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        if (response.ok) await cache.put(request, response.clone());
        return response;
      })
    );
    return;
  }

  event.respondWith(fetch(request, { cache: 'no-store' }));
});