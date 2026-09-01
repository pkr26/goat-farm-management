import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

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
  server.resetHandlers();
  localStorage?.clear?.();
});

afterAll(() => server.close());
