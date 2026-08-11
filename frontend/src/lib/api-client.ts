/**
 * Central fetch wrapper: same-origin /api calls (proxied to FastAPI by
 * next.config rewrites), bearer-token auth, X-Farm-Id injection, and a
 * single 401 → /api/auth/refresh retry.
 *
 * The access token lives in memory only (module store) — never localStorage.
 */

import type { UserOut } from "@/api/generated/models";
import {
  isIdempotencyProtectedMutation,
  runIdempotencyProtectedRequest,
} from "@/lib/idempotent-request";

let accessToken: string | null = null;
let accessTokenActorScope: string | null = null;
let authSessionEpoch = 0;
let currentFarmId: string | null = null;
let onAuthFailure: (() => void) | null = null;
export interface RefreshSessionResult {
  access_token: string;
  user: UserOut;
}

let refreshPromise: Promise<RefreshSessionResult | null> | null = null;
let refreshPromiseEpoch: number | null = null;

function tokenActorScope(token: string | null): string | null {
  if (!token || typeof globalThis.atob !== "function") return null;
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const padded = payload.replace(/-/g, "+").replace(/_/g, "/").padEnd(
      Math.ceil(payload.length / 4) * 4,
      "=",
    );
    const parsed = JSON.parse(globalThis.atob(padded)) as { sub?: unknown };
    return typeof parsed.sub === "string" || typeof parsed.sub === "number"
      ? String(parsed.sub)
      : null;
  } catch {
    return null;
  }
}

export function setAccessToken(
  token: string | null,
  actorScope?: string | number | null,
): void {
  // External token installation/teardown marks a new authenticated session.
  // Same-session refresh writes the rotated token internally without bumping.
  const changed = token !== accessToken;
  if (changed) authSessionEpoch += 1;
  accessToken = token;
  const parsedActor =
    actorScope === undefined
      ? tokenActorScope(token)
      : actorScope === null
        ? null
        : String(actorScope);
  // Auth bootstrap deliberately re-applies the token returned by refresh.
  // Preserve refresh's trusted UserOut fallback when an opaque token has no
  // decodable JWT subject and the token itself did not change.
  if (changed || actorScope !== undefined || parsedActor !== null || token === null) {
    accessTokenActorScope = parsedActor;
  }
}

export function setCurrentFarmId(farmId: string | null): void {
  currentFarmId = farmId;
}

export function setOnAuthFailure(handler: (() => void) | null): void {
  onAuthFailure = handler;
}

/** A /api/auth/refresh that never settles (black-holed network, captive-portal
 *  re-auth, wedged proxy) must not stall this tab forever — nor, through the
 *  cross-tab lock below, every other tab's queued 401 retry. Both waits are
 *  bounded. The lock wait is the longer of the two so one legitimately slow
 *  but still-bounded holder is always waited out; only a wedged holder (an
 *  older tab, or one whose timer the browser throttled) is bypassed. */
const REFRESH_REQUEST_TIMEOUT_MS = 10_000;
const REFRESH_LOCK_WAIT_TIMEOUT_MS = 12_000;

async function performRefresh(
  expectedEpoch: number,
  expectedActorScope: string | null,
): Promise<RefreshSessionResult | null> {
  if (authSessionEpoch !== expectedEpoch) return null;
  const requestTimeout = new AbortController();
  const requestTimer = setTimeout(
    () => requestTimeout.abort(),
    REFRESH_REQUEST_TIMEOUT_MS,
  );
  try {
    const resp = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
      signal: requestTimeout.signal,
    });
    if (!resp.ok) return null;
    const body = (await resp.json()) as RefreshSessionResult;
    if (authSessionEpoch !== expectedEpoch) return null;
    const refreshedActorScope =
      tokenActorScope(body.access_token) ??
      ((body.user as UserOut | undefined)?.id != null ? String(body.user.id) : null);
    if (
      expectedActorScope !== null &&
      refreshedActorScope !== expectedActorScope
    ) {
      // Another tab replaced the origin-wide refresh cookie with a different
      // account. Never replay the caller's request under that actor while the
      // current React tree still displays the old identity.
      setAccessToken(null);
      onAuthFailure?.();
      return null;
    }
    accessToken = body.access_token;
    accessTokenActorScope = refreshedActorScope;
    return body;
  } catch {
    // An aborted (timed-out) refresh is indistinguishable from any other
    // network failure here and takes the same path: no token, auth failure.
    return null;
  } finally {
    clearTimeout(requestTimer);
  }
}

async function performCoordinatedRefresh(
  expectedEpoch: number,
  expectedActorScope: string | null,
): Promise<RefreshSessionResult | null> {
  // Web Locks coordinates all same-origin tabs/windows. Waiting tabs begin
  // their fetch only after the first response has installed the rotated
  // httpOnly cookie, so they present the current token rather than replaying
  // the old one. The backend's short replay grace remains the fallback for
  // browsers without Web Locks and network-level races.
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (!locks) return performRefresh(expectedEpoch, expectedActorScope);
  // Bound the queue wait as well: without a signal a tab stuck behind a wedged
  // holder never runs its callback, so its apiFetch promise never settles and
  // its queries spin forever. Giving up downgrades to an uncoordinated refresh
  // — the backend's replay grace covers that — rather than dropping it.
  const waitTimeout = new AbortController();
  const waitTimer = setTimeout(
    () => waitTimeout.abort(),
    REFRESH_LOCK_WAIT_TIMEOUT_MS,
  );
  let granted = false;
  try {
    return await locks.request(
      "goatfarm-auth-refresh",
      { signal: waitTimeout.signal },
      () => {
        // The wait is over; performRefresh's own timeout bounds the rest, so
        // the pending abort must never reach an already-granted lock.
        granted = true;
        clearTimeout(waitTimer);
        return performRefresh(expectedEpoch, expectedActorScope);
      },
    );
  } catch (err) {
    if (granted || !waitTimeout.signal.aborted) throw err;
    return performRefresh(expectedEpoch, expectedActorScope);
  } finally {
    clearTimeout(waitTimer);
  }
}

export function refreshSession(): Promise<RefreshSessionResult | null> {
  // De-duplicate React/query concurrency inside this JavaScript realm too.
  const expectedEpoch = authSessionEpoch;
  const expectedActorScope = accessTokenActorScope;
  if (!refreshPromise || refreshPromiseEpoch !== expectedEpoch) {
    refreshPromise = performCoordinatedRefresh(expectedEpoch, expectedActorScope);
    refreshPromiseEpoch = expectedEpoch;
    const settled = refreshPromise;
    void settled.finally(() => {
      setTimeout(() => {
        if (refreshPromise === settled) {
          refreshPromise = null;
          refreshPromiseEpoch = null;
        }
      }, 0);
    });
  }
  return refreshPromise;
}

async function tryRefresh(): Promise<boolean> {
  return (await refreshSession()) !== null;
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function assertSafeApiPath(path: string): void {
  const validationOrigin = "https://goatfarm.invalid";
  let parsed: URL;
  try {
    parsed = new URL(path, validationOrigin);
  } catch {
    parsed = new URL("/invalid", validationOrigin);
  }
  const rawPathname = path.split(/[?#]/, 1)[0];
  if (
    !path.startsWith("/api/") ||
    parsed.origin !== validationOrigin ||
    !parsed.pathname.startsWith("/api/") ||
    parsed.pathname !== rawPathname ||
    parsed.hash !== "" ||
    rawPathname.includes("%")
  ) {
    const error = new Error("API requests must use a same-origin /api/... path.");
    error.name = "UnsafeApiPathError";
    throw error;
  }
}

function assertAuthSession(expectedEpoch: number): void {
  if (authSessionEpoch === expectedEpoch) return;
  const error = new Error(
    "Your authenticated session changed while this request was in progress. The request was not replayed.",
  );
  error.name = "AuthSessionChangedError";
  throw error;
}

/** FastAPI error bodies are {detail: string} or {detail: [{loc, msg}, ...]}. */
function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((e) =>
          e && typeof e === "object" && "msg" in e
            ? String((e as { msg: unknown }).msg)
            : String(e),
        )
        .join("; ");
    }
    return String(detail);
  }
  return fallback;
}

async function rawFetch(
  path: string,
  init: RequestInit = {},
  farmScope: string | null = currentFarmId,
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (farmScope) headers.set("X-Farm-Id", farmScope);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { ...init, headers, credentials: "include" });
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const sessionScope = authSessionEpoch;
  const resp = await apiResponse(path, init);
  assertAuthSession(sessionScope);
  if (resp.status === 204) return undefined as T;
  const data = (await resp.json()) as T;
  // Response bodies are asynchronous streams. The actor can change after
  // headers arrive but before JSON parsing finishes, so guard both edges.
  assertAuthSession(sessionScope);
  return data;
}

/** The only /api/auth/* paths exempt from the 401→refresh retry: a 401 there
 *  IS the answer (bad credentials / no refresh cookie), and retrying
 *  /api/auth/refresh itself would recurse. Every other path — including
 *  /api/auth/farms and /api/auth/me — gets one refresh + retry. */
const NO_REFRESH_PATHS = new Set([
  "/api/auth/login",
  "/api/auth/register",
  "/api/auth/refresh",
  "/api/auth/logout",
]);

/** Shared core: fetch with at most one 401→refresh retry, then map any
 *  remaining error to ApiError. Resolves to the raw (ok) Response. */
async function apiResponseOnce(
  path: string,
  init: RequestInit,
  farmScope: string | null,
  sessionScope: number,
  bufferSuccess: boolean,
): Promise<Response> {
  assertAuthSession(sessionScope);
  let resp = await rawFetch(path, init, farmScope);
  assertAuthSession(sessionScope);
  let clearedSession = false;
  if (resp.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    const refreshed = await tryRefresh();
    assertAuthSession(sessionScope);
    if (refreshed) {
      resp = await rawFetch(path, init, farmScope);
      assertAuthSession(sessionScope);
    } else {
      setAccessToken(null);
      onAuthFailure?.();
      clearedSession = true;
    }
  }
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      /* non-JSON error body */
    }
    // A failed refresh deliberately ended this request's own session; retain
    // the original 401 in that one case. All other delayed error bodies must
    // not cross an unrelated logout/login boundary either.
    if (!clearedSession) assertAuthSession(sessionScope);
    throw new ApiError(resp.status, extractDetail(body, resp.statusText));
  }
  // Fully consume protected successful bodies before their logical request is
  // marked complete. A connection that drops after response headers but
  // before the JSON arrives is still ambiguous and must retry with the key.
  if (!bufferSuccess || resp.status === 204) return resp;
  await resp.clone().arrayBuffer();
  return resp;
}

async function apiResponse(path: string, init: RequestInit = {}): Promise<Response> {
  assertSafeApiPath(path);
  // A farm switch must not move a 401/network replay into a different tenant.
  // Farm creation is the one actor-scoped protected mutation: it deliberately
  // neither sends nor hashes a stale selected-farm ID, so a lost response can
  // be recovered before the actor has any farm at all (or after switching).
  const farmScope =
    path.split("?", 1)[0] === "/api/auth/farms" ? null : currentFarmId;
  const sessionScope = authSessionEpoch;
  const actorScope = accessTokenActorScope;
  const protectedMutation = isIdempotencyProtectedMutation(path, init.method);
  return runIdempotencyProtectedRequest({
    url: path,
    init,
    farmScope,
    sessionScope,
    actorScope,
    execute: (preparedInit) =>
      apiResponseOnce(path, preparedInit, farmScope, sessionScope, protectedMutation),
    // The registry owns the untouched canonical response. Every concurrent
    // consumer gets an independent body stream.
    cloneResult: (response) => response.clone(),
  });
}

/** apiFetch variant that keeps the real status and headers — the orval
 *  custom instance wraps every generated call in this {data, status,
 *  headers} envelope, and pages branch on the status. */
export async function apiFetchEnvelope<T>(
  path: string,
  init: RequestInit = {},
): Promise<{ data: T; status: number; headers: Headers }> {
  const sessionScope = authSessionEpoch;
  const resp = await apiResponse(path, init);
  assertAuthSession(sessionScope);
  const data = resp.status === 204 ? undefined : await resp.json();
  assertAuthSession(sessionScope);
  return { data: data as T, status: resp.status, headers: resp.headers };
}
