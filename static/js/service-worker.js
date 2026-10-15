const APP_CACHE = 'dics-app-shell-__STATIC_ASSET_VERSION__';
const APP_SHELL = [
  '/static/css/style.css',
  '/static/css/tokens.css',
  '/static/css/sidebar.css',
  '/static/css/theme.css',
  '/static/css/landing.css',
  '/static/js/app.js',
  '/static/manifest.webmanifest',
  '/static/offline.html',
  '/static/icon-192.svg',
  '/static/icon-512.svg',
  '/static/vendor/bootstrap/css/bootstrap.min.css',
  '/static/vendor/bootstrap/js/bootstrap.bundle.min.js',
  '/static/vendor/chartjs/chart.umd.js',
  '/static/js/chart-theme.js',
  '/static/vendor/bootstrap-icons/font/bootstrap-icons.css',
  '/static/vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff2',
  '/static/vendor/leaflet/leaflet.css',
  '/static/vendor/leaflet/leaflet.js',
  '/static/vendor/leaflet/images/marker-icon.png',
  '/static/vendor/leaflet/images/marker-icon-2x.png',
  '/static/vendor/leaflet/images/marker-shadow.png',
  '/static/vendor/leaflet/images/layers.png',
  '/static/vendor/leaflet/images/layers-2x.png',
  '/static/vendor/leaflet/plugins/MarkerCluster.css',
  '/static/vendor/leaflet/plugins/MarkerCluster.Default.css',
  '/static/vendor/leaflet/plugins/leaflet.markercluster.js',
  '/static/vendor/leaflet/plugins/leaflet-heat.js'
];

async function cachePublicPage(path) {
  const url = new URL(path, self.location.origin);
  const request = new Request(url.href, { credentials: 'omit' });
  try {
    const response = await fetch(request);
    const cacheControl = response.headers.get('Cache-Control') || '';
    if (response.ok && /(?:^|,)\s*public(?:,|$)/i.test(cacheControl)) {
      const cache = await caches.open(APP_CACHE);
      await cache.put(request, response.clone());
    }
  } catch (error) {
    console.warn(`Could not cache public page ${path} for offline use.`, error);
  }
}

async function clearPrivateCaches() {
  const keys = await caches.keys();
  await Promise.all(keys.filter(key => key.startsWith('dics-map-cache-')).map(key => caches.delete(key)));
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => Promise.all([
        cachePublicPage('/'),
        cachePublicPage('/emergency-assistance')
      ]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== APP_CACHE).map(key => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  const isSameOrigin = url.origin === self.location.origin;

  if (!isSameOrigin) {
    return;
  }

  if (url.pathname === '/logout' || url.pathname === '/login') {
    event.respondWith(
      clearPrivateCaches().then(() => fetch(request, { cache: 'no-store' }))
    );
    return;
  }

  if (url.pathname === '/' || url.pathname === '/emergency-assistance') {
    event.respondWith(
      fetch(request, { cache: 'no-store' }).then(async response => {
        const cacheControl = response.headers.get('Cache-Control') || '';
        if (response.ok && /(?:^|,)\s*public(?:,|$)/i.test(cacheControl)) {
          const cache = await caches.open(APP_CACHE);
          await cache.put(request, response.clone());
        }
        return response;
      }).catch(async () => {
        const cached = await caches.match(request, { ignoreSearch: true });
        return cached || await caches.match('/static/offline.html') || Response.error();
      })
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request, { cache: 'no-store' }).catch(async () => {
        return await caches.match(request, { ignoreSearch: true })
          || await caches.match('/static/offline.html')
          || Response.error();
      })
    );
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(APP_CACHE).then(async cache => {
        const cached = await cache.match(request, { ignoreSearch: true });
        try {
          const response = await fetch(request, { cache: 'no-cache' });
          if (response.ok) await cache.put(request, response.clone());
          return response;
        } catch (error) {
          return cached || Response.error();
        }
      })
    );
    return;
  }

  event.respondWith(fetch(request, { cache: 'no-store' }));
});