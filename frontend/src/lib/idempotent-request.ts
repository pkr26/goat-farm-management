/**
 * Client-side idempotency for the small set of mutations that can create
 * duplicate financial or stock effects.
 *
 * Successful requests are never cached. Only an in-flight promise, or the key
 * from an ambiguous/retryable failure, is retained. This deliberately avoids
 * generic response caching and prevents separate completed user actions from
 * being collapsed together.
 */

import { safeStorage } from "@/lib/safe-storage";

const RETRY_KEY_TTL_MS = 2 * 60 * 1000;
const MAX_LOGICAL_REQUESTS = 128;
const MAX_STORAGE_BYTES = 64 * 1024;
const PERSISTENCE_VERSION = 1;
export const IDEMPOTENCY_SESSION_STORAGE_KEY = "goatfarm:idempotency:v1";
const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

type LogicalRequest<T> = {
  key: string;
  expiresAt: number;
  promise: Promise<T> | null;
  signal: AbortSignal | null;
  persistedDigest: string | null;
};

type PersistedLogicalRequest = {
  version: number;
  digest: string;
  key: string;
  expiresAt: number;
};

const logicalRequests = new Map<string, LogicalRequest<unknown>>();

function writePersistedRecords(
  storage: Storage,
  records: PersistedLogicalRequest[],
): void {
  try {
    if (records.length === 0) storage.removeItem(IDEMPOTENCY_SESSION_STORAGE_KEY);
    else storage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, JSON.stringify(records));
  // Storage can be disabled or over quota. Realm-only idempotency remains.
  } catch {
  }
}

/** Exported for direct persistence-rule testing (expiry window, shape
 * validation, dedupe, ordering, cap and canonical rewrite). */
export function readPersistedRecords(storage: Storage, now: number): PersistedLogicalRequest[] {
  let raw: string | null;
  try {
    raw = storage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY);
  } catch {
    return [];
  }
  if (raw === null) return [];
  if (raw.length > MAX_STORAGE_BYTES) {
    writePersistedRecords(storage, []);
    return [];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    writePersistedRecords(storage, []);
    return [];
  }
  if (!Array.isArray(parsed)) {
    writePersistedRecords(storage, []);
    return [];
  }

  const seen = new Set<string>();
  const records: PersistedLogicalRequest[] = [];
  for (const candidate of parsed) {
    if (typeof candidate !== "object" || candidate === null) continue;
    const record = candidate as Partial<PersistedLogicalRequest>;
    if (
      record.version !== PERSISTENCE_VERSION ||
      typeof record.digest !== "string" ||
      !SHA256_PATTERN.test(record.digest) ||
      typeof record.key !== "string" ||
      !UUID_V4_PATTERN.test(record.key) ||
      typeof record.expiresAt !== "number" ||
      !Number.isFinite(record.expiresAt) ||
      record.expiresAt <= now ||
      record.expiresAt > now + RETRY_KEY_TTL_MS ||
      seen.has(record.digest)
    ) {
      continue;
    }
    seen.add(record.digest);
    records.push({
      version: PERSISTENCE_VERSION,
      digest: record.digest,
      key: record.key,
      expiresAt: record.expiresAt,
    });
  }
  records.sort((left, right) => right.expiresAt - left.expiresAt);
  const bounded = records.slice(0, MAX_LOGICAL_REQUESTS);
  if (JSON.stringify(bounded) !== raw) writePersistedRecords(storage, bounded);
  return bounded;
}

function loadPersistedKey(digest: string, now: number): string | null {
  const storage = safeStorage("session");
  if (!storage) return null;
  return readPersistedRecords(storage, now).find((record) => record.digest === digest)?.key ?? null;
}

function persistKey(digest: string | null, key: string, now: number): void {
  if (!digest) return;
  const storage = safeStorage("session");
  if (!storage) return;
  const records = readPersistedRecords(storage, now).filter(
    (record) => record.digest !== digest,
  );
  // RT-Q-2: a flood of distinct protected mutations used to trim the bounded
  // store purely by expiry, which could push out the recovery digest of a send
  // whose outcome is still unresolved. Mirror the in-memory makeRoom policy
  // instead: a record whose logical request is still live in this realm (in
  // flight, or retained for explicit retry after an ambiguous failure) is
  // never evicted — only orphaned records (from a prior page load, or one
  // makeRoom has already dropped from realm memory) may go, oldest expiry
  // first. In-flight capacity stays bounded because makeRoom throws at 128
  // live entries, so protected records can never exceed the cap on their own
  // and the container stays within its storage budget.
  const liveDigests = new Set<string>();
  for (const entry of logicalRequests.values()) {
    if (entry.persistedDigest !== null) liveDigests.add(entry.persistedDigest);
  }
  const protectedCount = records.reduce(
    (count, record) => count + (liveDigests.has(record.digest) ? 1 : 0),
    0,
  );
  const evictableRoom = Math.max(MAX_LOGICAL_REQUESTS - 1 - protectedCount, 0);
  const evictable = records
    .filter((record) => !liveDigests.has(record.digest))
    .sort((left, right) => right.expiresAt - left.expiresAt)
    .slice(0, evictableRoom);
  const evictableKept = new Set(evictable.map((record) => record.digest));
  const bounded = records.filter(
    (record) => liveDigests.has(record.digest) || evictableKept.has(record.digest),
  );
  writePersistedRecords(
    storage,
    [
      {
        version: PERSISTENCE_VERSION,
        digest,
        key,
        expiresAt: now + RETRY_KEY_TTL_MS,
      },
      // Last-resort bound: unreachable while makeRoom caps live entries, but
      // the container is never allowed past the cap even for a hand-crafted
      // in-memory state — keep the newest records, drop the oldest.
      ...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS - 1, 0)),
    ],
  );
}

function removePersistedKey(digest: string | null): void {
  if (!digest) return;
  const storage = safeStorage("session");
  if (!storage) return;
  const records = readPersistedRecords(storage, Date.now()).filter(
    (record) => record.digest !== digest,
  );
  writePersistedRecords(storage, records);
}

async function sha256(value: string): Promise<string | null> {
  // Never replace a cryptographic digest with a collision-prone weak hash.
  try {
    const subtle = globalThis.crypto?.subtle;
    if (!subtle || typeof TextEncoder === "undefined") return null;
    const digest = await subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
  // Never replace a cryptographic digest with a collision-prone weak hash.
  } catch {
    return null;
  }
}

function requestPath(url: string): string {
  try {
    return new URL(url, "https://goatfarm.invalid").pathname;
  } catch {
    return url.split(/[?#]/, 1)[0];
  }
}

/** Exact allowlist of the generated mutation routes backed by idempotency. */
export function isIdempotencyProtectedMutation(url: string, method?: string): boolean {
  if ((method ?? "GET").toUpperCase() !== "POST") return false;
  const path = requestPath(url);
  return (
    path === "/api/auth/farms" ||
    path === "/api/finance/new" ||
    // Renewing a policy posts a new premium/period row; the spec declares
    // the key, so an ambiguous retry must not duplicate the renewal.
    /^\/api\/finance\/insurance\/\d+\/renew$/.test(path) ||
    /^\/api\/finance\/transactions\/\d+\/correct$/.test(path) ||
    path === "/api/purchases/new" ||
    path === "/api/animals" ||
    /^\/api\/animals\/\d+\/weight$/.test(path) ||
    path === "/api/tasks" ||
    // Duty completion/skip accept the key server-side (tablet offline retry).
    /^\/api\/tasks\/\d+\/(complete|skip)$/.test(path) ||
    // Duty verification/rejection: the server accepts the key on both — an ambiguous
    // verify/reject retry must replay the first verdict, not answer a bare conflict.
    /^\/api\/tasks\/\d+\/(verify|reject)$/.test(path) ||
    path === "/api/team/workers" ||
    // PIN resets re-key a worker's tablet credential; the server accepts the
    // Idempotency-Key so an ambiguous retry cannot double-rotate it. Password
    // resets carry the same guarantee server-side (A1) — the retry replays
    // instead of double-rotating the credential.
    /^\/api\/team\/workers\/\d+\/reset-(?:pin|password)$/.test(path) ||
    // Terminal status transitions book a ledger transaction on SOLD/CULLED;
    // the server accepts the key (A1) so a network-lost sale replays its
    // committed response rather than 409 "already sold".
    /^\/api\/animals\/\d+\/status$/.test(path) ||
    path === "/api/health/events" ||
    path === "/api/simulation/scenarios" ||
    // Saving a plan creates durable planning state. Its server endpoint
    // accepts Idempotency-Key, so preserve the key across an ambiguous retry
    // instead of leaving a completed save indistinguishable from a timeout.
    path === "/api/planner/plans" ||
    // Screening walkthroughs: a batch and its upload URLs are durable
    // server-side state, and the spec declares the key on both — an
    // ambiguous retry must not mint duplicate batches/upload sessions.
    // (Batch creation gets its own carve-outs below: a void POST cannot
    // tell two walkthroughs apart, so it never shares an in-flight promise
    // or a persisted key.)
    path === "/api/screening/batches" ||
    path === "/api/screening/uploads" ||
    // Pregnancy/kidding creation auto-creates tasks and (for kidding) animals,
    // so an ambiguous replay duplicates durable stock. Both routes now
    // declare the Idempotency-Key server-side too, so the automatic network
    // retry replays the committed response instead of a 409.
    path === "/api/breeding" ||
    path === "/api/kidding" ||
    // Stock/money mutations. The server REQUIRES the key on dispense and
    // finance/new (no DB natural key backs them — the key is the only replay
    // defense); the remaining stock routes below merely accept it, and the
    // client still sends it for single-retry coalescing.
    path === "/api/feeding/dispense" ||
    path === "/api/feeding/mix" ||
    /^\/api\/feeding\/inventory\/\d+\/add$/.test(path)
  );
}

/**
 * Cross-reload recovery stores a digest of the full request body in
 * sessionStorage so it can locate the original random idempotency key. That
 * is not acceptable for a password- or PIN-bearing request: even a one-way,
 * unsalted body digest is an offline verifier for a guessed credential (and
 * a 4–12 digit numeric PIN is a far weaker secret than a password). Those
 * endpoints still get normal in-memory coalescing and their one automatic
 * network retry; only recovery after a page reload is deliberately disabled.
 *
 * Batch creation is excluded for the opposite reason: it is a VOID POST, so
 * every walkthrough's create has the same digest — a later, genuinely new
 * batch create would load a prior walkthrough's key and be handed the OLD
 * batch instead of a fresh one.
 */
function allowsPersistedRecovery(url: string): boolean {
  const path = requestPath(url);
  return (
    path !== "/api/team/workers" &&
    !/^\/api\/team\/workers\/\d+\/reset-(?:pin|password)$/.test(path) &&
    path !== "/api/screening/batches"
  );
}

/**
 * Whether two byte-identical in-flight requests are the same logical action
 * and may share one promise (and one key). Everywhere except batch creation
 * they are: a double-submitted form IS one action. Batch creation is a void
 * POST, so a delayed create from a CLOSED walkthrough and the create from
 * the REOPENED dialog are byte-identical yet two different batches — the
 * reopened walkthrough must start its own immediately rather than waiting on
 * (and inheriting) the abandoned request (pinned by the DiseaseCheckDialog
 * isolation test). Each attempt still gets a fresh key with the usual
 * same-key automatic network retry.
 */
function allowsInflightSharing(url: string): boolean {
  return requestPath(url) !== "/api/screening/batches";
}

/**
 * Mint a cryptographically random idempotency key on any origin. Plain HTTP tablet
 * deployments may expose getRandomValues without randomUUID; use the secure fallback
 * rather than calling randomUUID at mutation sites.
 */
export function randomIdempotencyKey(): string {
  const cryptography = globalThis.crypto;
  if (!cryptography) {
    throw new Error("Secure random generation is unavailable; mutation was not sent.");
  }
  if (typeof cryptography.randomUUID === "function") return cryptography.randomUUID();

  const bytes = new Uint8Array(16);
  cryptography.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function bodySignature(body: BodyInit | null | undefined): string {
  if (body == null) return "";
  if (typeof body === "string") return body;
  if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) {
    return body.toString();
  }
  // Every protected generated endpoint currently sends JSON text. Refuse to
  // guess equality for streams/blobs, where consuming or stringifying the
  // body could corrupt the real request.
  throw new Error("Protected mutations require a replayable string request body.");
}

function headerSignature(headers: Headers): string {
  return Array.from(headers.entries())
    .filter(([name]) => name.toLowerCase() !== "idempotency-key")
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => `${name.toLowerCase()}:${value}`)
    .join("\n");
}

function logicalSignature(
  url: string,
  init: RequestInit,
  farmScope: string | null,
  sessionScope: number,
  callerKey: string | null,
): string {
  const headers = new Headers(init.headers);
  return [
    (init.method ?? "GET").toUpperCase(),
    farmScope ?? "",
    String(sessionScope),
    url,
    bodySignature(init.body),
    headerSignature(headers),
    callerKey ?? "",
  ].join("\u0000");
}

function persistentSignature(
  url: string,
  init: RequestInit,
  farmScope: string | null,
  actorScope: string,
): string {
  const headers = new Headers(init.headers);
  // Unlike the realm-only signature, this intentionally omits the ephemeral
  // session epoch so the same authenticated actor can recover after reload.
  // The stable JWT subject and farm scope prevent a different actor/farm from
  // loading that key; the backend independently namespaces keys by actor too.
  return [
    `v${PERSISTENCE_VERSION}`,
    (init.method ?? "GET").toUpperCase(),
    actorScope,
    farmScope ?? "",
    url,
    bodySignature(init.body),
    headerSignature(headers),
  ].join("\u0000");
}

function hasHttpStatus(error: unknown): error is { status: number } {
  return (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    typeof (error as { status?: unknown }).status === "number"
  );
}

function isAbortError(error: unknown): boolean {
  // AbortSignal.timeout() rejects with a "TimeoutError" DOMException, not an
  // "AbortError". Both are locally-owned cancellations of the transport, and
  // neither may trigger the automatic network replay: an internally-owned
  // timeout must only ever leave the retained logical key for an explicit
  // user retry. Caller-owned aborts (TanStack unmount/farm-switch signals)
  // reject with a genuine "AbortError" and are likewise never retried.
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    ((error as { name?: unknown }).name === "AbortError" ||
      (error as { name?: unknown }).name === "TimeoutError")
  );
}

function shouldRetainForExplicitRetry(error: unknown): boolean {
  if (!hasHttpStatus(error)) return true;
  // A 401 can be the second attempt after a response-losing network failure.
  // It proves only that the replay was not authenticated, not that the first
  // send failed to commit, so retain the logical key for the same actor.
  return (
    error.status === 401 ||
    error.status === 408 ||
    error.status === 409 ||
    error.status === 429 ||
    error.status >= 500
  );
}

function cleanup(now: number): void {
  for (const [signature, entry] of logicalRequests) {
    if (entry.promise === null && entry.expiresAt <= now) logicalRequests.delete(signature);
  }
}

function makeRoom(): void {
  if (logicalRequests.size < MAX_LOGICAL_REQUESTS) return;
  for (const [signature, entry] of logicalRequests) {
    if (entry.promise === null) {
      logicalRequests.delete(signature);
      return;
    }
  }
  throw new Error("Too many protected mutations are already in flight. Try again shortly.");
}

async function executeWithOneNetworkRetry<T>(
  execute: (init: RequestInit) => Promise<T>,
  init: RequestInit,
): Promise<T> {
  try {
    return await execute(init);
  } catch (error) {
    if (hasHttpStatus(error) || isAbortError(error)) throw error;
    return execute(init);
  }
}

export async function runIdempotencyProtectedRequest<T>({
  url,
  init,
  farmScope,
  sessionScope,
  actorScope,
  execute,
  cloneResult,
  assertRequestScope,
}: {
  url: string;
  init: RequestInit;
  farmScope: string | null;
  sessionScope: number;
  actorScope: string | null;
  execute: (init: RequestInit) => Promise<T>;
  cloneResult?: (result: T) => T;
  /** Re-check external ownership after asynchronous preparation. A logout can
   * clear the registry while SHA-256 is still pending; without this fence the
   * stale continuation could recreate the previous actor's durable retry key
   * after teardown had completed. */
  assertRequestScope?: () => void;
}): Promise<T> {
  assertRequestScope?.();
  if (!isIdempotencyProtectedMutation(url, init.method)) return execute(init);

  const headers = new Headers(init.headers);
  const callerProvidedKey = headers.has("Idempotency-Key");
  const callerKey = callerProvidedKey ? headers.get("Idempotency-Key") : null;
  const signal = init.signal ?? null;
  const signature = logicalSignature(url, init, farmScope, sessionScope, callerKey);
  const persistedDigest =
    // An opaque/non-JWT token has no stable actor identity. In that case we
    // fail safely back to memory-only behavior instead of persisting a key
    // that a later login could claim.
    !callerProvidedKey && actorScope && allowsPersistedRecovery(url)
      ? await sha256(persistentSignature(url, init, farmScope, actorScope))
      : null;
  // sha256() is the only yield before the registry and sessionStorage writes.
  // Ownership may have changed while Web Crypto was working.
  assertRequestScope?.();
  const now = Date.now();
  cleanup(now);

  let entry = logicalRequests.get(signature) as LogicalRequest<T> | undefined;
  if (entry?.promise && !allowsInflightSharing(url)) {
    // The in-flight twin is NOT this action's retry (see
    // allowsInflightSharing): drop the lookup so a fresh entry with its own
    // key is created below. The replaced attempt's settle handlers are
    // identity-guarded, so its late completion cannot touch the new entry.
    entry = undefined;
  }
  if (entry?.promise) {
    // Sharing is safe only when cancellation ownership is also shared. A
    // different signal must not be silently ignored or cancel another
    // caller's canonical submission.
    if (entry.signal !== signal) {
      const error = new Error(
        "An identical protected mutation is already running with a different cancellation signal.",
      );
      error.name = "ProtectedMutationSignalConflictError";
      throw error;
    }
    const result = await entry.promise;
    return cloneResult ? cloneResult(result) : result;
  }

  if (!entry) {
    makeRoom();
    entry = {
      key: callerProvidedKey
        ?
          (callerKey ?? "")
        : (persistedDigest ? loadPersistedKey(persistedDigest, now) : null) ??
          randomIdempotencyKey(),
      expiresAt: now + RETRY_KEY_TTL_MS,
      promise: null,
      signal,
      persistedDigest,
    };
    logicalRequests.set(signature, entry as LogicalRequest<unknown>);
  }

  // A settled ambiguous request may be explicitly retried with a fresh
  // signal; it still reuses the retained logical key.
  entry.signal = signal;
  // The record is durable before fetch starts, so a reload after an
  // ambiguous send can recover the exact same key.
  persistKey(entry.persistedDigest, entry.key, now);
  headers.set("Idempotency-Key", entry.key);
  const preparedInit: RequestInit = { ...init, headers };
  const currentEntry = entry;
  const promise = executeWithOneNetworkRetry(execute, preparedInit);
  currentEntry.promise = promise;

  void promise.then(
    () => {
      if (logicalRequests.get(signature) === currentEntry) {
        logicalRequests.delete(signature);
        removePersistedKey(currentEntry.persistedDigest);
      }
    },
    (error: unknown) => {
      if (logicalRequests.get(signature) !== currentEntry) return;
      if (shouldRetainForExplicitRetry(error)) {
        currentEntry.promise = null;
        currentEntry.expiresAt = Date.now() + RETRY_KEY_TTL_MS;
        persistKey(currentEntry.persistedDigest, currentEntry.key, Date.now());
      } else {
        logicalRequests.delete(signature);
        removePersistedKey(currentEntry.persistedDigest);
      }
    },
  );

  const result = await promise;
  return cloneResult ? cloneResult(result) : result;
}

/** Clears realm memory only; session storage intentionally survives reloads.
 * Production teardown uses clearPersistedIdempotencyRequestState below; this
 * realm-only variant has no production call site and is exported for the
 * persistence suites, which use it to simulate a same-tab reload. */
export function clearIdempotencyRequestState(): void {
  logicalRequests.clear();
}

/** Remove persisted recovery keys when an authenticated session ends.
 *
 * A successful reload intentionally keeps non-sensitive recovery keys, but a
 * logout/session-replacement boundary must not leave a prior account's retry
 * material in the browser profile. Clearing realm state at the same time
 * prevents an in-flight old-session request from writing it back on settle.
 */
export function clearPersistedIdempotencyRequestState(): void {
  logicalRequests.clear();
  const storage = safeStorage("session");
  if (storage) writePersistedRecords(storage, []);
}
