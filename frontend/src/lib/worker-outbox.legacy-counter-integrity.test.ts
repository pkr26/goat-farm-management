import { IDBFactory } from "fake-indexeddb";
import { expect, it, vi } from "vitest";

import { randomIdempotencyKey } from "./idempotent-request";
import { OFFLINE_QUEUE_STORAGE_KEY } from "./offline-queue";
import { persistWorkerOperation, readWorkerOutbox, WORKER_OUTBOX_DB } from "./worker-outbox";

it.each([
  ["missing", undefined],
  ["stale zero", 0],
  ["decreased positive", 2],
  ["healthy", 4],
] as const)("preserves legacy source and genuine durable order with %s sequence metadata", async (boundary, sequence) => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  localStorage.clear();
  const scope = { actorScope: "7", farmScope: "3" };
  try {
    const older = [];
    for (const task of [41, 42, 43, 44]) {
      older.push(await persistWorkerOperation(`/api/tasks/${task}/complete`, undefined, scope));
    }
    const olderIds = older.map((operation) => operation.id);
    expect((await readWorkerOutbox(scope)).map((operation) => operation.id)).toEqual(olderIds);
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open(WORKER_OUTBOX_DB, 1);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const database = request.result;
        const transaction = database.transaction("meta", "readwrite");
        if (sequence === undefined) transaction.objectStore("meta").delete("sequence");
        else transaction.objectStore("meta").put({ key: "sequence", value: sequence });
        transaction.oncomplete = () => { database.close(); resolve(); };
        transaction.onabort = () => { database.close(); reject(transaction.error); };
      };
    });
    // The legacy v1 storage key is a public import boundary. Supply its
    // documented compatible wire shape, with actual secure public IDs and
    // native time. Existing IndexedDB operations are untouched producer output.
    const importedId = randomIdempotencyKey();
    const raw = JSON.stringify([{
      v: 1, id: importedId, ...scope, path: "/api/tasks/45/complete", method: "POST",
      body: null, queuedAt: Date.now(), headers: { "Idempotency-Key": randomIdempotencyKey() },
    }]);
    localStorage.setItem(OFFLINE_QUEUE_STORAGE_KEY, raw);
    if (boundary === "healthy") {
      const records = await readWorkerOutbox(scope);
      expect(records.map((operation) => operation.id)).toEqual([...olderIds, importedId]);
      expect(localStorage.getItem(OFFLINE_QUEUE_STORAGE_KEY)).toBeNull();
    } else {
      await expect(readWorkerOutbox(scope)).rejects.toThrow();
      expect(localStorage.getItem(OFFLINE_QUEUE_STORAGE_KEY)).toBe(raw);
      // Inspect the original durable queue after moving the retained source
      // aside, just as a tablet can export/review the rejected import.
      localStorage.removeItem(OFFLINE_QUEUE_STORAGE_KEY);
      expect((await readWorkerOutbox(scope)).map((operation) => operation.id)).toEqual(olderIds);
    }
  } finally {
    localStorage.clear();
    vi.unstubAllGlobals();
  }
});
