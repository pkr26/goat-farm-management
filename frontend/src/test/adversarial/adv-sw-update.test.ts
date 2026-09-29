/**
 * ADVERSARIAL AUDIT — service-worker update safety (2026-09-28 audit, H1/W7;
 * 2026-09-29: source pins replaced by EXECUTION).
 *
 * public/sw.js is a static file with zero build-time checking: a regression
 * here wedges every field tablet at once. Source-substring pins once guarded
 * these invariants and a one-parenthesis rewrite of the activate handler
 * slipped past them (2026-09-29 audit) — so this suite now EXECUTES the
 * worker against stubbed browser APIs and asserts the observable behavior:
 *   H1  the shell is network-first with a bounded wait — a constant cache
 *       name can never pin a tablet to its install-time build, and a wedged
 *       connection falls back to the cached shell instead of blanking it;
 *   H1  a failed precache fails the install, never activates a worker with
 *       no cached shell;
 *   W7  activate deletes every OTHER cache name and claims open clients
 *       (the exact step the paren regression broke);
 *   W7  client-navigation RSC requests (?_rsc=…) are never intercepted;
 *   —   /api stays network-only: no cache read, no cache write.
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
            const response = await overrides.fetchImpl?.(url) ?? makeResponse();
            if (!response.ok) throw new TypeError(` precache failed: ${url}`);
            bucket.set(new URL(url, ORIGIN).toString(), response.clone());
          }
        },
        put: async (request: { url?: string }, response: Response) => {
          bucket.set(request.url ?? String(request), response.clone());
        },
      };
    },
    keys: async () => [...cacheStore.keys()],
    delete: async (name: string) => {
      state.deletedCaches.push(name);
      return cacheStore.delete(name);
    },
    match: async (request: { url?: string }) => {
      const url = request.url ?? String(request);
      for (const bucket of cacheStore.values()) {
        const hit = bucket.get(url);
        if (hit) return hit;
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
    expect(worker.cacheStore.get("herdly-worker-v2")?.has(`${ORIGIN}/worker`)).toBe(true);
  });

  it("fails the install when the precache fails (never activates a shell-less worker)", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.resolve(new Response("gateway down", { status: 502 }))) as unknown as typeof fetch,
    });
    await expect(worker.fire("install")).rejects.toBeTruthy();
    expect(worker.state.skipWaiting).toBe(0);
  });

  it("activate deletes every OTHER cache and claims open clients (the paren-regression step)", async () => {
    const worker = loadWorker();
    worker.cacheStore.set("herdly-worker-v1", new Map());
    worker.cacheStore.set("herdly-worker-v2", new Map());
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
    worker.cacheStore.set("herdly-worker-v2", bucket);
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
    worker.fire("fetch", {
      request: { method: "GET", url: `${ORIGIN}/worker` },
      respondWith: (p: unknown) => {
        respondArgument = p;
      },
    });
    const response = (await respondArgument) as Response;
    expect(worker.state.fetchCalls).toEqual([`${ORIGIN}/worker`]);
    expect(await response.text()).toBe("<fresh shell>");
    // The fresh copy lands in the cache for the next offline open.
    await Promise.resolve();
    await Promise.resolve();
    expect(worker.cacheStore.get("herdly-worker-v2")?.has(`${ORIGIN}/worker`)).toBe(true);
  });

  it("falls back to the cached shell when the network fails outright", async () => {
    const worker = loadWorker({
      fetchImpl: (() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch,
    });
    worker.cacheStore.set("herdly-worker-v2", new Map([[`${ORIGIN}/worker`, makeResponse("<cached shell>")]]));
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

  it("falls back to the cached shell when the connection wedges (bounded wait)", async () => {
    // A fetch that never settles; the timeout arm fires immediately.
    const worker = loadWorker({
      fetchImpl: (() => new Promise<Response>(() => {})) as unknown as typeof fetch,
      setTimeoutFn: ((callback: () => void) => {
        callback();
        return 0;
      }) as unknown as typeof setTimeout,
    });
    worker.cacheStore.set("herdly-worker-v2", new Map([[`${ORIGIN}/worker`, makeResponse("<cached shell>")]]));
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
