/* Herdly worker tablet service worker (ITEM 2 Phase 2, 2026-09-21 playbook).
 *
 * Network-first for /api (never serve stale operational data; a failed fetch
 * falls through to the caller so the offline queue can take over), cache-first
 * for the static build assets and the worker shell so the board opens in a
 * dead zone. No opaque cross-origin caching; no push/sync (v1 out of scope).
 */
const CACHE = "herdly-worker-v1";
const SHELL = ["/worker", "/worker/login", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => {})
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    // Network-first, no cache fallback: mutations and live data must never be
    // served stale on the tablet.
    event.respondWith(fetch(request));
    return;
  }
  if (url.pathname.startsWith("/_next/static/") || SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ??
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          }),
      ),
    );
  }
});
