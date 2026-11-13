import { IDBFactory } from "fake-indexeddb";
import { expect, it, vi } from "vitest";

import { persistWorkerOperation, readWorkerOutbox, settleWorkerOperation, WORKER_OUTBOX_DB } from "./worker-outbox";

it.each([
  ["missing", undefined, "pending"],
  ["stale zero", 0, "pending"],
  ["decreased positive", 2, "pending"],
  ["stale last ordinal", 3, "pending"],
  ["sent receipt ordinal", 2, "sent"],
  ["healthy", 4, "pending"],
] as const)("preserves genuine durable order with %s sequence metadata", async (_boundary, sequence, state) => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  localStorage.clear();
  const scope = { actorScope: "7", farmScope: "3" };
  try {
    const older = [];
    for (const task of [41, 42, 43, 44]) {
      older.push(await persistWorkerOperation(`/api/tasks/${task}/complete`, undefined, scope));
    }
    if (state === "sent") {
      for (const operation of older) await settleWorkerOperation(operation.id, "sent");
    }
    const olderIds = older.map((operation) => operation.id);
    expect((await readWorkerOutbox(scope)).map((operation) => operation.id)).toEqual(olderIds);
    // The operation records are genuine public-producer output. Simulate a
    // tablet losing only one public persistence entry, without seeding or
    // rewriting any operation, UUID, ordinal, timestamp, or internal map.
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
    // Recovery may reject the new write safely or reconstruct a safe next
    // ordinal. Either choice must retain the actual previously saved order.
    const [next] = await Promise.allSettled([
      persistWorkerOperation("/api/tasks/45/complete", undefined, scope),
    ]);
    const visible = await readWorkerOutbox(scope);
    expect(visible.slice(0, older.length).map((operation) => operation.id)).toEqual(olderIds);
    expect(visible.slice(0, older.length).every((operation) => operation.state === state)).toBe(true);
    expect(next.status).toBe(_boundary === "healthy" ? "fulfilled" : "rejected");
    if (next.status === "fulfilled") {
      expect(visible).toHaveLength(older.length + 1);
      expect(visible.at(-1)?.id).toBe(next.value.id);
    } else {
      expect(visible).toHaveLength(older.length);
    }
  } finally {
    localStorage.clear();
    vi.unstubAllGlobals();
  }
});
