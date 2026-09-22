/**
 * Offline mutation queue for the worker tablet (ITEM 2 Phase 2,
 * 2026-09-21 playbook).
 *
 * Rural tablets spend real time offline. Duty completions recorded in the
 * field must survive connectivity loss exactly once: every queued mutation
 * carries the Idempotency-Key it will retry under, replays FIFO through
 * apiFetch when connectivity returns, and is scoped to the actor+farm that
 * enqueued it so a shared tablet never replays someone else's writes.
 *
 * Hardening mirrors idempotent-request's persistence rules: bounded record
 * count, bounded storage bytes, a version field, and fail-closed reads (any
 * malformed store is discarded wholesale — a corrupted queue is a nuisance,
 * a misparsed one is a data-integrity bug).
 */

const QUEUE_VERSION = 1;
const MAX_QUEUED_MUTATIONS = 100;
const MAX_STORAGE_BYTES = 256 * 1024;

export const OFFLINE_QUEUE_STORAGE_KEY = "goatfarm:offlineQueue:v1";

export type QueuedMutation = {
  id: string;
  path: string;
  /** Serialized request recipe: method + JSON body + headers. */
  method: string;
  body: string | null;
  headers: Record<string, string>;
  queuedAt: number;
  /** Stable actor identity (JWT subject) — a different signed-in worker must
   * never replay these writes. */
  actorScope: string;
  /** X-Farm-Id the mutation was scoped to. */
  farmScope: string;
  v: number;
};

export type QueueScopes = { actorScope: string; farmScope: string };

function availableLocalStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function writeQueue(storage: Storage, records: QueuedMutation[]): void {
  try {
    if (records.length === 0) storage.removeItem(OFFLINE_QUEUE_STORAGE_KEY);
    else storage.setItem(OFFLINE_QUEUE_STORAGE_KEY, JSON.stringify(records));
  } catch {
    /* blocked or over quota: the queue simply stops persisting */
  }
}

function wellFormed(value: unknown): value is QueuedMutation {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.v === QUEUE_VERSION &&
    typeof record.id === "string" &&
    record.id.length > 0 &&
    typeof record.path === "string" &&
    record.path.startsWith("/api/") &&
    typeof record.method === "string" &&
    (record.body === null || typeof record.body === "string") &&
    typeof record.headers === "object" &&
    record.headers !== null &&
    typeof record.queuedAt === "number" &&
    Number.isFinite(record.queuedAt) &&
    typeof record.actorScope === "string" &&
    typeof record.farmScope === "string"
  );
}

/** Fail-closed read: malformed or oversized stores are dropped wholesale. */
const NULL_STORAGE: Storage = {
  length: 0,
  clear: () => {},
  getItem: () => null,
  key: () => null,
  removeItem: () => {},
  setItem: () => {},
};

export function readOfflineQueue(storage: Storage = availableLocalStorage() ?? NULL_STORAGE): QueuedMutation[] {
  let raw: string | null;
  try {
    raw = storage.getItem(OFFLINE_QUEUE_STORAGE_KEY);
  } catch {
    return [];
  }
  if (raw === null) return [];
  if (raw.length > MAX_STORAGE_BYTES) {
    writeQueue(storage, []);
    return [];
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    writeQueue(storage, []);
    return [];
  }
  if (!Array.isArray(parsed)) {
    writeQueue(storage, []);
    return [];
  }
  const records = parsed.filter(wellFormed);
  if (records.length !== parsed.length) writeQueue(storage, records);
  return records;
}

export function wipeOfflineQueue(): void {
  const storage = availableLocalStorage();
  if (storage !== null) writeQueue(storage, []);
}

export function offlineQueueDepth(): number {
  return readOfflineQueue().length;
}

/** Queue one mutation. Returns false when the queue is at its bound — the
 * caller surfaces a real error rather than pretending the write landed. */
export function enqueueOfflineMutation(
  path: string,
  init: { method: string; body?: string | null; headers?: Record<string, string> },
  scopes: QueueScopes,
): boolean {
  const storage = availableLocalStorage();
  if (storage === null) return false;
  const records = readOfflineQueue(storage);
  if (records.length >= MAX_QUEUED_MUTATIONS) return false;
  const record: QueuedMutation = {
    id: crypto.randomUUID(),
    path,
    method: init.method.toUpperCase(),
    body: init.body ?? null,
    headers: { ...(init.headers ?? {}) },
    queuedAt: Date.now(),
    actorScope: scopes.actorScope,
    farmScope: scopes.farmScope,
    v: QUEUE_VERSION,
  };
  const next = [...records, record];
  // Byte cap checked before the write so an oversized record cannot wedge the
  // store: drop oldest-first until it fits, never exceeding the count bound.
  while (next.length > 0 && JSON.stringify(next).length > MAX_STORAGE_BYTES) {
    next.shift();
  }
  if (next.length === 0) return false;
  writeQueue(storage, next);
  return true;
}

/** Mutations whose server endpoints replay safely under an Idempotency-Key —
 * the only writes this queue will carry. */
export function isOfflineQueueableMutation(path: string, method?: string): boolean {
  if ((method ?? "GET").toUpperCase() !== "POST") return false;
  return /^\/api\/tasks\/\d+\/(complete|skip)$/.test(path);
}

/** Does the failure mean "send it later"? Network-level failures and the
 * browser's offline flag do; HTTP error statuses are answers, not outages. */
export function isOfflineQueueableFailure(error: unknown): boolean {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    ((error as { name?: unknown }).name === "TypeError" ||
      (error as { name?: unknown }).name === "AbortError")
  );
}

export type DrainOutcome = {
  replayed: number;
  remaining: number;
};

/**
 * Replay the queue FIFO for the CURRENT session's scopes only. A record from
 * another actor or farm is skipped (left in place), never dropped and never
 * replayed. The first unrecoverable failure stops the drain so FIFO order is
 * preserved; 409s mean the server already applied the write under this key —
 * done, drop it.
 *
 * The replay function is injectable for tests.
 */
export async function drainOfflineQueue(
  scopes: QueueScopes,
  fetchImpl: (path: string, init: RequestInit) => Promise<unknown> = (path, init) =>
    import("@/lib/api-client").then((m) => m.apiFetch(path, init)),
): Promise<DrainOutcome> {
  const storage = availableLocalStorage();
  if (storage === null) return { replayed: 0, remaining: 0 };
  const records = readOfflineQueue(storage);
  let replayed = 0;
  let index = 0;
  let stopped = false;
  while (index < records.length) {
    const record = records[index];
    if (record.actorScope !== scopes.actorScope || record.farmScope !== scopes.farmScope) {
      // Not ours: leave it queued for whoever owns it.
      index += 1;
      continue;
    }
    if (stopped) {
      index += 1;
      continue;
    }
    try {
      await fetchImpl(record.path, {
        method: record.method,
        body: record.body ?? undefined,
        headers: record.headers,
      });
      records.splice(index, 1);
      replayed += 1;
    } catch (error) {
      const status = (error as { status?: unknown } | null)?.status;
      if (status === 409) {
        // The server already committed this exact keyed write: done.
        records.splice(index, 1);
        replayed += 1;
        continue;
      }
      if (typeof status === "number" && status >= 400 && status < 500) {
        // A definitive client rejection (403/404/422): retrying cannot fix
        // it; drop the record rather than wedging the queue forever.
        records.splice(index, 1);
        continue;
      }
      // 5xx or transport failure: back off — keep the record, stop here.
      stopped = true;
      index += 1;
    }
  }
  writeQueue(storage, records);
  return { replayed, remaining: records.length };
}

let workersRunning = false;

/** Start the drain triggers (online event, focus, interval). Idempotent;
 * called by the worker shell. Returns a stop function for tests. */
export function startOfflineQueueWorkers(getScopes: () => QueueScopes | null): () => void {
  if (workersRunning) return () => {};
  workersRunning = true;
  const drainIfScoped = () => {
    const scopes = getScopes();
    if (scopes === null) return;
    if (typeof navigator !== "undefined" && navigator.onLine === false) return;
    void drainOfflineQueue(scopes);
  };
  window.addEventListener("online", drainIfScoped);
  window.addEventListener("focus", drainIfScoped);
  const timer = window.setInterval(drainIfScoped, 30_000);
  return () => {
    workersRunning = false;
    window.removeEventListener("online", drainIfScoped);
    window.removeEventListener("focus", drainIfScoped);
    window.clearInterval(timer);
  };
}
