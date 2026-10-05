/* Worker tablet service worker.
 *
 * API requests remain network-only. Shell navigation revalidates against the
 * current deployment, with a bounded wait before using the cached shell.
 * Hashed Next assets are immutable and use cache-first reads. Cross-origin
 * opaque responses, push and background sync are outside this worker's scope.
 */
const CACHE = "herdly-worker-v4";
// The previous worker predates cross-instance locking. Isolate its writes
// during this upgrade, but keep immutable chunks available to open documents
// that still refer to that deployment after clients.claim().
const LEGACY_ASSET_CACHE = "herdly-worker-v3";
const SHELL = ["/worker", "/worker/login", "/worker/offline", "/manifest.webmanifest"];
const ASSET_STATE_KEY = "/__herdly_worker_asset_state_v1__";
/** Upper bound on a shell navigation's network wait before falling back to
 * the cached copy — a captive portal or stalled 2G link must not blank the
 * board while a perfectly good shell sits in the cache. */
const NAV_TIMEOUT_MS = 4000;

// Serialize read/modify/write of asset membership. The origin-wide lock also
// covers an installing worker sharing this cache with the still-active one.
let assetStateWork = Promise.resolve();
function updateAssetState(work) {
  const result = assetStateWork.then(() => typeof self.navigator?.locks?.request === "function"
    ? self.navigator.locks.request(`${CACHE}:assets`, work) : work());
  assetStateWork = result.catch(() => {});
  return result;
}

async function shellBuild(response, assets) {
  const html = await response.clone().text();
  const tag = html.match(/<meta\b[^>]*\bname=["']herdly-build["'][^>]*>/i)?.[0];
  const build = tag?.match(/\bcontent=["']([^"']+)["']/i)?.[1];
  // Old shells predate the marker. Their dependency fingerprint is a bounded
  // migration fallback, not the generation identity of new builds.
  return build ?? `legacy:${[...assets].sort().join("|")}`;
}

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
    if (!response) return { current: [], previous: [], build: null };
    const value = await response.json();
    if (!value || !Array.isArray(value.current) || !Array.isArray(value.previous)) {
      return { current: [], previous: [], build: null };
    }
    return {
      build: typeof value.build === "string" ? value.build : null,
      current: value.current.filter(isStaticAsset),
      previous: value.previous.filter(isStaticAsset),
    };
  } catch {
    return { current: [], previous: [], build: null };
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

async function publishAssetGeneration(cache, currentAssets, fallbackPrevious = new Set(), build = null) {
  const state = await readAssetState(cache);
  const sameBuild = state.build !== null && state.build === build;
  const current = [...new Set([...currentAssets, ...(sameBuild ? state.current : [])].filter(isStaticAsset))];
  // On the first upgrade from the legacy unbounded cache there is no state
  // record. Retain assets referenced by its old complete shell as the one
  // previous generation so already-open clients are not stranded.
  const previous = [...new Set((sameBuild ? state.previous : state.current.length ? state.current : [...fallbackPrevious])
    .filter(isStaticAsset))];
  await cache.put(ASSET_STATE_KEY, new Response(JSON.stringify({ current, previous, build }), {
    headers: { "Content-Type": "application/json" },
  }));
  // Older webviews without a shared lock cannot safely prune a cache that
  // another worker may be staging into. Keep their immutable assets rather
  // than publish HTML whose dependencies an overlapping update deleted.
  if (typeof self.navigator?.locks?.request !== "function") return;
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
    build: state.build,
  }), { headers: { "Content-Type": "application/json" } }));
}

async function cacheCompleteShell(request, response) {
  const cache = await caches.open(CACHE);
  const workerShell = new URL(request.url, self.location.origin).pathname.startsWith("/worker");
  const assets = await shellAssets(response);
  const build = await shellBuild(response, assets);
  await updateAssetState(async () => {
    const previousShellAssets = await cachedShellAssets(cache);
    if (workerShell) {
      // Stage dependencies in the same operation that publishes and prunes.
      // Otherwise an overlapping older navigation can delete these new
      // assets between addAll and the publication of their fallback HTML.
      await cache.addAll([...assets]);
    }
    await cache.put(request, response.clone());
    await publishAssetGeneration(cache, await cachedShellAssets(cache), previousShellAssets,
      workerShell ? build : (await readAssetState(cache)).build);
  });
}

self.addEventListener("install", (event) => {
  // A failed precache fails the install: activating a worker with no cached
  // shell would silently strip offline capability.
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
        // Keep the previously complete shared cache usable if this install
        // fails before all new-build assets are available.
        const build = await shellBuild(responses[0].response, assets);
        await updateAssetState(async () => {
          await cache.addAll([...assets]);
          await Promise.all(responses.map(({ path, response }) => cache.put(path, response)));
          await publishAssetGeneration(cache, assets, previousShellAssets, build);
        });
      })
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE && key !== LEGACY_ASSET_CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

// Warm immutable resources fetched by the first document before clients.claim.
// No API, cross-origin or arbitrary user-data URL is accepted here.
self.addEventListener("message", (event) => {
  if (event.data?.type !== "CACHE_RUNTIME_ASSETS" || !Array.isArray(event.data.assets)) return;
  const assets = [...new Set(event.data.assets.filter((value) => typeof value === "string" && isStaticAsset(value)))].slice(0, 256);
  event.waitUntil(Promise.all(assets.map(async (asset) => {
    try {
      const request = new URL(asset, self.location.origin).href;
      const response = await caches.match(request) ?? await fetch(request);
      if (!response.ok) return;
      await updateAssetState(async () => {
        const cache = await caches.open(CACHE);
        await cache.put(request, response);
        await recordRuntimeAsset(cache, request);
      });
    } catch { /* offline: preserve the previously complete cache */ }
  })));
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
        cacheWrite = updateAssetState(async () => {
          const cache = await caches.open(CACHE);
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
        // A retained legacy cache can contain an older document. It may
        // rescue an immutable chunk, never supersede this complete shell.
        const cached = await (await caches.open(CACHE)).match(request);
        return cached ?? response ?? Response.error();
      }),
    );
  }
});
