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
 * count, bounded storage bytes, a 72-hour record TTL, a version field, and
 * fail-closed reads (any malformed store is discarded wholesale — a
 * corrupted queue is a nuisance, a misparsed one is a data-integrity bug).
 * Enqueue also fails closed on the queueable-mutation allowlist.
 */

import { safeStorage } from "@/lib/safe-storage";

const QUEUE_VERSION = 1;
const MAX_QUEUED_MUTATIONS = 100;
const MAX_STORAGE_BYTES = 256 * 1024;
/** Records older than this are pruned on read and on enqueue: an undrained
 * write older than a long weekend is almost certainly from an abandoned
 * session, and replaying it days later would mutate a board the worker has
 * long stopped watching (2026-09-28 audit, H2 leftover — the lead chose
 * 72h). */
const RECORD_TTL_MS = 72 * 60 * 60 * 1000;

export const OFFLINE_QUEUE_STORAGE_KEY = "goatfarm:offlineQueue:v1";

export type QueuedMutation = {
  id: string;
  path: string;
  /** Serialized request recipe: method + JSON body + headers. */
  method: string;
  body: string | null;
  headers: Record<string, string>;
  queuedAt: number;
  /** Stable actor identity — the signed-in user's numeric id as a string
   * (String(user.id) at the call sites). A different worker on the shared
   * tablet must never replay these writes. */
  actorScope: string;
  /** X-Farm-Id the mutation was scoped to. */
  farmScope: string;
  v: number;
};

export type QueueScopes = { actorScope: string; farmScope: string };

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
    !Array.isArray(record.headers) &&
    // Header VALUES must be strings too: a smuggled number/object would only
    // fail at replay time, deep inside the drain (2026-09-28 audit).
    Object.values(record.headers).every((value) => typeof value === "string") &&
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

export function readOfflineQueue(storage: Storage = safeStorage("local") ?? NULL_STORAGE): QueuedMutation[] {
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
  const now = Date.now();
  const records = parsed
    .filter(wellFormed)
    // TTL prune: a write older than RECORD_TTL_MS is almost certainly from an
    // abandoned session (constant above). Future-dated stamps (clock skew)
    // compare negative and are kept — a mis-set clock must not nuke the queue.
    .filter((record) => now - record.queuedAt < RECORD_TTL_MS);
  if (records.length !== parsed.length) writeQueue(storage, records);
  return records;
}

export function wipeOfflineQueue(): void {
  const storage = safeStorage("local");
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
  // Fail closed on the queueable-mutation allowlist (below): it used to be
  // documentation-only, so a future caller could queue a write the server
  // cannot replay safely under an Idempotency-Key (2026-09-28 audit).
  if (!isOfflineQueueableMutation(path, init.method)) return false;
  const storage = safeStorage("local");
  if (storage === null) return false;
  // readOfflineQueue prunes expired records first, so the count bound below
  // is measured against writes that could still legitimately replay.
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
 * browser's offline flag do; HTTP error statuses are answers, not outages.
 * A definitive 4xx stays unqueueable even when the offline flag is set —
 * connectivity can drop right after the server's rejection arrived, and
 * queueing that write would tell the worker "Saved" for something the
 * server already refused.
 *
 * The name arm distinguishes two failures that both abort the fetch: the
 * api-client's own wedged-connection timeout (TimeoutError — the field
 * connectivity case this queue exists for) is queueable, while a
 * caller-owned AbortError is a DELIBERATE cancellation (TanStack unmount /
 * farm switch) and must never enqueue a write (2026-09-28 audit, W5 — these
 * two were inverted). */
export function isOfflineQueueableFailure(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  if (typeof status === "number" && status >= 400 && status < 500) return false;
  if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    ((error as { name?: unknown }).name === "TypeError" ||
      (error as { name?: unknown }).name === "TimeoutError")
  );
}

export type DrainOutcome = {
  replayed: number;
  remaining: number;
  /** Records the server definitively refused (4xx): the duty stays PENDING
   * server-side and reappears on the board, but the completion the worker
   * recorded was dropped — the shell surfaces this count so the loss is not
   * silent (2026-09-29 audit). */
  rejected: number;
};

/**
 * Replay the queue FIFO for the CURRENT session's scopes only. A record from
 * another actor or farm is skipped (left in place), never dropped and never
 * replayed. The first unrecoverable failure stops the drain so FIFO order is
 * preserved; 409s mean the write is already reflected server-side — either a
 * same-key replay or, since the 2026-09-28 audit (A3), a fresh "not pending"
 * answer because the duty already transitioned some other way — done, drop
 * it. 401/408/429 are transient answers (refresh unavailable, timeout, rate
 * limit), not rejections: the record is KEPT and the drain stops, so a
 * momentarily dead session never destroys field writes. A 429's Retry-After
 * (the backend convention always sends one) pushes the next drain attempt
 * out by the server's hint instead of retrying at the fixed 30s cadence.
 * Other definitive 4xx answers (403/404/422, request-invalid 400/409) are
 * counted in `rejected` and dropped: the duty stays PENDING server-side and
 * reappears on the board, and the shell toasts the count so the worker
 * learns their recorded completion did not land.
 *
 * Concurrency: drains are fired by the online/focus/interval triggers AND the
 * worker shell's immediate drain, so invocations can overlap. A module-level
 * in-flight guard keeps a second drain from interleaving replays, and the
 * write-back is merge-safe: it re-reads live storage and removes only the
 * records THIS drain resolved, so a completion enqueued while a slow replay
 * was on the wire is never clobbered by a stale snapshot.
 *
 * The replay function is injectable for tests.
 */
let drainInFlight = false;
/** Earliest next drain attempt after a 429 — Date.now() epoch millis, set
 * from the server's Retry-After hint. Zero means "no backoff outstanding". */
let nextDrainAfterMs = 0;

/** Clear any outstanding 429 backoff gate. Called on session teardown (the
 * next actor's first drain re-observes the server's throttle for itself)
 * and by tests between scenarios. */
export function clearOfflineQueueDrainBackoff(): void {
  nextDrainAfterMs = 0;
}

export async function drainOfflineQueue(
  scopes: QueueScopes,
  fetchImpl: (path: string, init: RequestInit) => Promise<unknown> = (path, init) =>
    import("@/lib/api-client").then((m) => m.apiFetch(path, init)),
): Promise<DrainOutcome> {
  const storage = safeStorage("local");
  if (storage === null) return { replayed: 0, remaining: 0, rejected: 0 };
  if (drainInFlight) {
    // The in-flight drain (or the next trigger after it) owns the replay;
    // re-entering here would double-fire replays under the same key.
    return { replayed: 0, remaining: readOfflineQueue(storage).length, rejected: 0 };
  }
  if (Date.now() < nextDrainAfterMs) {
    // The server asked us to wait (429 Retry-After); hammering the throttle
    // at the fixed cadence only extends the block.
    return { replayed: 0, remaining: readOfflineQueue(storage).length, rejected: 0 };
  }
  drainInFlight = true;
  try {
    const records = readOfflineQueue(storage);
    let replayed = 0;
    let rejected = 0;
    const resolvedIds = new Set<string>();
    let stopped = false;
    for (const record of records) {
      if (record.actorScope !== scopes.actorScope || record.farmScope !== scopes.farmScope) {
        // Not ours: leave it queued for whoever owns it.
        continue;
      }
      if (stopped) continue;
      try {
        await fetchImpl(record.path, {
          method: record.method,
          body: record.body ?? undefined,
          headers: record.headers,
        });
        resolvedIds.add(record.id);
        replayed += 1;
      } catch (error) {
        const status = (error as { status?: unknown } | null)?.status;
        const retryAfter = (error as { retryAfterSeconds?: unknown } | null)?.retryAfterSeconds;
        if (status === 409) {
          // The write is already reflected server-side: either the server
          // committed this exact keyed write (same-key replay), or the duty
          // already transitioned under another key/actor and the fresh
          // wrong-state answer is 409 "Task is not pending" (2026-09-28
          // audit, A3 — previously 400, which the 4xx drop branch below
          // already settled the same way). Either way: done.
          resolvedIds.add(record.id);
          replayed += 1;
          continue;
        }
        if (status === 401 || status === 408 || status === 429) {
          // Not a definitive rejection: the token may be expired while refresh
          // is momentarily unavailable (the api-client classifies exactly that
          // as transient), the request timed out, or the server is
          // rate-limiting. Keep the record and stop here — a later drain after
          // re-login/backoff can still deliver it (2026-09-28 audit, H2). A
          // 429's Retry-After (2026-09-29 audit) gates the next attempt.
          if (status === 429 && typeof retryAfter === "number" && retryAfter > 0) {
            nextDrainAfterMs = Date.now() + retryAfter * 1000;
          }
          stopped = true;
          continue;
        }
        if (typeof status === "number" && status >= 400 && status < 500) {
          // A definitive client rejection (403/404/422): retrying cannot fix
          // it; drop the record rather than wedging the queue forever. The
          // duty stays PENDING server-side and reappears on the board — the
          // rejected count tells the shell to surface the loss.
          resolvedIds.add(record.id);
          rejected += 1;
          continue;
        }
        // 5xx or transport failure: back off — keep the record, stop here.
        stopped = true;
      }
    }
    // Merge against LIVE storage: anything enqueued after the snapshot (a
    // completion recorded mid-drain) is not in resolvedIds and survives;
    // only the records this drain actually settled are removed.
    const next = readOfflineQueue(storage).filter(
      (record) => !resolvedIds.has(record.id),
    );
    writeQueue(storage, next);
    return { replayed, remaining: next.length, rejected };
  } finally {
    drainInFlight = false;
  }
}

let workersRunning = false;

/** Start the drain triggers (online event, focus, interval). Idempotent;
 * called by the worker shell. Returns a stop function for tests.
 * `onRejected` fires whenever a drain settles records the server
 * definitively refused, so the shell can surface the loss instead of
 * silently discarding field-recorded completions (2026-09-29 audit). */
export function startOfflineQueueWorkers(
  getScopes: () => QueueScopes | null,
  onRejected?: (count: number) => void,
): () => void {
  if (workersRunning) return () => {};
  workersRunning = true;
  const drainIfScoped = () => {
    const scopes = getScopes();
    if (scopes === null) return;
    if (typeof navigator !== "undefined" && navigator.onLine === false) return;
    void drainOfflineQueue(scopes).then((outcome) => {
      if (outcome.rejected > 0) onRejected?.(outcome.rejected);
    });
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
