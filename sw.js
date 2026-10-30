// Minimal service worker — required for PWA install prompt on Android/Chrome.
// No caching: always fetches fresh data so the daily update is always current.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', event => {
  // Same-origin GETs only. Firestore and Auth are cross-origin POSTs.
  // Claiming those with respondWith(fetch()) never settles on iPhone Safari,
  // which left "Link this team" stuck on "Linking…" with no error.
  if (event.request.method !== 'GET') return;
  var url;
  try { url = new URL(event.request.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;
  event.respondWith(fetch(event.request));
});
