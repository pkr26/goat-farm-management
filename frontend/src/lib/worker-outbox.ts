/** Transactional, actor/farm scoped device writes. No token or cookie is
 * persisted. Pending and rejected operations never expire or evict each
 * other. An acknowledgement is returned only after IndexedDB commits. */
import { apiFetch, currentRequestScope, type RequestScope } from "@/lib/api-client";
import { randomIdempotencyKey } from "@/lib/idempotent-request";
import { OFFLINE_QUEUE_STORAGE_KEY, isOfflineQueueableMutation, type QueueScopes } from "@/lib/offline-queue";
import { safeStorage } from "@/lib/safe-storage";

export const WORKER_OUTBOX_DB = "herdly-worker-outbox-v2";
export const LEGACY_QUARANTINE_META_PREFIX = "legacy-quarantine:";
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
  confirmedAt?: number;
  lastRetriedAt?: number;
};
export type LegacyQueueQuarantine = {
  raw: string;
  malformedRecords: number;
  quarantinedAt: number;
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

type LegacySnapshot = {
  raw: string;
  operations: WorkerOperation[];
  malformedRecords: number;
};

function parseLegacyOperation(entry: unknown): WorkerOperation | null {
  if (entry === null || typeof entry !== "object") return null;
  const item = entry as Record<string, unknown>;
  if (item.v !== 1 || typeof item.id !== "string" || !item.id ||
    typeof item.path !== "string" || typeof item.method !== "string" ||
    typeof item.actorScope !== "string" || typeof item.farmScope !== "string" ||
    typeof item.queuedAt !== "number" || !Number.isFinite(item.queuedAt) ||
    (item.body !== null && typeof item.body !== "string") ||
    typeof item.headers !== "object" || item.headers === null) return null;
  const key = Object.entries(item.headers).find(([name]) => name.toLowerCase() === "idempotency-key")?.[1];
  const allowed = isOfflineQueueableMutation(item.path, item.method);
  return {
    id: item.id, path: item.path, method: "POST", body: item.body as string | null,
    actorScope: item.actorScope, farmScope: item.farmScope, queuedAt: item.queuedAt,
    idempotencyKey: typeof key === "string" && key ? key : item.id,
    state: allowed ? "pending" : "review", legacyImported: true,
    ...(!allowed ? { reason: "unsupported" } : {}),
  };
}

function legacySnapshot(): LegacySnapshot | null {
  const storage = safeStorage("local");
  if (storage === null) return null;
  let raw: string | null;
  try { raw = storage.getItem(OFFLINE_QUEUE_STORAGE_KEY); }
  catch { return null; }
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return { raw, operations: [], malformedRecords: 1 };
    const operations: WorkerOperation[] = [];
    let malformedRecords = 0;
    for (const entry of parsed) {
      const operation = parseLegacyOperation(entry);
      if (operation === null) malformedRecords++;
      else operations.push(operation);
    }
    return { raw, operations, malformedRecords };
  } catch { return { raw, operations: [], malformedRecords: 1 }; }
}

function legacyArchiveKey(raw: string): string {
  // A deterministic two-lane checksum prevents repeated migrations from
  // creating unbounded archive copies. The raw value remains the evidence;
  // this checksum is only an IndexedDB key, not an integrity claim.
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < raw.length; index++) {
    const code = raw.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ (code + index), 0x85ebca6b);
  }
  return `${LEGACY_QUARANTINE_META_PREFIX}${raw.length}:${(first >>> 0).toString(16)}:${(second >>> 0).toString(16)}`;
}

const deliveredLegacyKey = (id: string) => `delivered-legacy:${id}`;

async function migrateLegacy(): Promise<void> {
  const snapshot = legacySnapshot();
  if (snapshot === null) return;
  const legacy = snapshot.operations;
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
      if (snapshot.malformedRecords > 0) {
        const quarantine: LegacyQueueQuarantine = {
          raw: snapshot.raw,
          malformedRecords: snapshot.malformedRecords,
          quarantinedAt: Date.now(),
        };
        meta.put({ key: legacyArchiveKey(snapshot.raw), value: quarantine });
      }
    };
    finish(undefined);
  });
  // Remove only the exact source that was committed. If another tab changed
  // it, that newer value remains for the next migration. Malformed source is
  // retained byte-for-byte in IndexedDB before this best-effort cleanup.
  try {
    const storage = safeStorage("local");
    if (storage?.getItem(OFFLINE_QUEUE_STORAGE_KEY) === snapshot.raw) {
      storage.removeItem(OFFLINE_QUEUE_STORAGE_KEY);
    }
  } catch {
    // Re-reading the source is harmless and idempotent; never make a healthy
    // IndexedDB outbox unavailable merely because Web Storage is blocked.
  }
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

/** Recoverable, byte-exact legacy sources containing malformed records. */
export async function readLegacyQueueQuarantine(): Promise<LegacyQueueQuarantine[]> {
  await migrateLegacy();
  return transaction<LegacyQueueQuarantine[]>((tx, finish) => {
    const request = tx.objectStore("meta").getAll();
    request.onsuccess = () => finish((request.result as Array<{ key: string; value?: unknown }>)
      .filter((item) => item.key.startsWith(LEGACY_QUARANTINE_META_PREFIX))
      .map((item) => item.value as LegacyQueueQuarantine));
  });
}

export async function persistWorkerOperation(
  path: string, body: string | undefined, scopes: QueueScopes, key = randomIdempotencyKey(),
  initialState: "pending" | "review" = "pending",
  initialReason?: string,
): Promise<WorkerOperation> {
  if (!isOfflineQueueableMutation(path, "POST") || !scopes.actorScope || !scopes.farmScope) throw new OutboxStorageError();
  await migrateLegacy();
  const operation: WorkerOperation = {
    ...scopes, id: randomIdempotencyKey(), path, method: "POST", body: body ?? null,
    idempotencyKey: key, queuedAt: Date.now(), state: initialState, reason: initialReason,
  };
  const accepted = await transaction<WorkerOperation | null>((tx, finish) => {
    const store = tx.objectStore("operations");
    const request = store.getAll();
    request.onsuccess = () => {
      const records = request.result as WorkerOperation[];
      const existing = records.find((item) => item.state !== "sent" && item.actorScope === scopes.actorScope &&
        item.farmScope === scopes.farmScope && item.path === path && item.body === operation.body);
      if (existing) { finish(existing.state === "review" ? null : existing); return; }
      const active = records.filter((item) => item.state !== "sent" &&
        item.actorScope === scopes.actorScope && item.farmScope === scopes.farmScope);
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

/** Save an action entered without a live server session as an untrusted
 * draft. It is never eligible for automatic replay until the original worker
 * signs in to the same farm and explicitly confirms it. */
export function persistOfflineWorkerDraft(
  path: string, body: string | undefined, scopes: QueueScopes,
): Promise<WorkerOperation> {
  return persistWorkerOperation(
    path,
    body,
    scopes,
    randomIdempotencyKey(),
    "review",
    "offline-untrusted",
  );
}

/** Promote only an offline-origin draft for the currently authenticated
 * actor/farm. Server rejections and stale/conflict receipts are never
 * approvable through this path. */
export async function confirmOfflineWorkerDraft(
  scope: RequestScope, id: string,
): Promise<boolean> {
  assertCleanupScope(scope);
  const confirmed = await transaction<boolean>((tx, finish) => {
    assertCleanupScope(scope);
    const store = tx.objectStore("operations");
    const request = store.get(id);
    request.onsuccess = () => {
      try { assertCleanupScope(scope); }
      catch { tx.abort(); return; }
      const operation = request.result as WorkerOperation | undefined;
      if (operation === undefined || operation.actorScope !== scope.actorScope ||
        operation.farmScope !== scope.farmScope || operation.state !== "review" ||
        operation.reason !== "offline-untrusted") { finish(false); return; }
      store.put({
        ...operation,
        state: "pending",
        reason: undefined,
        status: undefined,
        settledAt: undefined,
        confirmedAt: Date.now(),
      });
      finish(true);
    };
  });
  if (confirmed) changed();
  return confirmed;
}

/** Delete only an unauthenticated offline draft after the original worker
 * signs in to its actor/farm scope and rejects it. Server rejection receipts
 * remain immutable evidence and cannot be removed through this path. */
export async function discardOfflineWorkerDraft(
  scope: RequestScope, id: string,
): Promise<boolean> {
  assertCleanupScope(scope);
  const discarded = await transaction<boolean>((tx, finish) => {
    assertCleanupScope(scope);
    const store = tx.objectStore("operations");
    const request = store.get(id);
    request.onsuccess = () => {
      try { assertCleanupScope(scope); }
      catch { tx.abort(); return; }
      const operation = request.result as WorkerOperation | undefined;
      if (operation === undefined || operation.actorScope !== scope.actorScope ||
        operation.farmScope !== scope.farmScope || operation.state !== "review" ||
        operation.reason !== "offline-untrusted") { finish(false); return; }
      store.delete(id);
      finish(true);
    };
  });
  if (discarded) changed();
  return discarded;
}

/** Resolve a server/staleness review receipt for only the live actor/farm.
 * Retrying preserves its original request and idempotency key. Dismissal is
 * an explicit evidence deletion and is therefore confirmed by the caller. */
export async function resolveWorkerReviewReceipt(
  scope: RequestScope, id: string, resolution: "retry" | "dismiss",
): Promise<boolean> {
  assertCleanupScope(scope);
  const resolved = await transaction<boolean>((tx, finish) => {
    assertCleanupScope(scope);
    const store = tx.objectStore("operations");
    const request = store.get(id);
    request.onsuccess = () => {
      try { assertCleanupScope(scope); }
      catch { tx.abort(); return; }
      const operation = request.result as WorkerOperation | undefined;
      if (operation === undefined || operation.actorScope !== scope.actorScope ||
        operation.farmScope !== scope.farmScope || operation.state !== "review" ||
        operation.reason === "offline-untrusted" ||
        (resolution === "retry" && !isOfflineQueueableMutation(operation.path, operation.method))) {
        finish(false); return;
      }
      if (resolution === "dismiss") store.delete(id);
      else store.put({
        ...operation,
        state: "pending",
        reason: undefined,
        status: undefined,
        settledAt: undefined,
        lastRetriedAt: Date.now(),
      });
      finish(true);
    };
  });
  if (resolved) changed();
  return resolved;
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
      const lastAuthorizedAt = operation.lastRetriedAt ?? operation.confirmedAt ?? operation.queuedAt;
      if (!isOfflineQueueableMutation(operation.path, operation.method) || Date.now() - lastAuthorizedAt >= REVIEW_AFTER_MS) {
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
  canReplay: () => boolean = () => true,
): () => void {
  let active = true;
  const run = async () => {
    const scope = getScope();
    if (scope === null) return;
    try {
      if (navigator.onLine && canReplay()) await drainWorkerOutbox(scope, getScope);
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
