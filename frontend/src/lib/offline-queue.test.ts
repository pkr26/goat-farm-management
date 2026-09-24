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

  it("bounds stored bytes at 256 KiB by trimming oldest-first", () => {
    // ~100 KB bodies: two fit (~206 KB serialized), the third pushes past
    // the cap and the OLDEST is dropped to make room (FIFO trim, not a
    // rejected write).
    const big = "r".repeat(100 * 1024);
    for (const path of ["/api/tasks/1/skip", "/api/tasks/2/skip", "/api/tasks/3/skip"]) {
      expect(
        enqueueOfflineMutation(path, { method: "POST", body: `{"reason":"${big}"}` }, SCOPES),
      ).toBe(true);
    }
    const records = readOfflineQueue(storage());
    expect(records.length).toBe(2);
    expect(records[0]?.path).toBe("/api/tasks/2/skip");
    expect(records[1]?.path).toBe("/api/tasks/3/skip");
    expect(JSON.stringify(records).length).toBeLessThanOrEqual(256 * 1024);
  });

  it("a single record larger than the whole byte budget never wedges the store", () => {
    const huge = "r".repeat(300 * 1024);
    expect(
      enqueueOfflineMutation("/api/tasks/1/skip", { method: "POST", body: `{"reason":"${huge}"}` }, SCOPES),
    ).toBe(false);
    expect(offlineQueueDepth()).toBe(0);
    // The store stays writable afterwards.
    expect(
      enqueueOfflineMutation("/api/tasks/2/skip", { method: "POST", body: '{"reason":"ok"}' }, SCOPES),
    ).toBe(true);
    expect(offlineQueueDepth()).toBe(1);
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

  it("never queues a definitive 4xx, even while the browser reports offline", () => {
    // Connectivity can drop right after the server's rejection arrived;
    // queueing it would tell the worker "Saved" for a write the server
    // already refused. Only genuine outages (transport, 5xx) queue.
    const onLine = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    try {
      expect(isOfflineQueueableFailure({ status: 422, detail: "rejected" })).toBe(false);
      expect(isOfflineQueueableFailure({ status: 409 })).toBe(false);
      expect(isOfflineQueueableFailure({ status: 503 })).toBe(true);
      expect(isOfflineQueueableFailure(new TypeError("fetch failed"))).toBe(true);
    } finally {
      onLine.mockRestore();
    }
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

  it("keeps a completion enqueued while a slow replay was in flight", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    const fetchImpl = vi.fn().mockImplementation(async () => {
      // The worker taps another completion through on a flaky connection
      // while this replay is on the wire; the drain's write-back merges
      // against live storage instead of clobbering it with a stale snapshot.
      enqueueOfflineMutation("/api/tasks/2/complete", { method: "POST" }, SCOPES);
      return {};
    });
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(outcome).toEqual({ replayed: 1, remaining: 1 });
    expect(readOfflineQueue(storage()).map((r) => r.path)).toEqual([
      "/api/tasks/2/complete",
    ]);
  });

  it("keeps FIFO order between a backed-off record and a mid-drain enqueue", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    const error = Object.assign(new Error("boom"), { status: 503 });
    const fetchImpl = vi.fn().mockImplementation(async () => {
      enqueueOfflineMutation("/api/tasks/2/complete", { method: "POST" }, SCOPES);
      throw error;
    });
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(outcome).toEqual({ replayed: 0, remaining: 2 });
    expect(readOfflineQueue(storage()).map((r) => r.path)).toEqual([
      "/api/tasks/1/complete",
      "/api/tasks/2/complete",
    ]);
  });

  it("a concurrent drain invocation neither replays nor clobbers records", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    let release!: (value: unknown) => void;
    const parked = new Promise((resolve) => {
      release = resolve;
    });
    const slowFetch = vi.fn().mockImplementation(() => parked);
    const first = drainOfflineQueue(SCOPES, slowFetch);
    // The shell's immediate drain racing the interval trigger: it returns
    // without re-firing the write under the same Idempotency-Key and
    // without touching storage.
    const secondFetch = vi.fn().mockResolvedValue({});
    const second = await drainOfflineQueue(SCOPES, secondFetch);
    expect(second).toEqual({ replayed: 0, remaining: 1 });
    expect(secondFetch).not.toHaveBeenCalled();

    release({});
    const firstOutcome = await first;
    expect(firstOutcome).toEqual({ replayed: 1, remaining: 0 });
    expect(slowFetch).toHaveBeenCalledTimes(1);
    expect(offlineQueueDepth()).toBe(0);
  });

  it("the next drain replays a mid-drain enqueue in FIFO order", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    const first = vi.fn().mockImplementation(async () => {
      enqueueOfflineMutation("/api/tasks/3/complete", { method: "POST" }, SCOPES);
      return {};
    });
    await drainOfflineQueue(SCOPES, first);

    const second = vi.fn().mockResolvedValue({});
    const outcome = await drainOfflineQueue(SCOPES, second);
    expect(outcome).toEqual({ replayed: 1, remaining: 0 });
    expect(second).toHaveBeenCalledTimes(1);
    expect(second.mock.calls[0]?.[0]).toBe("/api/tasks/3/complete");
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

/** Mutation-hardening (2026-09-23 campaign): per-field fail-closed reads (a
 *  record failing ANY single wellFormed arm is dropped — not just a wrong
 *  version), the exact 256 KiB byte ceiling, the rewrite that cleans a
 *  partially corrupt store, and enqueue's false under blocked storage. */
describe("wellFormed per-field fail-closed reads", () => {
  const BASE = {
    v: 1,
    id: "id-1",
    path: "/api/tasks/1/complete",
    method: "POST",
    body: null,
    headers: { "Idempotency-Key": "k" },
    queuedAt: 1_000,
    actorScope: "7",
    farmScope: "3",
  };

  function withOverride(override: Record<string, unknown>) {
    storage().setItem(
      OFFLINE_QUEUE_STORAGE_KEY,
      JSON.stringify([{ ...BASE, ...override }]),
    );
    return readOfflineQueue(storage());
  }

  it("drops a record failing any single field check", () => {
    const cases: Array<[string, Record<string, unknown>]> = [
      ["non-object record", { }],
      ["wrong version", { v: 2 }],
      ["empty id", { id: "" }],
      ["non-string id", { id: 7 }],
      ["non-api path", { path: "https://evil.test/api" }],
      ["non-string method", { method: 5 }],
      ["non-string body", { body: 42 }],
      ["null headers", { headers: null }],
      ["non-numeric queuedAt", { queuedAt: "soon" }],
      ["non-finite queuedAt", { queuedAt: Number.NaN }],
      ["non-string actorScope", { actorScope: 7 }],
      ["non-string farmScope", { farmScope: 3 }],
    ];
    for (const [name, override] of cases) {
      // "non-object record" replaces the whole entry.
      if (name === "non-object record") {
        storage().setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify(["just a string"]));
        expect(readOfflineQueue(storage()), name).toEqual([]);
        continue;
      }
      expect(withOverride(override), name).toEqual([]);
    }
  });

  it("accepts the untouched record and every optional-body spelling", () => {
    expect(withOverride({})).toHaveLength(1);
    expect(withOverride({ body: "{}" })).toHaveLength(1);
  });

  it("rewrites the store so a partially corrupt queue cannot reappear", () => {
    const good = { ...BASE };
    const bad = { ...BASE, id: "" };
    storage().setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify([good, bad]));
    const records = readOfflineQueue(storage());
    expect(records).toHaveLength(1);
    // The corrupt twin must be gone from STORAGE, not just filtered in
    // memory.
    expect(JSON.parse(storage().getItem(OFFLINE_QUEUE_STORAGE_KEY) ?? "[]")).toHaveLength(1);
  });

  it("survives at exactly 256 KiB and wipes one byte over", () => {
    const limit = 256 * 1024;
    const one = JSON.stringify([{ ...BASE }]);
    // The wrapper adds 2 chars for the array; pad the body to hit the limit
    // exactly (plain spaces need no JSON escaping, so length grows 1:1).
    const pad = limit - one.length;
    const exact = [{ ...BASE, body: "x".repeat(Math.max(0, pad)) }];
    // Recompute: body replaced null (4 chars) with pad+2 quotes; adjust.
    let json = JSON.stringify(exact);
    const body = "x".repeat(Math.max(0, pad + (limit - json.length)));
    const fixed = [{ ...BASE, body }];
    expect(JSON.stringify(fixed).length).toBe(limit);
    storage().setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify(fixed));
    expect(readOfflineQueue(storage())).toHaveLength(1);

    const over = [{ ...BASE, body: "x".repeat(body.length + 1) }];
    expect(JSON.stringify(over).length).toBe(limit + 1);
    storage().setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify(over));
    expect(readOfflineQueue(storage())).toEqual([]);
    expect(storage().getItem(OFFLINE_QUEUE_STORAGE_KEY)).toBeNull();
  });

  it("enqueue returns false when storage is blocked entirely", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    try {
      expect(
        enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES),
      ).toBe(false);
    } finally {
      if (original) Object.defineProperty(window, "localStorage", original);
    }
  });
});

/** Same hardening round: failure-classification and drain boundaries at
 *  their exact edges, multi-record queues through every skip path, and the
 *  write-side byte ceiling at exactly 256 KiB. */
describe("failure classification and drain boundaries", () => {
  it("classifies statuses at their exact edges", () => {
    const offline = (status: number) =>
      Object.assign(new Error("x"), { status, name: "TypeError" });
    // 4xx are answers: unqueueable at both edges.
    expect(isOfflineQueueableFailure(offline(400))).toBe(false);
    expect(isOfflineQueueableFailure(offline(499))).toBe(false);
    // 399 and 500 are NOT 4xx: the transport/name arm decides.
    expect(isOfflineQueueableFailure(offline(399))).toBe(true);
    expect(isOfflineQueueableFailure(offline(500))).toBe(true);
  });

  it("drain drops 400/499 but keeps-and-stops on 399/500", async () => {
    for (const [status, dropped] of [
      [400, true],
      [499, true],
      [399, false],
      [500, false],
    ] as const) {
      storage().removeItem(OFFLINE_QUEUE_STORAGE_KEY);
      enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
      const outcome = await drainOfflineQueue(
        SCOPES,
        vi.fn().mockRejectedValue(Object.assign(new Error("x"), { status })),
      );
      expect(outcome.remaining, String(status)).toBe(dropped ? 0 : 1);
      expect(offlineQueueDepth(), String(status)).toBe(dropped ? 0 : 1);
    }
  });

  it("a foreign record is skipped, never a wall: later own records still replay", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, OTHER);
    enqueueOfflineMutation("/api/tasks/2/skip", { method: "POST" }, SCOPES);
    const fetchImpl = vi.fn().mockResolvedValue({});
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(outcome).toEqual({ replayed: 1, remaining: 1 });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/tasks/2/skip");
  });

  it("a 409 or a definitive 4xx resolves that record and the queue keeps draining", async () => {
    enqueueOfflineMutation("/api/tasks/1/complete", { method: "POST" }, SCOPES);
    enqueueOfflineMutation("/api/tasks/2/skip", { method: "POST" }, SCOPES);
    enqueueOfflineMutation("/api/tasks/3/verify", { method: "POST" }, SCOPES);
    const error409 = Object.assign(new Error("conflict"), { status: 409 });
    const error404 = Object.assign(new Error("gone"), { status: 404 });
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(error409)
      .mockRejectedValueOnce(error404)
      .mockResolvedValue({});
    const outcome = await drainOfflineQueue(SCOPES, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    // The 404 resolves without replay credit: 2 replays, nothing wedged.
    expect(outcome).toEqual({ replayed: 2, remaining: 0 });
  });

  it("enqueue accepts a first record that lands exactly at the byte ceiling", () => {
    const limit = 256 * 1024;
    const probe = { method: "POST", body: null as string | null, headers: {} };
    // Serialized wrapper for a single record with a null body; then pad the
    // body so the total lands exactly on the ceiling.
    enqueueOfflineMutation("/api/tasks/1/complete", probe, SCOPES);
    const base = storage().getItem(OFFLINE_QUEUE_STORAGE_KEY) ?? "[]";
    const pad = limit - base.length;
    storage().removeItem(OFFLINE_QUEUE_STORAGE_KEY);
    // Padding the body replaces the 4-char `null` literal with 2 quotes
    // plus the pad, hence the +2 correction.
    const ok = enqueueOfflineMutation(
      "/api/tasks/1/complete",
      { method: "POST", body: "x".repeat(Math.max(0, pad + 2)), headers: {} },
      SCOPES,
    );
    expect(ok).toBe(true);
    expect((storage().getItem(OFFLINE_QUEUE_STORAGE_KEY) ?? "").length).toBe(limit);
    expect(offlineQueueDepth()).toBe(1);
  });

  it("a null entry inside the stored array is dropped without throwing", () => {
    storage().setItem(OFFLINE_QUEUE_STORAGE_KEY, "[null]");
    expect(readOfflineQueue(storage())).toEqual([]);
  });
});
