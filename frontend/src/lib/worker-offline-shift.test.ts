import { IDBFactory } from "fake-indexeddb";
import { beforeEach, expect, it, vi } from "vitest";
import { endOfflineShift, readOfflineShift, saveOfflineShift } from "@/lib/worker-offline-shift";
import { persistWorkerOperation, readWorkerOutbox } from "@/lib/worker-outbox";
import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";

const input = {
  actorScope: "7", farmScope: "3", workerName: "Worker", farmName: "Farm",
  tasks: [{ id: 1, title: "Clean pen", dueDate: "2026-10-03", canComplete: true, canSkip: true }],
};
beforeEach(() => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("structuredClone", (value: unknown) => JSON.parse(JSON.stringify(value)));
  sessionStorage.clear(); localStorage.clear();
  setAccessToken("current-worker", 7); setCurrentFarmId("3");
});

it("recovers a bounded current-shift board after loss of in-memory authentication", async () => {
  await saveOfflineShift(input);
  setAccessToken(null); setCurrentFarmId(null);
  expect(await readOfflineShift()).toMatchObject(input);
  expect(JSON.stringify(await readOfflineShift())).not.toContain("token");
});

it("ends offline access without deleting pending field actions", async () => {
  await saveOfflineShift(input);
  await persistWorkerOperation("/api/tasks/1/complete", undefined, input);
  await endOfflineShift();
  expect(await readOfflineShift()).toBeNull();
  expect(await readWorkerOutbox(input)).toHaveLength(1);
});

it("expires the active shift without expiring pending writes", async () => {
  await saveOfflineShift(input);
  await persistWorkerOperation("/api/tasks/1/complete", undefined, input);
  const now = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(now + 13 * 60 * 60 * 1000);
  expect(await readOfflineShift()).toBeNull();
  expect(await readWorkerOutbox(input)).toHaveLength(1);
});

it("snapshot refreshes within an ongoing shift keep its original expiry", async () => {
  await saveOfflineShift(input);
  const first = await readOfflineShift();
  const now = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(now + 2 * 60 * 60 * 1000);
  await saveOfflineShift({ ...input, tasks: [] });
  expect((await readOfflineShift())?.expiresAt).toBe(first?.expiresAt);
});

it("fails closed when the per-tab active marker is unavailable", async () => {
  await saveOfflineShift(input);
  sessionStorage.clear();
  expect(await readOfflineShift()).toBeNull();
});
it("an asynchronous snapshot cannot recreate offline access after handover", async () => {
  const saving = saveOfflineShift(input);
  setAccessToken(null); setCurrentFarmId(null);
  await endOfflineShift();
  await expect(saving).rejects.toThrow();
  expect(await readOfflineShift()).toBeNull();
});
