/** Transactional, actor/farm scoped device writes. No token or cookie is
 * persisted. Pending and rejected operations never expire or evict each
 * other. An acknowledgement is returned only after IndexedDB commits. */
import { apiFetch, currentRequestScope, type RequestScope } from "@/lib/api-client";
import { randomIdempotencyKey } from "@/lib/idempotent-request";
import { OFFLINE_QUEUE_STORAGE_KEY, isOfflineQueueableMutation, type QueueScopes } from "@/lib/offline-queue";
import { safeStorage } from "@/lib/safe-storage";

export const WORKER_OUTBOX_DB = "herdly-worker-outbox-v2";
const MAX_PENDING = 100;
const MAX_BYTES = 2 * 1024 * 1024;
const REVIEW_AFTER_MS = 72 * 60 * 60 * 1000;
const LEASE_MS = 120_000;
const CHANGE_EVENT = "herdly:worker-outbox";

export type WorkerOperation = QueueScopes & {
  id: string;
  path: string;
  method: "POST";
  body: string | null;
  idempotencyKey: string;
  queuedAt: number;
  /** Transaction order, independent of clock skew or equal millisecond stamps. */
  sequence?: number;
  /** Imported IDs need delivery tombstones if their accepted receipts are cleared. */
  legacyImported?: boolean;
  state: "pending" | "review" | "sent";
  reason?: string;
  status?: number;
  settledAt?: number;
};
export class OutboxStorageError extends Error {
  constructor() { super("This device could not save the duty. Keep this screen open and try again."); }
}
export class OutboxReviewRequiredError extends Error {
  constructor() { super("This duty already has a saved action needing review."); }
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") { reject(new OutboxStorageError()); return; }
    const request = indexedDB.open(WORKER_OUTBOX_DB, 1);
    let rejected = false;
    request.onupgradeneeded = () => {
      request.result.createObjectStore("operations", { keyPath: "id" });
      request.result.createObjectStore("meta", { keyPath: "key" });
    };
    request.onsuccess = () => { if (rejected) request.result.close(); else resolve(request.result); };
    request.onerror = () => reject(new OutboxStorageError());
    request.onblocked = () => { rejected = true; reject(new OutboxStorageError()); };
  });
}

/** All read/modify/write operations use one serializable transaction across
 * tabs. No await is allowed inside the callback: IDB auto-commits idle work. */
async function transaction<T>(
  change: (tx: IDBTransaction, finish: (value: T) => void) => void,
): Promise<T> {
  const db = await openDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      let tx: IDBTransaction;
      try { tx = db.transaction(["operations", "meta"], "readwrite", { durability: "strict" }); }
      catch { tx = db.transaction(["operations", "meta"], "readwrite"); }
      let value: T;
      let finished = false;
      tx.oncomplete = () => finished ? resolve(value) : reject(new OutboxStorageError());
      tx.onabort = tx.onerror = () => reject(new OutboxStorageError());
      try { change(tx, (result) => { value = result; finished = true; }); }
      catch { tx.abort(); }
    });
  } finally { db.close(); }
}

function changed(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CHANGE_EVENT));
}

function legacyOperations(): WorkerOperation[] {
  const storage = safeStorage("local");
  if (storage === null) return [];
  let raw: string | null;
  try { raw = storage.getItem(OFFLINE_QUEUE_STORAGE_KEY); }
  catch { throw new OutboxStorageError(); }
  if (raw === null) return [];
  // Keep the original store intact even if migration cannot understand it.
  // It can be exported and repaired; never replace it with an empty queue.
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error();
    return parsed.map((entry: unknown) => {
      if (entry === null || typeof entry !== "object") throw new Error();
      const item = entry as Record<string, unknown>;
      if (item.v !== 1 || typeof item.id !== "string" || !item.id ||
        typeof item.path !== "string" || typeof item.method !== "string" ||
        typeof item.actorScope !== "string" || typeof item.farmScope !== "string" ||
        typeof item.queuedAt !== "number" || !Number.isFinite(item.queuedAt) ||
        (item.body !== null && typeof item.body !== "string") ||
        typeof item.headers !== "object" || item.headers === null) throw new Error();
      const key = Object.entries(item.headers).find(([name]) => name.toLowerCase() === "idempotency-key")?.[1];
      const allowed = isOfflineQueueableMutation(item.path, item.method);
      return {
        id: item.id, path: item.path, method: "POST", body: item.body as string | null,
        actorScope: item.actorScope, farmScope: item.farmScope, queuedAt: item.queuedAt,
        idempotencyKey: typeof key === "string" && key ? key : item.id,
        state: allowed ? "pending" : "review", legacyImported: true,
        ...(!allowed ? { reason: "unsupported" } : {}),
      };
    });
  } catch { throw new OutboxStorageError(); }
}

const deliveredLegacyKey = (id: string) => `delivered-legacy:${id}`;

async function migrateLegacy(): Promise<void> {
  const legacy = legacyOperations();
  if (legacy.length === 0) return;
  await transaction<void>((tx, finish) => {
    const store = tx.objectStore("operations");
    const meta = tx.objectStore("meta");
    const sequenceRequest = meta.get("sequence");
    sequenceRequest.onsuccess = () => {
      let sequence = Number(sequenceRequest.result?.value ?? 0);
      if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > Number.MAX_SAFE_INTEGER - legacy.length) {
        tx.abort(); return;
      }
      for (const operation of legacy) {
        const request = store.get(operation.id);
        request.onsuccess = () => {
          const existing = request.result as WorkerOperation | undefined;
          if (existing !== undefined) {
            // Older imported rows predate this marker. Mark them before a
            // cleanup can remove their only durable acknowledgement.
            if (!existing.legacyImported) store.put({ ...existing, legacyImported: true });
            return;
          }
          const delivered = meta.get(deliveredLegacyKey(operation.id));
          delivered.onsuccess = () => {
            if (delivered.result?.value === true) return;
            store.add({ ...operation, sequence: ++sequence });
            meta.put({ key: "sequence", value: sequence });
          };
        };
      }
    };
    finish(undefined);
  });
  // Receipts remain in the new store, so an old tab re-importing this legacy
  // array cannot resurrect a delivered operation. Retain the source for
  // recovery; no cross-tab localStorage read/modify/write is performed.
}

export async function readWorkerOutbox(scopes: QueueScopes): Promise<WorkerOperation[]> {
  await migrateLegacy();
  return transaction<WorkerOperation[]>((tx, finish) => {
    const request = tx.objectStore("operations").getAll();
    request.onsuccess = () => finish((request.result as WorkerOperation[])
      .filter((item) => item.actorScope === scopes.actorScope && item.farmScope === scopes.farmScope)
      .sort((a, b) => (a.sequence !== undefined && b.sequence !== undefined
        ? a.sequence - b.sequence : a.queuedAt - b.queuedAt) || a.id.localeCompare(b.id)));
  });
}

export async function persistWorkerOperation(
  path: string, body: string | undefined, scopes: QueueScopes, key = randomIdempotencyKey(),
): Promise<WorkerOperation> {
  if (!isOfflineQueueableMutation(path, "POST") || !scopes.actorScope || !scopes.farmScope) throw new OutboxStorageError();
  await migrateLegacy();
  const operation: WorkerOperation = {
    ...scopes, id: randomIdempotencyKey(), path, method: "POST", body: body ?? null,
    idempotencyKey: key, queuedAt: Date.now(), state: "pending",
  };
  const accepted = await transaction<WorkerOperation | null>((tx, finish) => {
    const store = tx.objectStore("operations");
    const request = store.getAll();
    request.onsuccess = () => {
      const records = request.result as WorkerOperation[];
      const existing = records.find((item) => item.state !== "sent" && item.actorScope === scopes.actorScope &&
        item.farmScope === scopes.farmScope && item.path === path && item.body === operation.body);
      if (existing) { finish(existing.state === "review" ? null : existing); return; }
      const active = records.filter((item) => item.state !== "sent");
      if (active.length >= MAX_PENDING ||
        new TextEncoder().encode(JSON.stringify([...active, operation])).length > MAX_BYTES) {
        tx.abort(); return;
      }
      const sequenceRequest = tx.objectStore("meta").get("sequence");
      sequenceRequest.onsuccess = () => {
        const sequence = Number(sequenceRequest.result?.value ?? 0) + 1;
        if (!Number.isSafeInteger(sequence) || sequence <= 0) { tx.abort(); return; }
        const saved = { ...operation, sequence };
        store.add(saved);
        tx.objectStore("meta").put({ key: "sequence", value: saved.sequence });
        finish(saved);
      };
    };
  });
  if (accepted === null) throw new OutboxReviewRequiredError();
  changed();
  return accepted;
}

export async function settleWorkerOperation(id: string, state: "sent" | "review", reason?: string, status?: number): Promise<void> {
  await transaction<void>((tx, finish) => {
    const store = tx.objectStore("operations");
    const request = store.get(id);
    request.onsuccess = () => {
      const operation = request.result as WorkerOperation | undefined;
      // A late rejection cannot overwrite an acknowledgement from another
      // tab. A sent receipt is the strongest evidence of delivery.
      if (operation !== undefined && operation.state !== "sent") store.put({
        ...operation, state, reason, status, settledAt: Date.now(),
      });
      finish(undefined);
    };
  });
  changed();
}

function assertCleanupScope(scope: RequestScope): void {
  const live = currentRequestScope();
  if (live === null || live.actorScope !== scope.actorScope || live.farmScope !== scope.farmScope ||
    live.sessionEpoch !== scope.sessionEpoch || live.farmEpoch !== scope.farmEpoch) {
    throw new DOMException("The worker or farm changed before receipts were cleared.", "AbortError");
  }
}

/** Explicitly remove only this authenticated worker's accepted receipts.
 * Pending/review writes and every other scope are preserved. Imported IDs
 * remain delivered even if an old tab re-imports the original legacy queue. */
export async function clearAcceptedWorkerReceipts(scope: RequestScope, acceptedIds: readonly string[]): Promise<number> {
  assertCleanupScope(scope);
  const confirmedIds = new Set(acceptedIds);
  await migrateLegacy();
  assertCleanupScope(scope);
  const cleared = await transaction<number>((tx, finish) => {
    assertCleanupScope(scope);
    const store = tx.objectStore("operations");
    const request = store.getAll();
    request.onsuccess = () => {
      try { assertCleanupScope(scope); }
      catch { tx.abort(); return; }
      const accepted = (request.result as WorkerOperation[]).filter((operation) =>
        confirmedIds.has(operation.id) && operation.state === "sent" &&
        operation.actorScope === scope.actorScope && operation.farmScope === scope.farmScope);
      for (const operation of accepted) {
        if (operation.legacyImported) {
          tx.objectStore("meta").put({ key: deliveredLegacyKey(operation.id), value: true });
        }
        store.delete(operation.id);
      }
      finish(accepted.length);
    };
  });
  changed();
  return cleared;
}

async function lease(scopes: QueueScopes, owner: string, release = false): Promise<boolean> {
  return transaction<boolean>((tx, finish) => {
    const store = tx.objectStore("meta");
    const key = `drain:${scopes.actorScope}:${scopes.farmScope}`;
    const request = store.get(key);
    request.onsuccess = () => {
      const existing = request.result as { owner: string; expires: number } | undefined;
      if (existing && existing.owner !== owner && existing.expires > Date.now()) { finish(false); return; }
      if (release) store.delete(key);
      else store.put({ key, owner, expires: Date.now() + LEASE_MS });
      finish(true);
    };
  });
}

export type OutboxDrain = { replayed: number; rejected: number; remaining: number };
const sameScope = (a: QueueScopes | null, b: QueueScopes) => a?.actorScope === b.actorScope && a.farmScope === b.farmScope;
let nextAttempt = 0;
export function clearWorkerOutboxBackoff(): void { nextAttempt = 0; }

/** Device snapshots share the same transactional store, but are never
 * interpreted as server authentication or sent as request credentials. */
export async function writeWorkerSnapshot<T>(key: string, value: T, assertCurrent?: () => void): Promise<void> {
  await transaction<void>((tx, finish) => {
    assertCurrent?.();
    tx.objectStore("meta").put({ key: `snapshot:${key}`, value }); finish(undefined);
  });
}

export async function readWorkerSnapshot<T>(key: string): Promise<T | null> {
  return transaction<T | null>((tx, finish) => {
    const request = tx.objectStore("meta").get(`snapshot:${key}`);
    request.onsuccess = () => finish(request.result?.value ?? null);
  });
}

export async function deleteWorkerSnapshot(key: string): Promise<void> {
  await transaction<void>((tx, finish) => {
    tx.objectStore("meta").delete(`snapshot:${key}`); finish(undefined);
  });
}

export async function drainWorkerOutbox(
  scopes: QueueScopes,
  getScope: () => QueueScopes | null = currentRequestScope,
  send: (operation: WorkerOperation, scope: RequestScope | null) => Promise<unknown> = (operation, scope) => {
    if (scope === null) throw new DOMException("No current worker session.", "AbortError");
    return apiFetch(operation.path, {
      method: operation.method, body: operation.body ?? undefined,
      headers: { "Idempotency-Key": operation.idempotencyKey },
    }, scope);
  },
): Promise<OutboxDrain> {
  const owner = randomIdempotencyKey();
  const boundScope = currentRequestScope();
  let replayed = 0; let rejected = 0;
  const initial = await readWorkerOutbox(scopes);
  if (Date.now() < nextAttempt || !sameScope(getScope(), scopes) || !(await lease(scopes, owner))) {
    return { replayed, rejected, remaining: initial.filter((item) => item.state !== "sent").length };
  }
  try {
    for (const operation of initial) {
      if (operation.state !== "pending") continue;
      if (!sameScope(getScope(), scopes) || !(await lease(scopes, owner))) break;
      if (!isOfflineQueueableMutation(operation.path, operation.method) || Date.now() - operation.queuedAt >= REVIEW_AFTER_MS) {
        await settleWorkerOperation(operation.id, "review", "stale-or-unsupported"); rejected++; continue;
      }
      try {
        await send(operation, boundScope);
        await settleWorkerOperation(operation.id, "sent"); replayed++;
      } catch (error) {
        const status = (error as { status?: number } | null)?.status;
        if (status === 401 || status === 408 || status === 429) {
          const retry = (error as { retryAfterSeconds?: number }).retryAfterSeconds;
          if (status === 429) nextAttempt = Date.now() + Math.max(30, retry ?? 30) * 1000;
          break;
        }
        if (typeof status === "number" && status >= 400 && status < 500) {
          // Conflict is evidence requiring a decision, never proof that the
          // intended action happened. Keep its original body/key and receipt.
          await settleWorkerOperation(operation.id, "review", status === 409 ? "conflict" : "rejected", status);
          rejected++; continue;
        }
        break;
      }
    }
  } finally { await lease(scopes, owner, true); }
  const records = await readWorkerOutbox(scopes);
  return { replayed, rejected, remaining: records.filter((item) => item.state !== "sent").length };
}

export function startWorkerOutbox(
  getScope: () => QueueScopes | null,
  onChange: (records: WorkerOperation[]) => void,
  onError: () => void,
): () => void {
  let active = true;
  const run = async () => {
    const scope = getScope();
    if (scope === null) return;
    try {
      if (navigator.onLine) await drainWorkerOutbox(scope, getScope);
      const records = await readWorkerOutbox(scope);
      if (active && sameScope(getScope(), scope)) onChange(records);
    } catch { if (active && sameScope(getScope(), scope)) onError(); }
  };
  const handle = () => { if (active) void run(); };
  window.addEventListener("online", handle);
  window.addEventListener("focus", handle);
  window.addEventListener(CHANGE_EVENT, handle);
  // Poll reads committed IDB state from other tabs; local events refresh
  // promptly without storing any identity in a broadcast channel.
  const timer = window.setInterval(handle, 30_000);
  handle();
  return () => {
    active = false; window.clearInterval(timer);
    window.removeEventListener("online", handle);
    window.removeEventListener("focus", handle);
    window.removeEventListener(CHANGE_EVENT, handle);
  };
}
