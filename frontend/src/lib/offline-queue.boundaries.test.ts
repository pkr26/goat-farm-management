/**
 * Offline queue boundary semantics (2026-09-30 fresh mutation campaign):
 * exact TTL expiry, the exact 256 KiB byte cap, empty-string body
 * round-trip, null-storage drain counts, and the Retry-After backoff gate's
 * exact arithmetic (clear-to-zero, expiry boundary, retryAfter of exactly 1,
 * the ×1000 multiplier and its ±1 neighbours).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  OFFLINE_QUEUE_STORAGE_KEY,
  clearOfflineQueueDrainBackoff,
  drainOfflineQueue,
  enqueueOfflineMutation,
  offlineQueueDepth,
  readOfflineQueue,
  wipeOfflineQueue,
} from "@/lib/offline-queue";

const storageState = vi.hoisted(() => ({ broken: false }));

// Breaking window.localStorage itself crashes the jsdom worker at teardown;
// null storage is simulated at the module boundary instead.
vi.mock("@/lib/safe-storage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/safe-storage")>();
  return {
    safeStorage: (kind: "local" | "session"): Storage | null =>
      kind === "local" && storageState.broken ? null : actual.safeStorage(kind),
  };
});

const SCOPES = { actorScope: "7", farmScope: "3" };
const HOUR = 60 * 60 * 1000;

function storage(): Storage {
  return window.localStorage;
}

function enqueue(path = "/api/tasks/12/complete", body = '{"reason":"r"}'): boolean {
  return enqueueOfflineMutation(path, { method: "POST", body }, SCOPES);
}

beforeEach(() => {
  storage().removeItem(OFFLINE_QUEUE_STORAGE_KEY);
  clearOfflineQueueDrainBackoff();
  storageState.broken = false;
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-06-01T06:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
  clearOfflineQueueDrainBackoff();
  storageState.broken = false;
});

describe("record TTL boundary", () => {
  it("drops a record at exactly 72 h and keeps it one millisecond earlier", () => {
    expect(enqueue()).toBe(true);
    vi.setSystemTime(new Date(Date.now() + 72 * HOUR - 1));
    expect(offlineQueueDepth()).toBe(1);
    vi.setSystemTime(new Date(Date.now() + 1));
    expect(offlineQueueDepth()).toBe(0);
    expect(readOfflineQueue(storage())).toEqual([]);
  });
});

describe("byte-cap boundary", () => {
  it("keeps a queue whose serialized size is exactly 256 KiB", () => {
    expect(enqueue("/api/tasks/1/skip", "")).toBe(true);
    // Each body character adds exactly one serialized byte: pad once to the
    // cap (a grow-by-one loop is quadratic over 256 KiB and OOMs the jsdom
    // worker). A `>=` trim drops the record, `>` (the shipped rule) keeps it.
    const start = storage().getItem(OFFLINE_QUEUE_STORAGE_KEY)!.length;
    const pad = "r".repeat(256 * 1024 - start);
    storage().removeItem(OFFLINE_QUEUE_STORAGE_KEY);
    expect(enqueue("/api/tasks/1/skip", pad)).toBe(true);
    const size = storage().getItem(OFFLINE_QUEUE_STORAGE_KEY)!.length;
    expect(size).toBe(256 * 1024);
    expect(offlineQueueDepth()).toBe(1);
    // And one byte more cannot fit even after trimming: the write is refused
    // and the at-cap record survives (fail-closed, never an empty overwrite).
    expect(
      enqueueOfflineMutation(
        "/api/tasks/1/skip",
        { method: "POST", body: pad + "r" },
        SCOPES,
      ),
    ).toBe(false);
    expect(offlineQueueDepth()).toBe(1);
  });
});

describe("empty-string body round-trip", () => {
  it("replays an empty-string body verbatim, not as absent", async () => {
    expect(enqueue("/api/tasks/1/skip", "")).toBe(true);
    const bodies: unknown[] = [];
    const outcome = await drainOfflineQueue(SCOPES, async (_path, init) => {
      bodies.push(init.body);
    });
    expect(outcome).toEqual({ replayed: 1, remaining: 0, rejected: 0 });
    expect(bodies).toEqual([""]);
  });
});

describe("null storage", () => {
  it("drain counts are exactly zero when storage is inaccessible", async () => {
    storageState.broken = true;
    const outcome = await drainOfflineQueue(SCOPES, async () => undefined);
    expect(outcome).toEqual({ replayed: 0, remaining: 0, rejected: 0 });
  });
});

describe("429 Retry-After backoff gate", () => {
  function queueOne() {
    expect(enqueue()).toBe(true);
  }

  it("gates the next drain while the hint is outstanding", async () => {
    queueOne();
    const outcome = await drainOfflineQueue(SCOPES, async () => {
      throw Object.assign(new Error("throttled"), { status: 429, retryAfterSeconds: 30 });
    });
    expect(outcome).toEqual({ replayed: 0, remaining: 1, rejected: 0 });

    vi.setSystemTime(new Date(Date.now() + 10_000));
    // A succeeding fetch keeps gated and replayed drains distinguishable:
    // a gate-skipping mutant replays (count 1), the real gate does not fetch.
    const calls: number[] = [];
    const gated = await drainOfflineQueue(SCOPES, async () => {
      calls.push(1);
    });
    expect(gated).toEqual({ replayed: 0, remaining: 1, rejected: 0 });
    expect(calls).toHaveLength(0);
  });

  it("lets a drain through exactly when the hint expires", async () => {
    queueOne();
    await drainOfflineQueue(SCOPES, async () => {
      throw Object.assign(new Error("throttled"), { status: 429, retryAfterSeconds: 2 });
    });
    vi.setSystemTime(new Date(Date.now() + 2_000));
    const outcome = await drainOfflineQueue(SCOPES, async () => undefined);
    expect(outcome).toEqual({ replayed: 1, remaining: 0, rejected: 0 });
  });

  it("clearOfflineQueueDrainBackoff re-arms the very next drain at clock zero", async () => {
    // Anchor the fake clock at the epoch: a clear that leaves a 1 ms gate
    // behind (0 -> 1 mutant) would still block a drain run at t = 0.
    vi.setSystemTime(new Date(0));
    queueOne();
    await drainOfflineQueue(SCOPES, async () => {
      throw Object.assign(new Error("throttled"), { status: 429, retryAfterSeconds: 1 });
    });
    clearOfflineQueueDrainBackoff();
    const calls: number[] = [];
    const outcome = await drainOfflineQueue(SCOPES, async () => {
      calls.push(1);
    });
    expect(outcome).toEqual({ replayed: 1, remaining: 0, rejected: 0 });
    expect(calls).toHaveLength(1);
  });

  it("a Retry-After of exactly 1 second still gates", async () => {
    queueOne();
    await drainOfflineQueue(SCOPES, async () => {
      throw Object.assign(new Error("throttled"), { status: 429, retryAfterSeconds: 1 });
    });
    const immediateCalls: number[] = [];
    const immediate = await drainOfflineQueue(SCOPES, async () => {
      immediateCalls.push(1);
    });
    expect(immediate).toEqual({ replayed: 0, remaining: 1, rejected: 0 });
    expect(immediateCalls).toHaveLength(0);
    vi.setSystemTime(new Date(Date.now() + 999));
    const justBeforeCalls: number[] = [];
    const justBefore = await drainOfflineQueue(SCOPES, async () => {
      justBeforeCalls.push(1);
    });
    expect(justBefore).toEqual({ replayed: 0, remaining: 1, rejected: 0 });
    expect(justBeforeCalls).toHaveLength(0);
    vi.setSystemTime(new Date(Date.now() + 1));
    const atExpiry = await drainOfflineQueue(SCOPES, async () => undefined);
    expect(atExpiry).toEqual({ replayed: 1, remaining: 0, rejected: 0 });
  });

  it("scales the hint by a full 1000 ms per second", async () => {
    queueOne();
    await drainOfflineQueue(SCOPES, async () => {
      throw Object.assign(new Error("throttled"), { status: 429, retryAfterSeconds: 8 });
    });
    vi.setSystemTime(new Date(Date.now() + 4_000));
    const halfwayCalls: number[] = [];
    const halfway = await drainOfflineQueue(SCOPES, async () => {
      halfwayCalls.push(1);
    });
    expect(halfway).toEqual({ replayed: 0, remaining: 1, rejected: 0 });
    expect(halfwayCalls).toHaveLength(0);
    vi.setSystemTime(new Date(Date.now() + 4_000));
    const atExpiry = await drainOfflineQueue(SCOPES, async () => undefined);
    expect(atExpiry).toEqual({ replayed: 1, remaining: 0, rejected: 0 });
  });
});

describe("wipe", () => {
  it("clears the queue", () => {
    expect(enqueue()).toBe(true);
    wipeOfflineQueue();
    expect(offlineQueueDepth()).toBe(0);
  });
});
