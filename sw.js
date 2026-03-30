// Minimal service worker — required for PWA install prompt on Android/Chrome.
// No caching: always fetches fresh data so the daily update is always current.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', event => {
  // Pass every request straight to the network (no caching)
  event.respondWith(fetch(event.request));
});
