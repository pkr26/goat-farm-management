import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { clearIdempotencyRequestState } from "./src/lib/idempotent-request";
import { server } from "./src/test/msw-server";

/** Minimal Web Storage shim. Node >= 25 ships an experimental host
 *  localStorage that shadows the jsdom realm's and evaluates to undefined
 *  unless --localstorage-file is passed; without this shim every
 *  storage-touching test fails on current Node. Installed as a configurable
 *  property so tests that deliberately block or quota-limit storage can
 *  still replace it per-case. */
class InMemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length(): number {
    return this.store.size;
  }
  key(index: number): string | null {
    return [...this.store.keys()][index] ?? null;
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  setItem(key: string, value: string): void {
    this.store.set(String(key), String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  clear(): void {
    this.store.clear();
  }
}

function ensureStorage(name: "localStorage" | "sessionStorage"): void {
  const existing = (globalThis as Record<string, unknown>)[name] as
    | Storage
    | undefined;
  if (typeof existing?.getItem === "function") return;
  Object.defineProperty(globalThis, name, {
    value: new InMemoryStorage(),
    configurable: true,
    writable: true,
    enumerable: true,
  });
}
ensureStorage("localStorage");
ensureStorage("sessionStorage");

// The realm can end up with a second Storage class (Node's experimental
// webidl globals, or a shadowed jsdom install) that is NOT the class the
// real storage instances use. Bare `Storage.prototype` references in tests
// must resolve to the instances' class, or prototype spies — the standard
// jsdom pattern — silently record nothing. Alias it before anything else.
const nativeSessionProto = Object.getPrototypeOf(
  window.sessionStorage,
) as Storage | null;
const nativeStorageCtor = nativeSessionProto?.constructor;
if (
  typeof window.sessionStorage?.getItem === "function" &&
  nativeStorageCtor &&
  (globalThis as { Storage?: unknown }).Storage !== nativeStorageCtor
) {
  Object.defineProperty(globalThis, "Storage", {
    value: nativeStorageCtor,
    configurable: true,
    writable: true,
  });
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  // A deliberately unresolved mutation must not coalesce an identical
  // mutation in the next test. Browser navigation does not cross this
  // boundary, but the shared Vitest realm does.
  clearIdempotencyRequestState();
  server.resetHandlers();
  // On Node >= 25 an experimental host localStorage can shadow the jsdom
  // realm's — `localStorage` may resolve to a different Storage instance
  // than `window.localStorage`, the one the app actually writes. Clear BOTH
  // or a farm id written by one test file bleeds into the next on the same
  // worker (observed as cross-file "farmId" assertions seeing other files'
  // values).
  localStorage?.clear?.();
  window.localStorage?.clear?.();
});

afterAll(() => server.close());
