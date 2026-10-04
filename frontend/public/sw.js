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
const CACHE = "herdly-worker-v3";
const SHELL = ["/worker", "/worker/login", "/worker/offline", "/manifest.webmanifest"];
const ASSET_STATE_KEY = "/__herdly_worker_asset_state_v1__";
/** Upper bound on a shell navigation's network wait before falling back to
 * the cached copy — a captive portal or stalled 2G link must not blank the
 * board while a perfectly good shell sits in the cache. */
const NAV_TIMEOUT_MS = 4000;

async function shellAssets(response) {
  const assets = new Set();
  const html = await response.clone().text();
  for (const match of html.matchAll(/(?:src|href)=["']([^"']+)["']/g)) {
    const asset = new URL(match[1].replaceAll("&amp;", "&"), self.location.origin);
    if (asset.origin === self.location.origin && asset.pathname.startsWith("/_next/static/")) {
      assets.add(asset.href);
    }
  }
  return assets;
}

function isStaticAsset(value) {
  try {
    const url = new URL(value, self.location.origin);
    return url.origin === self.location.origin && url.pathname.startsWith("/_next/static/");
  } catch {
    return false;
  }
}

async function readAssetState(cache) {
  try {
    const response = await cache.match(ASSET_STATE_KEY);
    if (!response) return { current: [], previous: [] };
    const value = await response.json();
    if (!value || !Array.isArray(value.current) || !Array.isArray(value.previous)) {
      return { current: [], previous: [] };
    }
    return {
      current: value.current.filter(isStaticAsset),
      previous: value.previous.filter(isStaticAsset),
    };
  } catch {
    return { current: [], previous: [] };
  }
}

async function cachedShellAssets(cache) {
  const assets = new Set();
  for (const path of SHELL.filter((value) => value.startsWith("/worker"))) {
    const response = await cache.match(path);
    if (!response) continue;
    for (const asset of await shellAssets(response)) assets.add(asset);
  }
  return assets;
}

async function publishAssetGeneration(cache, currentAssets, fallbackPrevious = new Set()) {
  const state = await readAssetState(cache);
  const current = [...new Set([...currentAssets].filter(isStaticAsset))];
  // On the first upgrade from the legacy unbounded cache there is no state
  // record. Retain assets referenced by its old complete shell as the one
  // previous generation so already-open clients are not stranded.
  const previous = [...new Set((state.current.length ? state.current : [...fallbackPrevious])
    .filter(isStaticAsset))];
  await cache.put(ASSET_STATE_KEY, new Response(JSON.stringify({ current, previous }), {
    headers: { "Content-Type": "application/json" },
  }));
  const retained = new Set([...current, ...previous]);
  for (const request of await cache.keys()) {
    const href = new URL(request.url, self.location.origin).href;
    if (isStaticAsset(href) && !retained.has(href)) await cache.delete(request);
  }
}

async function recordRuntimeAsset(cache, asset) {
  if (!isStaticAsset(asset)) return;
  const state = await readAssetState(cache);
  const current = [...new Set([...state.current, new URL(asset, self.location.origin).href])];
  await cache.put(ASSET_STATE_KEY, new Response(JSON.stringify({
    current,
    previous: state.previous,
  }), { headers: { "Content-Type": "application/json" } }));
}

async function cacheCompleteShell(request, response) {
  const cache = await caches.open(CACHE);
  const previousShellAssets = await cachedShellAssets(cache);
  if (new URL(request.url, self.location.origin).pathname.startsWith("/worker")) {
    // Publish fallback HTML only after its build's dependencies are durable.
    // A late navigation response may never execute in a client at all.
    await cache.addAll([...await shellAssets(response)]);
  }
  await cache.put(request, response.clone());
  await publishAssetGeneration(cache, await cachedShellAssets(cache), previousShellAssets);
}

self.addEventListener("install", (event) => {
  // A failed precache fails the install: activating a worker with no cached
  // shell would silently strip offline capability (2026-09-28 audit, H1).
  event.waitUntil(
    caches
      .open(CACHE)
      .then(async (cache) => {
        const previousShellAssets = await cachedShellAssets(cache);
        const responses = await Promise.all(SHELL.map(async (path) => {
          const response = await fetch(path, { cache: "reload" });
          if (!response.ok) throw new Error("Offline shell could not be fetched.");
          // Drain streamed HTML as soon as its headers arrive. Waiting for
          // every response before consuming the earlier bodies can stall a
          // cold worker's remaining requests (including its manifest).
          // Keep the original response staged until all assets are durable.
          const assets = path.startsWith("/worker") ? await shellAssets(response) : new Set();
          return { path, response, assets };
        }));
        // A cold reload needs each shell's JS/CSS, including routes the
        // worker has never visited. Fetching HTML alone does not warm those
        // assets, and the first page may have loaded before clients.claim().
        const assets = new Set();
        for (const response of responses) {
          for (const asset of response.assets) assets.add(asset);
        }
        await cache.addAll([...assets]);
        // Keep the previously complete shared cache usable if this install
        // fails before all new-build assets are available.
        await Promise.all(responses.map(({ path, response }) => cache.put(path, response)));
        await publishAssetGeneration(cache, assets, previousShellAssets);
      })
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
    let cacheWrite = Promise.resolve();
    const response = caches.match(request).then((cached) => cached ?? fetch(request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        cacheWrite = caches.open(CACHE).then(async (cache) => {
          await cache.put(request, copy);
          await recordRuntimeAsset(cache, request.url);
        });
      }
      return response;
    }));
    event.respondWith(response);
    event.waitUntil(response.then(() => cacheWrite).catch(() => {}));
    return;
  }
  if (SHELL.includes(url.pathname) && url.search === "") {
    // Network-first with a bounded wait and offline fallback. Client
    // navigation RSC requests (url.search non-empty, e.g. ?_rsc=…) are never
    // intercepted or cached.
    // Only a real network response wins the race.
    const network = fetch(request).catch(() => undefined);
    // Hold the event even when the timeout wins; cache failures retain the
    // previous complete fallback and never reject a successful live response.
    event.waitUntil(network.then((response) => response?.ok
      ? cacheCompleteShell(request, response.clone()) : undefined).catch(() => {}));
    event.respondWith(
      Promise.race([
        network,
        new Promise((resolve) => setTimeout(() => resolve(undefined), NAV_TIMEOUT_MS)),
      ]).then(async (response) => {
        // A reachable proxy is not necessarily a usable application. Prefer
        // the last complete shell when the network answers quickly with an
        // error; return that error only when no offline shell exists so the
        // true outage remains observable rather than becoming a blank page.
        if (response?.ok) return response;
        const cached = await caches.match(request);
        return cached ?? response ?? Response.error();
      }),
    );
  }
});
