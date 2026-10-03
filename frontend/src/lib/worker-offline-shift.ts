import { deleteWorkerSnapshot, readWorkerSnapshot, writeWorkerSnapshot } from "@/lib/worker-outbox";
import { safeStorage } from "@/lib/safe-storage";
import { currentRequestScope } from "@/lib/api-client";

const ACTIVE_SHIFT_KEY = "herdly:offline-shift:v1";
const SHIFT_LIFETIME_MS = 12 * 60 * 60 * 1000;
export type OfflineDuty = {
  id: number; title: string; dueDate: string; canComplete: boolean; canSkip: boolean;
};
export type OfflineShift = {
  version: 1; actorScope: string; farmScope: string; workerName: string;
  farmName: string; verifiedAt: number; expiresAt: number; tasks: OfflineDuty[];
};
type ShiftMarker = { actorScope: string; farmScope: string; expiresAt: number };
const keyFor = (scope: ShiftMarker) => `${scope.actorScope}:${scope.farmScope}`;

function readMarker(): ShiftMarker | null {
  try {
    const raw = safeStorage("session")?.getItem(ACTIVE_SHIFT_KEY);
    if (!raw) return null;
    const item = JSON.parse(raw) as ShiftMarker;
    if (!/^[1-9]\d*$/.test(item.actorScope) || !/^[1-9]\d*$/.test(item.farmScope) ||
      !Number.isFinite(item.expiresAt) || Date.now() >= item.expiresAt) return null;
    return item;
  } catch { return null; }
}

export async function saveOfflineShift(input: Omit<OfflineShift, "version" | "verifiedAt" | "expiresAt">): Promise<void> {
  const scope = currentRequestScope();
  const assertCurrent = () => {
    const live = currentRequestScope();
    if (!scope || scope.actorScope !== input.actorScope || scope.farmScope !== input.farmScope ||
      live?.sessionEpoch !== scope.sessionEpoch || live.farmEpoch !== scope.farmEpoch) {
      throw new DOMException("The shift changed.", "AbortError");
    }
  };
  assertCurrent();
  const storage = safeStorage("session");
  if (storage === null) throw new Error("Session storage unavailable");
  const previous = readMarker();
  // Snapshot refreshes do not extend a shift indefinitely. A new worker or
  // farm starts a new bounded snapshot; reconnecting requires real login.
  const expiresAt = previous && keyFor(previous) === keyFor({ ...input, expiresAt: 0 })
    ? previous.expiresAt : Date.now() + SHIFT_LIFETIME_MS;
  const snapshot: OfflineShift = { ...input, version: 1, verifiedAt: Date.now(), expiresAt };
  await writeWorkerSnapshot(keyFor(snapshot), snapshot, assertCurrent);
  assertCurrent();
  storage.setItem(ACTIVE_SHIFT_KEY, JSON.stringify({ actorScope: input.actorScope, farmScope: input.farmScope, expiresAt }));
}

export async function readOfflineShift(): Promise<OfflineShift | null> {
  const marker = readMarker();
  if (marker === null) return null;
  const snapshot = await readWorkerSnapshot<OfflineShift>(keyFor(marker));
  const current = readMarker();
  if (!current || keyFor(current) !== keyFor(marker) || current.expiresAt !== marker.expiresAt) return null;
  if (snapshot === null || snapshot.version !== 1 || snapshot.actorScope !== marker.actorScope ||
    snapshot.farmScope !== marker.farmScope || snapshot.expiresAt !== marker.expiresAt ||
    !Number.isFinite(snapshot.verifiedAt) || Date.now() >= snapshot.expiresAt ||
    Date.now() < snapshot.verifiedAt - 5 * 60 * 1000 ||
    snapshot.expiresAt - snapshot.verifiedAt > SHIFT_LIFETIME_MS || !Array.isArray(snapshot.tasks) ||
    typeof snapshot.workerName !== "string" || typeof snapshot.farmName !== "string" ||
    snapshot.tasks.some((task) => !Number.isSafeInteger(task.id) || task.id <= 0 ||
      typeof task.title !== "string" || typeof task.dueDate !== "string" ||
      typeof task.canComplete !== "boolean" || typeof task.canSkip !== "boolean")) return null;
  return snapshot;
}

export async function endOfflineShift(): Promise<void> {
  const marker = readMarker();
  // A failure in one device store must not prevent revoking the other.
  let markerError: unknown;
  try { safeStorage("session")?.removeItem(ACTIVE_SHIFT_KEY); }
  catch (error) { markerError = error; }
  if (marker !== null) await deleteWorkerSnapshot(keyFor(marker));
  if (markerError) throw markerError;
}
