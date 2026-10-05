/**
 * ADVERSARIAL AUDIT — service-worker update safety. public/sw.js is a static file
 * with zero build-time checking: a regression here wedges every field tablet at
 * once. Source-substring pins once guarded these invariants and a one-parenthesis
 * rewrite of the activate handler slipped past them — so this suite now EXECUTES the
 * worker against stubbed browser APIs and asserts the observable behavior: H1 the
 * shell is network-first with a bounded wait — a constant cache name can never pin a
 * tablet to its install-time build, and a wedged connection falls back to the cached
 * shell instead of blanking it; H1 a failed precache fails the install, never
 * activates a worker with no cached shell; W7 activate deletes every OTHER cache
 * name and claims open clients (the exact step the paren regression broke); W7
 * client-navigation RSC requests (?_rsc=…) are never intercepted; — /api stays
 * network-only: no cache read, no cache write.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SW_SOURCE = readFileSync(join(import.meta.dirname, "..", "..", "..", "public", "sw.js"), "utf8");
const ORIGIN = "https://tablet.local";

type Listener = (event: Record<string, unknown>) => void;

function makeResponse(body = "<html></html>", ok = true): Response {
  return new Response(body, { status: ok ? 200 : 500 });
}

function cacheKey(input: string | URL | { url?: string }): string {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url ?? String(input);
  return new URL(url, ORIGIN).toString();
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

/** One loaded service-worker instance with instrumented browser APIs. */
function loadWorker(overrides: { fetchImpl?: typeof fetch; setTimeoutFn?: typeof setTimeout } = {}) {
  const listeners = new Map<string, Listener>();
  const state = {
    skipWaiting: 0,
    claims: 0,
    deletedCaches: [] as string[],
    fetchCalls: [] as string[],
  };
  // cache name → url → Response
  const cacheStore = new Map<string, Map<string, Response>>();

  const cachesStub = {
    open: async (name: string) => {
      if (!cacheStore.has(name)) cacheStore.set(name, new Map());
      const bucket = cacheStore.get(name)!;
      return {
        addAll: async (urls: string[]) => {
          for (const url of urls) {
            const response = await fetchStub(url);
            if (!response.ok) throw new TypeError(` precache failed: ${url}`);
            bucket.set(cacheKey(url), response.clone());
          }
        },
        match: async (url: string | URL | { url?: string }) => bucket.get(cacheKey(url))?.clone(),
        put: async (request: string | URL | { url?: string }, response: Response) => {
          bucket.set(cacheKey(request), response.clone());
        },
        keys: async () => [...bucket.keys()].map((url) => ({ url })),
        delete: async (request: string | URL | { url?: string }) => bucket.delete(cacheKey(request)),
      };
    },
    keys: async () => [...cacheStore.keys()],
    delete: async (name: string) => {
      state.deletedCaches.push(name);
      return cacheStore.delete(name);
    },
    match: async (request: string | URL | { url?: string }) => {
      const url = cacheKey(request);
      for (const bucket of cacheStore.values()) {
        const hit = bucket.get(url);
        if (hit) return hit.clone();
      }
      return undefined;
    },
  };

  const selfStub = {
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    skipWaiting: () => {
      state.skipWaiting += 1;
    },
    clients: { claim: () => {
      state.claims += 1;
      return Promise.resolve();
    } },
    location: { origin: ORIGIN },
  };

  const fetchStub: typeof fetch = ((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : (input as { url?: string }).url ?? String(input);
    state.fetchCalls.push(url);
    return overrides.fetchImpl ? overrides.fetchImpl(input) : Promise.resolve(makeResponse());
  }) as typeof fetch;

  // Shadow self/caches/fetch/setTimeout; everything else (Promise, URL,
  // Response…) resolves to the real globals, exactly like a real worker.
  const run = new Function("self", "caches", "fetch", "setTimeout", SW_SOURCE);
  run(selfStub, cachesStub, fetchStub, overrides.setTimeoutFn ?? setTimeout);

  const fire = (type: string, event: Record<string, unknown> = {}) => {
    const listener = listeners.get(type);
    if (listener === undefined) throw new Error(`no ${type} listener registered`);
    let waitUntilPromise: Promise<unknown> | undefined;
    listener({ ...event, waitUntil: (p: Promise<unknown>) => (waitUntilPromise = p) });
    return waitUntilPromise as Promise<unknown>;
  };

  return { fire, state, cacheStore };
}

describe("service worker update safety (executed)", () => {
  it("versions its cache name so an update can retire stale entries", () => {
    expect(SW_SOURCE).toMatch(/const CACHE = "herdly-worker-v\d+"/);
  });

  it("installs by precaching the shell and skipWaiting on success", async () => {
    const worker = loadWorker();
    await worker.fire("install");
    expect(worker.state.skipWaiting).toBe(1);
    expect(worker.cacheStore.get("herdly-worker-v3")?.has(`${ORIGIN}/worker`)).toBe(true);
  });

  it("consumes streamed shell bodies before waiting for the remaining install response", async () => {
    const manifest = deferred<Response>();
    const drained = new Set<string>();
    const worker = loadWorker({
      fetchImpl: (async (input) => {
        const path = String(input);
        if (path === "/manifest.webmanifest") return manifest.promise;
        const response = makeResponse(path.startsWith("/worker")
          ? '<script src="/_next/static/cold-install.js"></script>' : "asset");
        if (path.startsWith("/worker")) {
          const clone = response.clone.bind(response);
          response.clone = () => {
            const copy = clone();
            const text = copy.text.bind(copy);
            copy.text = async () => {
              const body = await text();
              drained.add(path);
              // Model a cold browser fetch pipeline whose fourth response
              // cannot arrive until the preceding streamed bodies drain.
              if (drained.size === 3) manifest.resolve(makeResponse('{"name":"Herdly"}'));
              return body;
            };
            return copy;
          };
        }
        return response;
      }) as typeof fetch,
    });
    const installation = worker.fire("install");
    await expect.poll(() => worker.state.skipWaiting, { timeout: 1000 }).toBe(1);
    await installation;
    expect([...drained].sort()).toEqual(["/worker", "/worker/login", "/worker/offline"].sort());
    const cached = worker.cacheStore.get("herdly-worker-v3")!;
    expect(cached.has(`${ORIGIN}/manifest.webmanifest`)).toBe(true);
    expect(cached.has(`${ORIGIN}/_next/static/cold-install.js`)).toBe(true);
    expect(cached.has(`${ORIGIN}/worker/offline`)).toBe(true);
  });

  it("fails the install when the precache fails (never activates a shell-less worker)", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.resolve(new Response("gateway down", { status: 502 }))) as unknown as typeof fetch,
    });
    await expect(worker.fire("install")).rejects.toBeTruthy();
    expect(worker.state.skipWaiting).toBe(0);
  });

  it("warms unvisited offline-route scripts and styles before activating", async () => {
    const worker = loadWorker({
      fetchImpl: (async (input) => {
        const path = String(input);
        return makeResponse(path.startsWith("/worker")
          ? '<script src="/_next/static/offline.js"></script><link href="/_next/static/offline.css" rel="stylesheet"><script src="https://other.test/_next/static/foreign.js"></script>'
          : "asset");
      }) as typeof fetch,
    });
    await worker.fire("install");
    const cached = worker.cacheStore.get("herdly-worker-v3")!;
    expect(cached.has(`${ORIGIN}/worker/offline`)).toBe(true);
    expect(cached.has(`${ORIGIN}/_next/static/offline.js`)).toBe(true);
    expect(cached.has(`${ORIGIN}/_next/static/offline.css`)).toBe(true);
    expect(cached.has("https://other.test/_next/static/foreign.js")).toBe(false);
    expect(worker.state.skipWaiting).toBe(1);
  });

  it("does not activate when an offline-route chunk fails to cache", async () => {
    const worker = loadWorker({
      fetchImpl: (async (input) => String(input).includes("/_next/static/")
        ? makeResponse("missing", false)
        : makeResponse('<script src="/_next/static/offline.js"></script>')) as typeof fetch,
    });
    const previousShell = '<script src="/_next/static/previous.js"></script>';
    worker.cacheStore.set("herdly-worker-v3", new Map([
      [`${ORIGIN}/worker`, makeResponse(previousShell)],
      [`${ORIGIN}/worker/login`, makeResponse(previousShell)],
      [`${ORIGIN}/worker/offline`, makeResponse(previousShell)],
      [`${ORIGIN}/_next/static/previous.js`, makeResponse("previous working build")],
    ]));
    await expect(worker.fire("install")).rejects.toThrow();
    expect(worker.state.skipWaiting).toBe(0);
    // The failed candidate uses the same named cache as the active worker.
    // Staging must not replace its working HTML with missing dependencies.
    const cached = worker.cacheStore.get("herdly-worker-v3")!;
    for (const path of ["/worker", "/worker/login", "/worker/offline"]) {
      expect(await cached.get(`${ORIGIN}${path}`)!.clone().text()).toBe(previousShell);
    }
    expect(await cached.get(`${ORIGIN}/_next/static/previous.js`)!.clone().text()).toBe("previous working build");
  });

  it("retains only the current and previous shell asset generations", async () => {
    let generation = 1;
    const worker = loadWorker({
      fetchImpl: (async (input) => {
        const path = String(input);
        return makeResponse(path.startsWith("/worker")
          ? `<script src="/_next/static/build-${generation}.js"></script>`
          : `generation ${generation}`);
      }) as typeof fetch,
    });

    await worker.fire("install");
    generation = 2;
    await worker.fire("install");
    generation = 3;
    await worker.fire("install");

    const cached = worker.cacheStore.get("herdly-worker-v3")!;
    expect(cached.has(`${ORIGIN}/_next/static/build-1.js`)).toBe(false);
    expect(cached.has(`${ORIGIN}/_next/static/build-2.js`)).toBe(true);
    expect(cached.has(`${ORIGIN}/_next/static/build-3.js`)).toBe(true);
    const state = await cached.get(`${ORIGIN}/__herdly_worker_asset_state_v1__`)!.clone().json();
    expect(state).toEqual({
      build: `legacy:${ORIGIN}/_next/static/build-3.js`,
      current: [`${ORIGIN}/_next/static/build-3.js`],
      previous: [`${ORIGIN}/_next/static/build-2.js`],
    });
  });

  it("activate deletes every OTHER cache and claims open clients (the paren-regression step)", async () => {
    const worker = loadWorker();
    worker.cacheStore.set("herdly-worker-v1", new Map());
    worker.cacheStore.set("herdly-worker-v3", new Map());
    worker.cacheStore.set("stale-extra", new Map());
    // The 2026-09-29 regression chained .map off Promise.all's result — a
    // TypeError on every activation, so this await throws on the broken
    // handler. Execution pins the real behavior.
    await worker.fire("activate");
    expect(worker.state.deletedCaches.sort()).toEqual(["herdly-worker-v1", "stale-extra"]);
    expect(worker.state.claims).toBe(1);
  });

  it("keeps /api network-only — respondWith gets the raw fetch, caches untouched", async () => {
    let respondArgument: unknown;
    const worker = loadWorker();
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/api/tasks` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    expect(worker.state.fetchCalls).toEqual([`${ORIGIN}/api/tasks`]);
    await expect(respondArgument).resolves.toBeInstanceOf(Response);
  });

  it("serves hashed static assets cache-first without touching the network", async () => {
    const worker = loadWorker();
    const bucket = new Map([[`${ORIGIN}/_next/static/chunk-abc.js`, makeResponse("cached chunk")]]);
    worker.cacheStore.set("herdly-worker-v3", bucket);
    let respondArgument: unknown;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/_next/static/chunk-abc.js` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    expect(worker.state.fetchCalls).toEqual([]);
    const response = (await respondArgument) as Response;
    expect(await response.text()).toBe("cached chunk");
  });

  it("serves the shell network-first and caches the fresh copy", async () => {
    const worker = loadWorker({ fetchImpl: (() => Promise.resolve(makeResponse("<fresh shell>"))) as unknown as typeof fetch });
    let respondArgument: unknown;
    const lifetime = worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(worker.state.fetchCalls).toEqual([`${ORIGIN}/worker`]);
    expect(await response.text()).toBe("<fresh shell>");
    // The fresh copy lands in the cache for the next offline open.
    await lifetime;
    expect(worker.cacheStore.get("herdly-worker-v3")?.has(`${ORIGIN}/worker`)).toBe(true);
  });

  it("falls back to the cached shell when the network fails outright", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch,
    });
    worker.cacheStore.set("herdly-worker-v3", new Map([[`${ORIGIN}/worker`, makeResponse("<cached shell>")]]));
    let respondArgument: unknown;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(await response.text()).toBe("<cached shell>");
  });

  it("prefers a complete cached shell over a fast non-OK network response", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.resolve(new Response("upstream unavailable", { status: 503 }))) as unknown as typeof fetch,
    });
    worker.cacheStore.set("herdly-worker-v3", new Map([[`${ORIGIN}/worker`, makeResponse("<cached shell>")]]));
    let respondArgument: unknown;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("<cached shell>");
  });

  it("preserves a non-OK network response when no cached shell exists", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.resolve(new Response("upstream unavailable", { status: 503 }))) as unknown as typeof fetch,
    });
    let respondArgument: unknown;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(response.status).toBe(503);
    expect(await response.text()).toBe("upstream unavailable");
  });

  it("falls back to the cached shell when the connection wedges (bounded wait)", async () => {
    // A fetch that never settles; the timeout arm fires immediately.
    const worker = loadWorker({
      fetchImpl: (() => new Promise<Response>(() => {})) as unknown as typeof fetch,
      setTimeoutFn: ((callback: () => void) => {
        callback();
        return 0;
      }) as unknown as typeof setTimeout,
    });
    worker.cacheStore.set("herdly-worker-v3", new Map([[`${ORIGIN}/worker`, makeResponse("<cached shell>")]]));
    let respondArgument: unknown;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(await response.text()).toBe("<cached shell>");
  });

  it("keeps a late deploy refresh alive and publishes its HTML only after its chunks cache", async () => {
    const shell = deferred<Response>();
    const chunk = deferred<Response>();
    let offline = false;
    const previousShell = '<script src="/_next/static/previous.js"></script>';
    const newShell = '<script src="/_next/static/new-deploy.js"></script>';
    const worker = loadWorker({
      fetchImpl: (async (input) => {
        if (offline) throw new TypeError("offline");
        return cacheKey(input as string | URL | { url?: string }).endsWith("/_next/static/new-deploy.js")
          ? chunk.promise : shell.promise;
      }) as typeof fetch,
      setTimeoutFn: ((callback: () => void) => { callback(); return 0; }) as unknown as typeof setTimeout,
    });
    const cached = new Map([
      [`${ORIGIN}/worker/offline`, makeResponse(previousShell)],
      [`${ORIGIN}/_next/static/previous.js`, makeResponse("previous build")],
    ]);
    worker.cacheStore.set("herdly-worker-v3", cached);
    let navigation!: Promise<Response>;
    const lifetime = worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker/offline` },
      respondWith: (promise: Promise<Response>) => { navigation = promise; },
    });
    expect(await (await navigation).text()).toBe(previousShell);
    expect(lifetime).toBeInstanceOf(Promise);
    shell.resolve(makeResponse(newShell));
    await expect.poll(() => worker.state.fetchCalls).toContain(`${ORIGIN}/_next/static/new-deploy.js`);
    expect(await cached.get(`${ORIGIN}/worker/offline`)!.clone().text()).toBe(previousShell);
    chunk.resolve(makeResponse("new working build"));
    await lifetime;
    expect(await cached.get(`${ORIGIN}/worker/offline`)!.clone().text()).toBe(newShell);
    expect(await cached.get(`${ORIGIN}/_next/static/new-deploy.js`)!.clone().text()).toBe("new working build");

    // The next cold navigation can hydrate even though no client consumed
    // the late new-build response or visited its route while online.
    offline = true;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker/offline` },
      respondWith: (promise: Promise<Response>) => { navigation = promise; },
    });
    expect(await (await navigation).text()).toBe(newShell);
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/_next/static/new-deploy.js` },
      respondWith: (promise: Promise<Response>) => { navigation = promise; },
    });
    expect(await (await navigation).text()).toBe("new working build");
  });

  it("retains the complete previous shell when a navigation refresh cannot cache its chunks", async () => {
    const previousShell = '<script src="/_next/static/previous.js"></script>';
    const worker = loadWorker({
      fetchImpl: (async (input) => cacheKey(input as string | URL | { url?: string }).includes("/_next/static/")
        ? makeResponse("missing chunk", false)
        : makeResponse('<script src="/_next/static/missing.js"></script>')) as typeof fetch,
      setTimeoutFn: ((callback: () => void) => { callback(); return 0; }) as unknown as typeof setTimeout,
    });
    const cached = new Map([
      [`${ORIGIN}/worker/offline`, makeResponse(previousShell)],
      [`${ORIGIN}/_next/static/previous.js`, makeResponse("previous working build")],
    ]);
    worker.cacheStore.set("herdly-worker-v3", cached);
    let navigation!: Promise<Response>;
    const lifetime = worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker/offline` },
      respondWith: (promise: Promise<Response>) => { navigation = promise; },
    });
    await navigation;
    expect(lifetime).toBeInstanceOf(Promise);
    await lifetime;
    expect(await cached.get(`${ORIGIN}/worker/offline`)!.clone().text()).toBe(previousShell);
    expect(await cached.get(`${ORIGIN}/_next/static/previous.js`)!.clone().text()).toBe("previous working build");
    expect(cached.has(`${ORIGIN}/_next/static/missing.js`)).toBe(false);
  });

  it("answers a shell miss with no cached copy as a network error, not a hang", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch,
    });
    let respondArgument: unknown;
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(response.type).toBe("error");
  });

  it("never intercepts client-navigation RSC requests (query-string guard)", () => {
    const worker = loadWorker();
    const event = {
      request: { method: "GET", url: `${ORIGIN}/worker?_rsc=abc` },
      respondWith: () => {
        throw new Error("RSC navigations must never be intercepted");
      },
    };
    expect(() => worker.fire("fetch", event)).not.toThrow();
  });

  it("still declares the RSC/query guard in source (defense in depth)", () => {
    expect(SW_SOURCE).toContain('SHELL.includes(url.pathname) && url.search === ""');
  });
});

describe("lazy assets survive same-build navigation", () => {
  it("retains locale/fonts across repeated document reloads and rotates only real builds", async () => {
    let build = "one";
    let offline = false;
    const worker = loadWorker({ fetchImpl: (async (input) => {
      if (offline) throw new TypeError("network unavailable");
      const path = new URL(typeof input === "string" ? input : (input as Request).url, ORIGIN).pathname;
      return makeResponse(path.startsWith("/worker")
        ? `<meta name="herdly-build" content="${build}"><script src="/_next/static/${build}-${path.split("/").at(-1)}.js"></script>`
        : "asset bytes");
    }) as typeof fetch });
    const read = async (path: string) => {
      let response!: Promise<Response>;
      const retained = worker.fire("fetch", { request: { method: "GET", url: `${ORIGIN}${path}` },
        respondWith: (value: Promise<Response>) => { response = value; } });
      const result = await response;
      await retained;
      return result;
    };
    await worker.fire("install");
    await Promise.all([read("/_next/static/telugu-one.js"), read("/_next/static/telugu-font-one.woff2")]);
    // A route can expose a different HTML asset list within the same build;
    // neither a route visit nor repeated reload is a deployment boundary.
    for (const path of ["/worker", "/worker/login", "/worker/offline", "/worker/login", "/worker/login"]) await read(path);
    offline = true;
    expect(await (await read("/_next/static/telugu-one.js")).text()).toBe("asset bytes");
    expect(await (await read("/_next/static/telugu-font-one.woff2")).text()).toBe("asset bytes");
    expect((await read("/worker/login")).ok).toBe(true);
    offline = false;
    build = "two";
    await worker.fire("install");
    expect(worker.cacheStore.get("herdly-worker-v3")!.has(`${ORIGIN}/_next/static/telugu-one.js`)).toBe(true);
    build = "three";
    await worker.fire("install");
    expect(worker.cacheStore.get("herdly-worker-v3")!.has(`${ORIGIN}/_next/static/telugu-one.js`)).toBe(false);
    expect(worker.cacheStore.get("herdly-worker-v3")!.has(`${ORIGIN}/_next/static/two-login.js`)).toBe(true);
  });
});

it("warms initial pre-control lazy assets and rejects non-static message URLs", async () => {
  const worker = loadWorker();
  await worker.fire("install");
  await worker.fire("message", { data: { type: "CACHE_RUNTIME_ASSETS", assets: [
    "/_next/static/te.js", "/_next/static/font.woff2", "/api/auth/me", "https://foreign.test/_next/static/foreign.js", null,
  ] } });
  const cached = worker.cacheStore.get("herdly-worker-v3")!;
  expect(cached.has(`${ORIGIN}/_next/static/te.js`)).toBe(true);
  expect(cached.has(`${ORIGIN}/_next/static/font.woff2`)).toBe(true);
  expect(worker.state.fetchCalls).not.toContain("/api/auth/me");
  expect(worker.state.fetchCalls).not.toContain("https://foreign.test/_next/static/foreign.js");
  const state = await cached.get(`${ORIGIN}/__herdly_worker_asset_state_v1__`)!.clone().json();
  expect(state.current).toEqual(expect.arrayContaining([`${ORIGIN}/_next/static/te.js`, `${ORIGIN}/_next/static/font.woff2`]));
});
