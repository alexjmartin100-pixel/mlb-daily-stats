// Service worker for the installable dashboard.
// Same-origin GETs only. Firestore and Auth use cross-origin POSTs.
// Intercepting those with respondWith(fetch()) never settles on iPhone
// Safari, which left "Link this team" stuck on "Linking…".

var STATIC_CACHE = "mlb-static-v2";

self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (key) { return key !== STATIC_CACHE; })
             .map(function (key) { return caches.delete(key); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

function isPhoto(path) {
  return path.indexOf("/photos/") === 0;
}

function isStaticAsset(path) {
  return isPhoto(path)
    || path === "/manifest.json"
    || path === "/icon-192.png"
    || path === "/icon-512.png"
    || path === "/mickey.png";
}

self.addEventListener("fetch", function (event) {
  if (event.request.method !== "GET") return;
  var url;
  try { url = new URL(event.request.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;

  var path = url.pathname;

  // Daily JSON must come from the network. A cached copy would show
  // yesterday's slate after the GitHub Action publishes a new one.
  if (path.indexOf("/data/") === 0) {
    event.respondWith(fetch(event.request, { cache: "no-store" }));
    return;
  }

  // Shell, scripts, and the worker itself: network first so a deploy
  // is visible on the next load. Photos and icons are safe to reuse.
  if (isStaticAsset(path)) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(function (cache) {
        return cache.match(event.request).then(function (hit) {
          if (hit) return hit;
          return fetch(event.request).then(function (response) {
            if (response && response.ok) cache.put(event.request, response.clone());
            return response;
          });
        });
      })
    );
    return;
  }

  event.respondWith(fetch(event.request));
});
