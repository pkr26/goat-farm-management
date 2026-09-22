/**
 * Offline mutation queue (ITEM 2 Phase 2, 2026-09-21 playbook): enqueue
 * bounds and fail-closed reads, actor/farm scoping, FIFO replay semantics
 * (success/409/4xx drop, 5xx backoff), and the session wipe.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  OFFLINE_QUEUE_STORAGE_KEY,
  drainOfflineQueue,
  enqueueOfflineMutation,
  isOfflineQueueableFailure,
  isOfflineQueueableMutation,
  offlineQueueDepth,
  readOfflineQueue,
  wipeOfflineQueue,
} from "@/lib/offline-queue";

const SCOPES = { actorScope: "7", farmScope: "3" };
const OTHER = { actorScope: "8", farmScope: "3" };

function storage(): Storage {
  return window.localStorage;
}

function lastRecord() {
  return readOfflineQueue(storage())[0];
}

beforeEach(() => {
  storage().removeItem(OFFLINE_QUEUE_STORAGE_KEY);
});

describe("enqueue + read", () => {
  it("queues a well-formed record with its idempotency header intact", () => {
    const ok = enqueueOfflineMutation(
      "/api/tasks/12/complete",
      { method: "POST", body: undefined, headers: { "Idempotency-Key": "key-1" } },
      SCOPES,
    );
    expect(ok).toBe(true);
    expect(offlineQueueDepth()).toBe(1);
    const record = lastRecord();
    expect(record.path).toBe("/api/tasks/12/complete");
    expect(record.method).toBe("POST");
    expect(record.headers["Idempotency-Key"]).toBe("key-1");
    expect(record.actorScope).toBe("7");
    expect(record.farmScope).toBe("3");
    expect(record.v).toBe(1);
  });

  it("bounds the record count at 100", () => {
    for (let i = 0; i < 100; i += 1) {
      expect(
        enqueueOfflineMutation("/api/tasks/1/skip", { method: "POST", body: '{"reason":"r"}' }, SCOPES),
      ).toBe(true);
    }
    expect(offlineQueueDepth()).toBe(100);
    expect(
      enqueueOfflineMutation("/api/tasks/2/skip", { method: "POST", body: '{"reason":"r"}' }, SCOPES),
    ).toBe(false);
    expect(offlineQueueDepth()).toBe(100);
  });

  it("fails closed on a malformed store: dropped wholesale, not misparsed", () => {
    storage().setItem(OFFLINE_QUEUE_STORAGE_KEY, '{"not":"an array"}');
    expect(readOfflineQueue(storage())).toEqual([]);
    expect(offlineQueueDepth()).toBe(0);

    storage().setItem(
      OFFLINE_QUEUE_STORAGE_KEY,
      JSON.stringify([
        { v: 1, id: "a", path: "/api/tasks/1/complete", method: "POST", body: null, headers: {}, queuedAt: 1, actorScope: "7", farmScope: "3" },
        { v: 99, evil: true },
      ]),
    );
    const records = readOfflineQueue(storage());
    expect(records).toHaveLength(1);
    expect(records[0]?.id).toBe("a");
  });
});

describe("isOfflineQueueableMutation", () => {
  it("accepts exactly the duty completion/skip routes", () => {
    expect(isOfflineQueueableMutation("/api/tasks/12/complete", "POST")).toBe(true);
    expect(isOfflineQueueableMutation("/api/tasks/12/skip", "POST")).toBe(true);
    expect(isOfflineQueueableMutation("/api/tasks/12/complete", "GET")).toBe(false);
    expect(isOfflineQueueableMutation("/api/finance/new", "POST")).toBe(false);
    expect(isOfflineQueueableMutation("/api/tasks/12/complete/extra", "POST")).toBe(false);
  });
});

describe("isOfflineQueueableFailure", () => {
  it("treats transport failures and the offline flag as queueable, HTTP errors not", () => {
    expect(isOfflineQueueableFailure(new TypeError("fetch failed"))).toBe(true);
    expect(isOfflineQueueableFailure({ name: "AbortError" })).toBe(true);
    expect(isOfflineQueueableFailure({ status: 403, detail: "no" })).toBe(false);
  });
});

describe("drainOfflineQueue", () => {
  it("replays FIFO and drops each success", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    enqueueOfflineMutation("/api/tasks/2/skip", { method: "POST", body: '{"reason":"r"}' }, SCOPES);
    const fetchImpl = vi.fn().mockResolvedValue({});
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(outcome).toEqual({ replayed: 2, remaining: 0 });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/tasks/1/complete");
    expect(fetchImpl.mock.calls[1]?.[0]).toBe("/api/tasks/2/skip");
    expect(offlineQueueDepth()).toBe(0);
  });

  it("drops a 409 — the server already applied this exact keyed write", async () => {
    enqueueOfflineMutation(
      "/api/tasks/1/complete",
      { method: "POST", headers: { "Idempotency-Key": "k" } },
      SCOPES,
    );
    const error = Object.assign(new Error("conflict"), { status: 409 });
    const outcome = await drainOfflineQueue(SCOPES, vi.fn().mockRejectedValue(error));
    expect(outcome.replayed).toBe(1);
    expect(offlineQueueDepth()).toBe(0);
  });

  it("drops definitive 4xx rejections so the queue cannot wedge", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    const error = Object.assign(new Error("gone"), { status: 404 });
    const outcome = await drainOfflineQueue(SCOPES, vi.fn().mockRejectedValue(error));
    expect(outcome.remaining).toBe(0);
    expect(offlineQueueDepth()).toBe(0);
  });

  it("keeps the record and stops on a 5xx (FIFO order preserved)", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    enqueueOfflineMutation("/api/tasks/2/complete", { method: "POST" }, SCOPES);
    const error = Object.assign(new Error("boom"), { status: 503 });
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(error)
      .mockResolvedValue({});
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ replayed: 0, remaining: 2 });
    expect(offlineQueueDepth()).toBe(2);
  });

  it("never touches another actor's or farm's records", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    enqueueOfflineMutation("/api/tasks/2/complete", { method: "POST" }, OTHER);
    const fetchImpl = vi.fn().mockResolvedValue({});
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/tasks/1/complete");
    expect(outcome.remaining).toBe(1);
    // The other worker's record survives for their session.
    expect(readOfflineQueue()[0]?.actorScope).toBe("8");
  });
});

describe("wipeOfflineQueue", () => {
  it("clears every record (End-shift handover)", () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    expect(offlineQueueDepth()).toBe(1);
    wipeOfflineQueue();
    expect(offlineQueueDepth()).toBe(0);
  });
});
