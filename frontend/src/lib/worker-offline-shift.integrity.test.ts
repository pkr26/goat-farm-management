import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { readOfflineShift, saveOfflineShift, type OfflineShift } from "@/lib/worker-offline-shift";

const device = vi.hoisted(() => ({ snapshot: null as unknown }));
vi.mock("@/lib/worker-outbox", () => ({
  writeWorkerSnapshot: vi.fn(async (_key: string, value: unknown, assertCurrent?: () => void) => {
    assertCurrent?.();
    device.snapshot = structuredClone(value);
  }),
  readWorkerSnapshot: vi.fn(async () => device.snapshot),
  deleteWorkerSnapshot: vi.fn(async () => { device.snapshot = null; }),
}));

const START = Date.UTC(2026, 9, 7, 12);
const LIFETIME = 43_200_000;
const MARKER_KEY = "herdly:offline-shift:v1";
const input = {
  actorScope: "7", farmScope: "3", workerName: "Worker", farmName: "Farm",
  tasks: [{ id: 1, title: "Clean pen", dueDate: "2026-10-07", canComplete: true, canSkip: false }],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(Date, "now").mockReturnValue(START);
  sessionStorage.clear();
  device.snapshot = null;
  setAccessToken("worker-token", 7);
  setCurrentFarmId("3");
});

describe("offline shift persisted-data boundaries", () => {
  it("allows the last millisecond of a twelve-hour shift and expires exactly at its deadline", async () => {
    await saveOfflineShift(input);
    expect(device.snapshot).toMatchObject({ version: 1, verifiedAt: START, expiresAt: START + LIFETIME });
    vi.mocked(Date.now).mockReturnValue(START + LIFETIME - 1);
    expect(await readOfflineShift()).toMatchObject(input);
    vi.mocked(Date.now).mockReturnValue(START + LIFETIME);
    expect(await readOfflineShift()).toBeNull();
  });

  it("accepts five minutes of clock rollback and rejects one millisecond more", async () => {
    await saveOfflineShift(input);
    vi.mocked(Date.now).mockReturnValue(START - 300_000);
    expect(await readOfflineShift()).toMatchObject(input);
    vi.mocked(Date.now).mockReturnValue(START - 300_001);
    expect(await readOfflineShift()).toBeNull();
  });

  it("rejects a snapshot that extends a shift beyond twelve hours even when its marker agrees", async () => {
    await saveOfflineShift(input);
    const snapshot = device.snapshot as OfflineShift;
    snapshot.expiresAt += 1;
    sessionStorage.setItem(MARKER_KEY, JSON.stringify({
      actorScope: "7", farmScope: "3", expiresAt: snapshot.expiresAt,
    }));
    expect(await readOfflineShift()).toBeNull();
  });

  it.each([
    ["version", 0], ["version", 2], ["actorScope", "8"], ["farmScope", "4"],
    ["expiresAt", START + LIFETIME - 1], ["verifiedAt", Number.NaN],
    ["workerName", null], ["farmName", 123], ["tasks", {}],
  ])("rejects corrupt persisted %s (%s)", async (key, value) => {
    await saveOfflineShift(input);
    (device.snapshot as unknown as Record<string, unknown>)[key] = value;
    expect(await readOfflineShift()).toBeNull();
  });

  it.each([
    ["id", 0], ["id", -1], ["id", 1.5], ["id", Number.MAX_SAFE_INTEGER + 1],
    ["title", null], ["dueDate", 123], ["canComplete", 1], ["canSkip", "false"],
  ])("rejects a persisted duty with invalid %s (%s)", async (key, value) => {
    await saveOfflineShift(input);
    const task = (device.snapshot as OfflineShift).tasks[0];
    (task as unknown as Record<string, unknown>)[key] = value;
    expect(await readOfflineShift()).toBeNull();
  });

  it("fails closed for null entries in a corrupted persisted duty list", async () => {
    await saveOfflineShift(input);
    (device.snapshot as unknown as { tasks: unknown[] }).tasks = [null];
    await expect(readOfflineShift()).resolves.toBeNull();
  });
});


it.each(["actorScope", "farmScope"] as const)(
  "rejects a corrupt %s even when both device stores contain the same shift", async (scopeField) => {
    await saveOfflineShift(input);
    const snapshot = device.snapshot as OfflineShift;
    snapshot[scopeField] = "0";
    sessionStorage.setItem(MARKER_KEY, JSON.stringify({
      actorScope: snapshot.actorScope, farmScope: snapshot.farmScope, expiresAt: snapshot.expiresAt,
    }));
    await expect(readOfflineShift()).resolves.toBeNull();
  },
);
