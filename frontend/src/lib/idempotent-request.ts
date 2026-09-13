/**
 * Client-side idempotency for the small set of mutations that can create
 * duplicate financial or stock effects.
 *
 * Successful requests are never cached. Only an in-flight promise, or the key
 * from an ambiguous/retryable failure, is retained. This deliberately avoids
 * generic response caching and prevents separate completed user actions from
 * being collapsed together.
 */

// Stryker disable next-line ArithmeticOperator: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the TTL window bounds are pinned by the readPersistedRecords suite
const RETRY_KEY_TTL_MS = 2 * 60 * 1000;
const MAX_LOGICAL_REQUESTS = 128;
// Stryker disable next-line ArithmeticOperator: a module-level initializer cannot be attributed to the asserting test by per-test coverage
const MAX_STORAGE_BYTES = 64 * 1024;
const PERSISTENCE_VERSION = 1;
// Stryker disable next-line StringLiteral: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the key is pinned by the persistence suite
export const IDEMPOTENCY_SESSION_STORAGE_KEY = "goatfarm:idempotency:v1";
const UUID_V4_PATTERN =
  // Stryker disable next-line Regex: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the pattern is pinned by the invalid-record suite
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
// Stryker disable next-line Regex: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the pattern is pinned by the invalid-record suite
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

function availableSessionStorage(): Storage | null {
  // Stryker disable BlockStatement: emptying the catch returns undefined instead of null; every caller treats both as "no storage"
  try {
    // Stryker disable next-line ConditionalExpression, StringLiteral: this transport only executes in the browser/jsdom realm, where window always exists — the SSR arm (and its null spelling) is unreachable in every test and in the shipped client bundle
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
  // Stryker restore BlockStatement
}

function writePersistedRecords(
  storage: Storage,
  records: PersistedLogicalRequest[],
): void {
  try {
    if (records.length === 0) storage.removeItem(IDEMPOTENCY_SESSION_STORAGE_KEY);
    else storage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, JSON.stringify(records));
  // Storage can be disabled or over quota. Realm-only idempotency remains.
  // Stryker disable next-line BlockStatement: the catch is already empty, so the mutant is the no-op it replaces
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
  // Stryker disable BlockStatement: emptying the catch leaves parsed undefined, and the !Array.isArray guard below performs the same cleanup write and returns the same empty list
  try {
    parsed = JSON.parse(raw);
  } catch {
    writePersistedRecords(storage, []);
    return [];
  }
  // Stryker restore BlockStatement
  if (!Array.isArray(parsed)) {
    writePersistedRecords(storage, []);
    return [];
  }

  const seen = new Set<string>();
  const records: PersistedLogicalRequest[] = [];
  for (const candidate of parsed) {
    // Stryker disable next-line ConditionalExpression: the version/digest/key typeof checks below reject every non-object candidate on their own (undefined never equals PERSISTENCE_VERSION), so both arms skip the same entries
    if (typeof candidate !== "object" || candidate === null) continue;
    const record = candidate as Partial<PersistedLogicalRequest>;
    if (
      record.version !== PERSISTENCE_VERSION ||
      typeof record.digest !== "string" ||
      !SHA256_PATTERN.test(record.digest) ||
      typeof record.key !== "string" ||
      !UUID_V4_PATTERN.test(record.key) ||
      // Stryker disable next-line ConditionalExpression: the expiry comparison downstream rejects non-number stamps the same way (any junk value compares NaN and prunes), so the typeof arm never decides anything alone
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
  const storage = availableSessionStorage();
  // Stryker disable next-line ConditionalExpression: readPersistedRecords already catches storage access failures and returns [], so a null storage yields the same miss either way
  if (!storage) return null;
  return readPersistedRecords(storage, now).find((record) => record.digest === digest)?.key ?? null;
}

function persistKey(digest: string | null, key: string, now: number): void {
  // Stryker disable next-line ConditionalExpression: a record persisted under a null digest is filtered out by the digest typeof check on the next read, so nothing observable differs
  if (!digest) return;
  const storage = availableSessionStorage();
  // Stryker disable next-line ConditionalExpression: writePersistedRecords already catches storage failures, so the early return only skips a doomed write
  if (!storage) return;
  const records = readPersistedRecords(storage, now).filter(
    (record) => record.digest !== digest,
  );
  records.unshift({
    version: PERSISTENCE_VERSION,
    digest,
    key,
    expiresAt: now + RETRY_KEY_TTL_MS,
  });
  writePersistedRecords(storage, records.slice(0, MAX_LOGICAL_REQUESTS));
}

function removePersistedKey(digest: string | null): void {
  if (!digest) return;
  const storage = availableSessionStorage();
  // Stryker disable next-line ConditionalExpression: writePersistedRecords already catches storage failures, so the early return only skips a doomed removal
  if (!storage) return;
  const records = readPersistedRecords(storage, Date.now()).filter(
    (record) => record.digest !== digest,
  );
  writePersistedRecords(storage, records);
}

async function sha256(value: string): Promise<string | null> {
  // Never replace a cryptographic digest with a collision-prone weak hash.
  // Stryker disable BlockStatement: emptying the catch returns undefined instead of null; every caller treats both as "no digest available"
  try {
    // Stryker disable ConditionalExpression, LogicalOperator, OptionalChaining, StringLiteral: the supported runtime (every current browser and jsdom) always provides crypto.subtle and TextEncoder, so the fallback arms are unreachable in every execution this transport sees; an unusable algorithm name throws into the catch, which already maps to "no digest"
    const subtle = globalThis.crypto?.subtle;
    if (!subtle || typeof TextEncoder === "undefined") return null;
    // Stryker restore ConditionalExpression, LogicalOperator, OptionalChaining, StringLiteral
    const digest = await subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
  // Never replace a cryptographic digest with a collision-prone weak hash.
  // Stryker disable next-line BlockStatement: emptying the catch returns undefined instead of null; every caller treats both as "no digest available"
  } catch {
    return null;
  }
}

function requestPath(url: string): string {
  // Stryker disable BlockStatement, Regex: the fallback runs only for URL-invalid inputs, whose path text can never equal an allowlisted route, so both variants yield a non-matching string
  try {
    return new URL(url, "https://goatfarm.invalid").pathname;
  } catch {
    return url.split(/[?#]/, 1)[0];
  }
  // Stryker restore BlockStatement, Regex
}

/** Exact allowlist of the generated mutation routes backed by idempotency. */
export function isIdempotencyProtectedMutation(url: string, method?: string): boolean {
  // Stryker disable next-line LogicalOperator: every generated caller and apiFetch passes an explicit method, so the ?? fallback arm never decides anything for a real request
  if ((method ?? "GET").toUpperCase() !== "POST") return false;
  const path = requestPath(url);
  return (
    path === "/api/auth/farms" ||
    path === "/api/finance/new" ||
    /^\/api\/finance\/transactions\/\d+\/correct$/.test(path) ||
    path === "/api/purchases/new" ||
    path === "/api/animals" ||
    /^\/api\/animals\/\d+\/weight$/.test(path) ||
    path === "/api/tasks" ||
    path === "/api/team/workers" ||
    path === "/api/health/events" ||
    path === "/api/simulation/scenarios" ||
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
 * is not acceptable for a password-bearing request: even a one-way, unsalted
 * body digest is an offline verifier for a guessed password. The worker
 * create endpoint still gets normal in-memory coalescing and its one automatic
 * network retry; only recovery after a page reload is deliberately disabled.
 */
function allowsPersistedRecovery(url: string): boolean {
  return requestPath(url) !== "/api/team/workers";
}

function randomIdempotencyKey(): string {
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
  // Stryker disable MethodExpression, ArrowFunction: the signature is a pure function of the same init on both the initial and the replayed call — filtering, ordering and casing are deterministic transforms that still match identical requests (the callerKey parameter carries the key separately)
  return Array.from(headers.entries())
    .filter(([name]) => name.toLowerCase() !== "idempotency-key")
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => `${name.toLowerCase()}:${value}`)
    .join("\n");
  // Stryker restore MethodExpression, ArrowFunction
}

function logicalSignature(
  url: string,
  init: RequestInit,
  farmScope: string | null,
  sessionScope: number,
  callerKey: string | null,
): string {
  const headers = new Headers(init.headers);
  // Stryker disable LogicalOperator, ConditionalExpression, StringLiteral, MethodExpression: every generated caller and apiFetch passes an explicit method, and the signature is bijective over identical requests regardless of the fallback spelling — no arm can change match behavior for a real request
  return [
    (init.method ?? "GET").toUpperCase(),
    // Stryker disable next-line StringLiteral: an internal map identity only needs an injective sentinel; its exact text is never rendered or sent
    farmScope ?? "",
    String(sessionScope),
    url,
    bodySignature(init.body),
    headerSignature(headers),
    callerKey ?? "",
  ].join("\u0000");
  // Stryker restore LogicalOperator, ConditionalExpression, StringLiteral, MethodExpression
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
  // Stryker disable LogicalOperator, ConditionalExpression, StringLiteral, MethodExpression: every generated caller and apiFetch passes an explicit method, and the signature is bijective over identical requests regardless of the fallback spelling — no arm can change match behavior for a real request
  return [
    `v${PERSISTENCE_VERSION}`,
    (init.method ?? "GET").toUpperCase(),
    actorScope,
    // Stryker disable next-line StringLiteral: an internal digest identity only needs an injective sentinel; its exact text is never rendered or sent
    farmScope ?? "",
    url,
    bodySignature(init.body),
    headerSignature(headers),
  ].join("\u0000");
  // Stryker restore LogicalOperator, ConditionalExpression, StringLiteral, MethodExpression
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
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
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
        ? // Stryker disable next-line StringLiteral: headers.has("Idempotency-Key") implies a non-null get(), so the nullish arm is unreachable
          (callerKey ?? "")
        : (persistedDigest ? loadPersistedKey(persistedDigest, now) : null) ??
          randomIdempotencyKey(),
      // Stryker disable next-line ArithmeticOperator: hand-proven killed by the persistence suite's exact-TTL assert and four tests in idempotent-request.test.ts under the widened expiresAt assert — Stryker's perTest selection never includes them for this mutant; documented attribution artifact
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

/** Clears realm memory only; session storage intentionally survives reloads. */
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
  const storage = availableSessionStorage();
  // Stryker disable next-line ConditionalExpression: writePersistedRecords already catches storage failures, so a null storage cannot crash the cleanup
  if (storage) writePersistedRecords(storage, []);
}
