import { IDBFactory, IDBObjectStore } from "fake-indexeddb";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { currentRequestScope, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { OFFLINE_QUEUE_STORAGE_KEY } from "@/lib/offline-queue";
import {
  clearAcceptedWorkerReceipts, clearWorkerOutboxBackoff, drainWorkerOutbox, persistWorkerOperation,
  readWorkerOutbox, settleWorkerOperation, WORKER_OUTBOX_DB, type WorkerOperation,
} from "@/lib/worker-outbox";

const scopes = { actorScope: "7", farmScope: "42" };

async function deviceDatabase(): Promise<IDBDatabase> {
  await readWorkerOutbox(scopes);
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(WORKER_OUTBOX_DB, 1);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function seedOperations(records: WorkerOperation[]): Promise<void> {
  const db = await deviceDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(["operations", "meta"], "readwrite");
      for (const record of records) tx.objectStore("operations").put(record);
      tx.objectStore("meta").put({ key: "sequence", value: records.length });
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error);
    });
  } finally { db.close(); }
}

async function metadata(): Promise<Array<{ key: string; value: unknown }>> {
  const db = await deviceDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction("meta", "readonly").objectStore("meta").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally { db.close(); }
}

function receipt(id: number, state: WorkerOperation["state"] = "sent", scope = scopes): WorkerOperation {
  return { ...scope, id: `receipt-${id}`, path: `/api/tasks/${id}/complete`, method: "POST", body: null,
    idempotencyKey: `accepted-key-${id}`, queuedAt: 1, sequence: id, state, settledAt: state === "pending" ? undefined : 2 };
}
beforeEach(() => {
  vi.useRealTimers();
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("structuredClone", (value: unknown) => JSON.parse(JSON.stringify(value)));
  localStorage.clear(); clearWorkerOutboxBackoff();
  setAccessToken("worker-token", 7); setCurrentFarmId("42");
});

describe("durable worker actions", () => {
  it("acknowledges only committed writes and rejects unavailable device storage", async () => {
    vi.stubGlobal("indexedDB", undefined);
    await expect(persistWorkerOperation("/api/tasks/1/complete", undefined, scopes)).rejects.toThrow();
    vi.stubGlobal("indexedDB", new IDBFactory());
    expect(await readWorkerOutbox(scopes)).toEqual([]);
    const operation = await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes, "original-key");
    expect((await readWorkerOutbox(scopes))[0]).toMatchObject({ id: operation.id, idempotencyKey: "original-key", state: "pending" });
  });

  it("serializes simultaneous tab writes without overwriting either action", async () => {
    await Promise.all([
      persistWorkerOperation("/api/tasks/1/complete", undefined, scopes),
      persistWorkerOperation("/api/tasks/2/skip", '{"reason":"field note"}', scopes),
    ]);
    expect((await readWorkerOutbox(scopes)).map((item) => item.path).sort()).toEqual(["/api/tasks/1/complete", "/api/tasks/2/skip"]);
  });
  it("two tabs retrying the same logical duty share its original key", async () => {
    const [first, second] = await Promise.all([
      persistWorkerOperation("/api/tasks/1/complete", undefined, scopes, "first-tab"),
      persistWorkerOperation("/api/tasks/1/complete", undefined, scopes, "second-tab"),
    ]);
    expect(first.id).toBe(second.id);
    expect(first.idempotencyKey).toBe(second.idempotencyKey);
    expect(await readWorkerOutbox(scopes)).toHaveLength(1);
    await settleWorkerOperation(first.id, "review", "conflict", 409);
    await expect(persistWorkerOperation("/api/tasks/1/complete", undefined, scopes)).rejects.toThrow("needing review");
    expect(await readWorkerOutbox(scopes)).toHaveLength(1);
  });

  it("keeps the existing queue when a new write exceeds the capacity", async () => {
    const original = await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes);
    await expect(persistWorkerOperation("/api/tasks/2/skip", "x".repeat(2 * 1024 * 1024), scopes)).rejects.toThrow();
    expect((await readWorkerOutbox(scopes)).map((item) => item.id)).toEqual([original.id]);
  });

  it("accepts a new duty with more than 2 MiB of sent history without deleting any receipt", async () => {
    const history = Array.from({ length: 12_000 }, (_, index) => receipt(index + 1));
    expect(new TextEncoder().encode(JSON.stringify(history)).length).toBeGreaterThan(2 * 1024 * 1024);
    await seedOperations([...history, receipt(20_000, "pending"), receipt(20_001, "review")]);
    const saved = await persistWorkerOperation("/api/tasks/20002/complete", undefined, scopes);
    const records = await readWorkerOutbox(scopes);
    expect(records).toHaveLength(12_003);
    expect(records.filter((item) => item.state === "sent")).toHaveLength(12_000);
    expect(records.find((item) => item.id === saved.id)?.state).toBe("pending");
    expect(records.find((item) => item.id === "receipt-20001")?.state).toBe("review");
  });

  it("clears only confirmed accepted receipts in the current actor/farm and keeps all active work", async () => {
    const records = [receipt(1), receipt(2, "pending"), receipt(3, "review"),
      receipt(4, "sent", { ...scopes, actorScope: "8" }), receipt(5, "sent", { ...scopes, farmScope: "43" }), receipt(6)];
    await seedOperations(records);
    const scope = currentRequestScope()!;
    expect(await clearAcceptedWorkerReceipts(scope, records.slice(0, 5).map((record) => record.id))).toBe(1);
    expect((await readWorkerOutbox(scopes)).map((record) => [record.id, record.state])).toEqual([
      ["receipt-2", "pending"], ["receipt-3", "review"], ["receipt-6", "sent"],
    ]);
    expect(await readWorkerOutbox({ ...scopes, actorScope: "8" })).toHaveLength(1);
    expect(await readWorkerOutbox({ ...scopes, farmScope: "43" })).toHaveLength(1);
    expect((await metadata()).filter((item) => item.key.startsWith("delivered-legacy:"))).toEqual([]);
  });

  it("does not acknowledge cleanup or lose records when its transaction aborts", async () => {
    await seedOperations([receipt(1), receipt(2, "pending")]);
    const changed = vi.fn();
    window.addEventListener("herdly:worker-outbox", changed);
    const originalDelete = IDBObjectStore.prototype.delete;
    const deletion = vi.spyOn(IDBObjectStore.prototype, "delete").mockImplementation(function (this: IDBObjectStore, key) {
      const request = originalDelete.call(this, key);
      this.transaction.abort();
      return request;
    });
    try {
      await expect(clearAcceptedWorkerReceipts(currentRequestScope()!, ["receipt-1"])).rejects.toThrow();
      expect(changed).not.toHaveBeenCalled();
    } finally { deletion.mockRestore(); window.removeEventListener("herdly:worker-outbox", changed); }
    expect((await readWorkerOutbox(scopes)).map((record) => [record.id, record.state])).toEqual([
      ["receipt-1", "sent"], ["receipt-2", "pending"],
    ]);
    expect(await clearAcceptedWorkerReceipts(currentRequestScope()!, ["receipt-1"])).toBe(1);
    expect((await readWorkerOutbox(scopes))[0].id).toBe("receipt-2");
  });

  it("refuses stale cleanup after another identity or same-farm leave-and-return", async () => {
    await seedOperations([receipt(1)]);
    const identityScope = currentRequestScope()!;
    const clearing = clearAcceptedWorkerReceipts(identityScope, ["receipt-1"]);
    setAccessToken("different-worker", 8);
    await expect(clearing).rejects.toMatchObject({ name: "AbortError" });
    setAccessToken("worker-again", 7);
    const farmScope = currentRequestScope()!;
    setCurrentFarmId("43"); setCurrentFarmId("42");
    await expect(clearAcceptedWorkerReceipts(farmScope, ["receipt-1"])).rejects.toMatchObject({ name: "AbortError" });
    expect((await readWorkerOutbox(scopes))[0].state).toBe("sent");
  });

  it("marks pre-existing imported acknowledgements before cleanup and cannot resurrect their legacy source", async () => {
    const existing = { ...receipt(1), id: "old-imported-id", idempotencyKey: "legacy-key" };
    await seedOperations([existing]);
    // This row was imported before legacyImported existed; its original
    // localStorage source still survives, as it does on upgraded tablets.
    localStorage.setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify([{
      ...scopes, id: existing.id, path: existing.path, method: "POST", body: null,
      headers: { "Idempotency-Key": existing.idempotencyKey }, queuedAt: 1, v: 1,
    }]));
    expect(await clearAcceptedWorkerReceipts(currentRequestScope()!, [existing.id])).toBe(1);
    expect(localStorage.getItem(OFFLINE_QUEUE_STORAGE_KEY)).toContain(existing.id);
    expect(await readWorkerOutbox(scopes)).toEqual([]);
    const send = vi.fn();
    await drainWorkerOutbox(scopes, () => scopes, send);
    expect(send).not.toHaveBeenCalled();
    expect(await metadata()).toContainEqual({ key: `delivered-legacy:${existing.id}`, value: true });
    expect(await readWorkerOutbox(scopes)).toEqual([]);
  });

  it("does not send another worker's or farm's writes", async () => {
    await persistWorkerOperation("/api/tasks/1/complete", undefined, { actorScope: "8", farmScope: "42" });
    await persistWorkerOperation("/api/tasks/2/complete", undefined, { actorScope: "7", farmScope: "43" });
    const send = vi.fn();
    await drainWorkerOutbox(scopes, () => scopes, send);
    expect(send).not.toHaveBeenCalled();
    expect(await readWorkerOutbox(scopes)).toEqual([]);
    expect(await readWorkerOutbox({ actorScope: "8", farmScope: "42" })).toHaveLength(1);
  });

  it("retains a conflict and validation rejection as review receipts", async () => {
    await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes, "key-1");
    await persistWorkerOperation("/api/tasks/2/skip", '{"reason":"note"}', scopes, "key-2");
    const send = vi.fn().mockRejectedValueOnce({ status: 409 }).mockRejectedValueOnce({ status: 422 });
    expect(await drainWorkerOutbox(scopes, () => scopes, send)).toEqual({ replayed: 0, rejected: 2, remaining: 2 });
    expect(await readWorkerOutbox(scopes)).toEqual(expect.arrayContaining([
      expect.objectContaining({ state: "review", reason: "conflict", idempotencyKey: "key-1" }),
      expect.objectContaining({ state: "review", status: 422, body: '{"reason":"note"}' }),
    ]));
    await drainWorkerOutbox(scopes, () => scopes, send);
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("replays an ambiguous network failure with the original key and keeps its acknowledgement", async () => {
    await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes, "same-key");
    const send = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce({ status: "DONE" });
    expect((await drainWorkerOutbox(scopes, () => scopes, send)).remaining).toBe(1);
    expect((await drainWorkerOutbox(scopes, () => scopes, send)).replayed).toBe(1);
    expect(send.mock.calls.map(([item]) => item.idempotencyKey)).toEqual(["same-key", "same-key"]);
    expect((await readWorkerOutbox(scopes))[0].state).toBe("sent");
    await drainWorkerOutbox(scopes, () => scopes, send);
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("stops after handover during a slow request without losing either operation", async () => {
    await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes);
    await persistWorkerOperation("/api/tasks/2/complete", undefined, scopes);
    let live: typeof scopes | null = scopes;
    const send = vi.fn(async () => { live = null; throw new TypeError("connection lost during signout"); });
    await drainWorkerOutbox(scopes, () => live, send);
    expect(send).toHaveBeenCalledTimes(1);
    setAccessToken(null); setCurrentFarmId(null);
    expect((await readWorkerOutbox(scopes)).filter((item) => item.state === "pending")).toHaveLength(2);
  });

  it("uses a cross-tab lease to avoid concurrent replay of the same queue", async () => {
    await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes);
    let complete!: () => void;
    const send = vi.fn(() => new Promise<void>((resolve) => { complete = resolve; }));
    const first = drainWorkerOutbox(scopes, () => scopes, send);
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    await drainWorkerOutbox(scopes, () => scopes, send);
    expect(send).toHaveBeenCalledTimes(1);
    complete(); await first;
  });

  it("moves aged operations to review instead of expiring them", async () => {
    const past = Date.now() - 73 * 60 * 60 * 1000;
    vi.spyOn(Date, "now").mockReturnValueOnce(past);
    await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes);
    const send = vi.fn();
    await drainWorkerOutbox(scopes, () => scopes, send);
    expect(send).not.toHaveBeenCalled();
    expect((await readWorkerOutbox(scopes))[0]).toMatchObject({ state: "review", queuedAt: past });
  });

  it("imports legacy writes without pruning or persisting credentials, and cannot resurrect a sent receipt", async () => {
    localStorage.setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify([{
      ...scopes, id: "legacy-id", path: "/api/tasks/1/skip", method: "POST", body: '{"reason":"old note"}',
      headers: { "Idempotency-Key": "legacy-key", Authorization: "secret" }, queuedAt: 1, v: 1,
    }]));
    expect((await readWorkerOutbox(scopes))[0]).toMatchObject({ id: "legacy-id", idempotencyKey: "legacy-key" });
    expect(JSON.stringify(await readWorkerOutbox(scopes))).not.toContain("secret");
    await settleWorkerOperation("legacy-id", "sent");
    expect((await readWorkerOutbox(scopes))[0].state).toBe("sent");
    expect(localStorage.getItem(OFFLINE_QUEUE_STORAGE_KEY)).toContain("old note");
  });

  it("fences same-actor leave-and-return before fetch executes", async () => {
    await persistWorkerOperation("/api/tasks/1/complete", undefined, scopes);
    const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    let probes = 0;
    await drainWorkerOutbox(scopes, () => {
      if (++probes === 2) { setCurrentFarmId("43"); setCurrentFarmId("42"); }
      return currentRequestScope();
    });
    expect(fetch).not.toHaveBeenCalled();
    expect((await readWorkerOutbox(scopes))[0].state).toBe("pending");
  });
});
