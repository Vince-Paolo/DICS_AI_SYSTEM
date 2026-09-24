const APP_CACHE = 'dics-app-shell-v2';
const MAP_CACHE = 'dics-map-cache-v1';
const APP_SHELL = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.webmanifest',
  '/static/offline.html',
  '/emergency-assistance'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== APP_CACHE && key !== MAP_CACHE).map(key => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  const isSameOrigin = url.origin === self.location.origin;

  if (!isSameOrigin) return;

  // Emergency hotline page: network first so the numbers and language are
  // always current, with the last copy kept for when there is no connection.
  // (Calling still works offline because tel: links use the phone network.)
  if (url.pathname === '/emergency-assistance') {
    event.respondWith(
      fetch(request).then(response => {
        if (response && response.status === 200) {
          const copy = response.clone();
          caches.open(APP_CACHE).then(cache => cache.put('/emergency-assistance', copy));
        }
        return response;
      }).catch(() => caches.match('/emergency-assistance')
        .then(cached => cached || caches.match('/static/offline.html'))
        .then(fallback => fallback || Response.error()))
    );
    return;
  }

  if (url.pathname.startsWith('/api/map-pins')) {
    event.respondWith(
      caches.open(MAP_CACHE).then(async cache => {
        try {
          const response = await fetch(request);
          if (response && response.status === 200) {
            cache.put(request.url, response.clone());
          }
          return response;
        } catch (error) {
          const cached = await cache.match(request.url);
          if (cached) return cached;
          return caches.match('/static/offline.html') || Response.error();
        }
      })
    );
    return;
  }

  if (url.pathname.endsWith('.html') || url.pathname === '/' || url.pathname.startsWith('/dashboard') || url.pathname.startsWith('/hazard-map') || url.pathname.startsWith('/analytics')) {
    event.respondWith(
      caches.match(request, { ignoreSearch: true }).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(APP_CACHE).then(cache => cache.put(request, copy));
          }
          return response;
        }).catch(() => caches.match('/static/offline.html') || Response.error());
      })
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(response => {
        if (response && response.status === 200) {
          const copy = response.clone();
          caches.open(APP_CACHE).then(cache => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match('/static/offline.html') || Response.error());
    })
  );
});
