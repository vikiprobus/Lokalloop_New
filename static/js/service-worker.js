const CACHE_NAME = 'lokalloop-pwa-v2.5';
const OFFLINE_URL = '/offline/';

// Core static assets to cache on install for offline experience
const STATIC_SHELL_ASSETS = [
  '/',
  '/offline/',
  '/static/css/design_tokens.css',
  '/static/css/design_tokens.css?v=2.5',
  '/static/css/main.css',
  '/static/css/main.css?v=2.5',
  '/static/vendor/html5-qrcode.min.js',
  '/static/images/favicon.svg',
  '/static/images/icon-192.svg',
  '/static/images/icon-512.svg',
  '/static/manifest.json',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css'
];

// Install: Cache critical shell assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_SHELL_ASSETS).catch((err) => {
        // Tolerant of external CDN fetch issues during initial service worker install
        console.warn('[LokalLoop PWA] Pre-caching partial shell assets:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate: Clean up previous cache versions
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: Network-first strategy with cache fallback and offline page
self.addEventListener('fetch', (event) => {
  // Only handle GET requests
  if (event.request.method !== 'GET') {
    return;
  }

  const url = new URL(event.request.url);

  // BUG-2.1: Strictly exclude authenticated dashboards, APIs, and admin routes from service worker HTML caching
  // This guarantees user session privacy and prevents stale CSRF tokens from causing 403 Forbidden errors
  const isAuthenticatedOrApiRoute = (
    url.pathname.startsWith('/buyer/') ||
    url.pathname.startsWith('/seller/') ||
    url.pathname.startsWith('/admin-portal/') ||
    url.pathname.startsWith('/portal-admin/') ||
    url.pathname.startsWith('/admin/') ||
    url.pathname.startsWith('/api/')
  );

  if (isAuthenticatedOrApiRoute) {
    if (event.request.mode === 'navigate' || event.request.headers.get('accept')?.includes('text/html')) {
      // Directly fetch live authenticated page; if offline, fallback to dedicated offline page
      event.respondWith(
        fetch(event.request).catch(() => caches.match(OFFLINE_URL))
      );
    }
    return;
  }

  // Static files: Cache-first strategy for fast asset loading
  if (url.pathname.startsWith('/static/') || url.origin.includes('cdn.jsdelivr.net') || url.origin.includes('fonts.googleapis.com')) {
    event.respondWith(
      caches.match(event.request, { ignoreSearch: true }).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200 && !isAuthenticatedOrApiRoute) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return networkResponse;
        });
      })
    );
    return;
  }

  // HTML page navigations: Network-first with offline.html fallback
  if (event.request.mode === 'navigate' || event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(event.request)
        .then((networkResponse) => {
          // If valid response, update cache for this page
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(() => {
          // Try to return cached version of this page if previously visited
          return caches.match(event.request).then((cachedPage) => {
            if (cachedPage) {
              return cachedPage;
            }
            // Return dedicated offline fallback page
            return caches.match(OFFLINE_URL);
          });
        })
    );
    return;
  }

  // Other assets (images, fonts)
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      return (
        cachedResponse ||
        fetch(event.request).then((networkResponse) => {
          return networkResponse;
        }).catch(() => {
          // Fallback gracefully for missing images
          return new Response('', { status: 408, statusText: 'Offline' });
        })
      );
    })
  );
});
