/* Herdly worker tablet service worker (ITEM 2 Phase 2, 2026-09-21 playbook).
 *
 * Network-only for /api (never serve stale operational data; a failed fetch
 * falls through to the caller so the offline queue can take over). The shell
 * HTML is network-first with a bounded wait: online tablets always
 * revalidate against the current deploy, a wedged-but-alive connection falls
 * back to the cached shell after NAV_TIMEOUT_MS instead of blanking the
 * tablet, and the cache is only the offline fallback, so a constant cache
 * name can never pin a tablet to its install-time build (2026-09-28 audit,
 * H1; 2026-09-29 timeout race). Hashed /_next/static/ assets are immutable
 * and stay cache-first. No opaque cross-origin caching; no push/sync (v1
 * out of scope).
 */
const CACHE = "herdly-worker-v2";
const SHELL = ["/worker", "/worker/login", "/manifest.webmanifest"];
/** Upper bound on a shell navigation's network wait before falling back to
 * the cached copy — a captive portal or stalled 2G link must not blank the
 * board while a perfectly good shell sits in the cache. */
const NAV_TIMEOUT_MS = 4000;

self.addEventListener("install", (event) => {
  // A failed precache fails the install: activating a worker with no cached
  // shell would silently strip offline capability (2026-09-28 audit, H1).
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
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
    // Network-only, no cache fallback: mutations and live data must never be
    // served stale on the tablet.
    event.respondWith(fetch(request));
    return;
  }
  if (url.pathname.startsWith("/_next/static/")) {
    // Content-hashed build assets are immutable: cache-first is correct, and
    // a new deploy's hashes simply miss the old cache.
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
    return;
  }
  if (SHELL.includes(url.pathname) && url.search === "") {
    // Network-first with a bounded wait and offline fallback. Client
    // navigation RSC requests (url.search non-empty, e.g. ?_rsc=…) are never
    // intercepted or cached.
    event.respondWith(
      Promise.race([
        fetch(request)
          .then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
          // A network failure resolves (not rejects) to the fallback below —
          // only a real response wins the race.
          .catch(() => undefined),
        new Promise((resolve) => setTimeout(() => resolve(undefined), NAV_TIMEOUT_MS)),
      ]).then((response) => response ?? caches.match(request).then((cached) => cached ?? Response.error())),
    );
  }
});
