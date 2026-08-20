/**
 * Central fetch wrapper: approved same-origin backend calls (proxied to
 * FastAPI by next.config rewrites), bearer-token auth, X-Farm-Id injection,
 * and a single 401 → /api/auth/refresh retry.
 *
 * The access token lives in memory only (module store) — never localStorage.
 */

import type { TokenOut, UserOut } from "@/api/generated/models";
import {
  isIdempotencyProtectedMutation,
  runIdempotencyProtectedRequest,
} from "@/lib/idempotent-request";

let accessToken: string | null = null;
let accessTokenActorScope: string | null = null;
let authSessionEpoch = 0;
let currentFarmId: string | null = null;
let farmScopeEpoch = 0;
let onAuthFailure: (() => void) | null = null;
const authFailureRegistrations: Array<{ handler: () => void }> = [];
export type RefreshSessionResult = Pick<TokenOut, "access_token" | "user">;

function parseRefreshSessionResult(body: unknown): RefreshSessionResult | null {
  if (typeof body !== "object" || body === null) return null;
  const candidate = body as {
    access_token?: unknown;
    user?: { id?: unknown; email?: unknown; name?: unknown } | null;
  };
  const user = candidate.user;
  if (
    typeof candidate.access_token !== "string" ||
    candidate.access_token.length === 0 ||
    /\s/.test(candidate.access_token) ||
    typeof user !== "object" ||
    user === null ||
    typeof user.id !== "number" ||
    !Number.isSafeInteger(user.id) ||
    user.id <= 0 ||
    typeof user.email !== "string" ||
    (user.name !== null && typeof user.name !== "string")
  ) {
    return null;
  }
  return { access_token: candidate.access_token, user: user as UserOut };
}

let refreshPromise: Promise<RefreshOutcome> | null = null;
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
  if (farmId !== currentFarmId) farmScopeEpoch += 1;
  currentFarmId = farmId;
}

/** Monotonic ownership boundary for async UI continuations. Requests already
 * on the wire retain their captured X-Farm-Id, but a success callback must not
 * navigate or rewrite UI after the operator has moved to another farm (even
 * if they switch back before the response arrives). */
export function farmScopeEpochValue(): number {
  return farmScopeEpoch;
}

export function setOnAuthFailure(handler: (() => void) | null): () => void {
  if (handler === null) {
    authFailureRegistrations.length = 0;
    onAuthFailure = null;
    return () => {};
  }
  const registration = { handler };
  authFailureRegistrations.push(registration);
  onAuthFailure = handler;
  // Providers can briefly overlap during a root replacement. Removing either
  // registration must leave the newest still-mounted provider active; if the
  // newer tree goes away first, restore the older tree's handler.
  return () => {
    const index = authFailureRegistrations.indexOf(registration);
    if (index === -1) return;
    authFailureRegistrations.splice(index, 1);
    onAuthFailure = authFailureRegistrations.at(-1)?.handler ?? null;
  };
}

/** A /api/auth/refresh that never settles (black-holed network, captive-portal
 *  re-auth, wedged proxy) must not stall this tab forever — nor, through the
 *  cross-tab lock below, every other tab's queued 401 retry. Both waits are
 *  bounded. A timed-out waiter fails transiently; it must never bypass a
 *  holder and race a login/logout response for the shared cookie jar. */
const REFRESH_REQUEST_TIMEOUT_MS = 10_000;
const AUTH_COOKIE_LOCK_NAME = "goatfarm-auth-refresh";
const AUTH_COOKIE_MUTATION_LOCK_WAIT_TIMEOUT_MS = 62_000;
const REFRESH_LOCK_WAIT_TIMEOUT_MS = AUTH_COOKIE_MUTATION_LOCK_WAIT_TIMEOUT_MS;

class AuthCookieCoordinationError extends Error {
  constructor(
    message = "Another authentication change is still finishing. Try again shortly.",
  ) {
    super(message);
    this.name = "AuthCookieCoordinationError";
  }
}

// Browsers without Web Locks still need same-realm ordering. Each ticket's
// gate is chained behind its predecessor, while a timed-out ticket releases
// only its own gate; later callers therefore continue waiting for the actual
// holder instead of accidentally entering the critical section beside it.
let localAuthCookieLockTail: Promise<void> = Promise.resolve();

async function waitForAuthCookieTurn(
  turn: Promise<void>,
  timeoutMs: number,
): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      turn,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new AuthCookieCoordinationError()), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

async function withLocalAuthCookieLock<T>(
  operation: () => Promise<T>,
  timeoutMs: number,
): Promise<T> {
  const predecessor = localAuthCookieLockTail.catch(() => undefined);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  localAuthCookieLockTail = predecessor.then(() => gate);
  try {
    await waitForAuthCookieTurn(predecessor, timeoutMs);
    return await operation();
  } finally {
    release();
  }
}

/** Serialize every response that can set/delete the origin-wide refresh
 * cookie. Holding the lock until fetch resolves is sufficient: response
 * headers (including Set-Cookie) have been processed at that point; body
 * parsing can proceed without owning the cookie critical section. */
async function withAuthCookieLock<T>(
  operation: () => Promise<T>,
  timeoutMs: number,
): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (!locks) return withLocalAuthCookieLock(operation, timeoutMs);

  const waitTimeout = new AbortController();
  const waitTimer = setTimeout(() => waitTimeout.abort(), timeoutMs);
  let granted = false;
  try {
    return await locks.request(
      AUTH_COOKIE_LOCK_NAME,
      { signal: waitTimeout.signal },
      () => {
        granted = true;
        clearTimeout(waitTimer);
        return operation();
      },
    );
  } catch (error) {
    if (granted) throw error;
    if (waitTimeout.signal.aborted) throw new AuthCookieCoordinationError();
    // If the API exists, another tab may already hold this Web Lock. Falling
    // back to an unrelated realm-only mutex after a pre-grant rejection would
    // bypass that exclusion and recreate the response-order cookie race. Fail
    // closed; the caller gets a bounded, actionable retry error.
    throw new AuthCookieCoordinationError(
      "Secure authentication coordination is temporarily unavailable. Try again shortly.",
    );
  } finally {
    clearTimeout(waitTimer);
  }
}

/** Why a refresh did not produce a session.
 *
 *  "rejected" is the server's authoritative answer — the refresh cookie is
 *  expired, revoked, replayed, or describes a different actor — and is the
 *  only outcome that may destroy local session state. "unavailable" means we
 *  never got that answer (transport failure, timeout, 5xx during a rolling
 *  deploy). Collapsing the two logged the operator out of a session the
 *  server still considers valid for the rest of the refresh cookie's 14-day
 *  life, discarding the query cache, the farm selection and any unsaved
 *  dialog state — on one dropped request. */
type RefreshOutcome =
  | { kind: "session"; body: RefreshSessionResult }
  | { kind: "rejected" }
  | { kind: "unavailable" };

/** 408/429 and 5xx are "the server could not answer right now", not "your
 *  session is invalid". Every other non-2xx is treated as authoritative. */
function isTransientRefreshStatus(status: number): boolean {
  return status >= 500 || status === 408 || status === 429;
}

async function performRefresh(
  expectedEpoch: number,
  expectedActorScope: string | null,
): Promise<RefreshOutcome> {
  if (authSessionEpoch !== expectedEpoch) return { kind: "rejected" };
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
    if (!resp.ok) {
      return isTransientRefreshStatus(resp.status)
        ? { kind: "unavailable" }
        : { kind: "rejected" };
    }
    const body = parseRefreshSessionResult(await resp.json());
    if (!body) return { kind: "rejected" };
    if (authSessionEpoch !== expectedEpoch) return { kind: "rejected" };
    const tokenScope = tokenActorScope(body.access_token);
    const userScope = String(body.user.id);
    if (tokenScope !== null && tokenScope !== userScope) {
      // A refresh response must describe the same actor as the signed token.
      // Otherwise the UI identity and every authenticated API request would
      // immediately diverge even during a signed-out bootstrap.
      setAccessToken(null);
      onAuthFailure?.();
      return { kind: "rejected" };
    }
    const refreshedActorScope = tokenScope ?? userScope;
    if (
      expectedActorScope !== null &&
      refreshedActorScope !== expectedActorScope
    ) {
      // Another tab replaced the origin-wide refresh cookie with a different
      // account. Never replay the caller's request under that actor while the
      // current React tree still displays the old identity.
      setAccessToken(null);
      onAuthFailure?.();
      return { kind: "rejected" };
    }
    accessToken = body.access_token;
    accessTokenActorScope = refreshedActorScope;
    return { kind: "session", body };
  } catch {
    // An aborted (timed-out) refresh and any other transport failure mean the
    // server never answered. That is not evidence the session ended, so the
    // caller keeps the installed token and surfaces a transient error.
    return { kind: "unavailable" };
  } finally {
    clearTimeout(requestTimer);
  }
}

async function performCoordinatedRefresh(
  expectedEpoch: number,
  expectedActorScope: string | null,
): Promise<RefreshOutcome> {
  // Web Locks coordinates all same-origin tabs/windows. Waiting tabs begin
  // their fetch only after the first response has installed the rotated
  // httpOnly cookie, so they present the current token rather than replaying
  // the old one. The backend's short replay grace remains the fallback for
  // browsers without Web Locks and network-level races.
  try {
    return await withAuthCookieLock(
      () => performRefresh(expectedEpoch, expectedActorScope),
      REFRESH_LOCK_WAIT_TIMEOUT_MS,
    );
  } catch (error) {
    if (error instanceof AuthCookieCoordinationError) {
      return { kind: "unavailable" };
    }
    throw error;
  }
}

/** Shared single-flight refresh, reporting WHY it did not produce a session. */
function refreshSessionOutcome(): Promise<RefreshOutcome> {
  // De-duplicate React/query concurrency inside this JavaScript realm too.
  const expectedEpoch = authSessionEpoch;
  const expectedActorScope = accessTokenActorScope;
  if (!refreshPromise || refreshPromiseEpoch !== expectedEpoch) {
    refreshPromise = performCoordinatedRefresh(expectedEpoch, expectedActorScope);
    refreshPromiseEpoch = expectedEpoch;
    const settled = refreshPromise;
    const release = () => {
      setTimeout(() => {
        if (refreshPromise === settled) {
          refreshPromise = null;
          refreshPromiseEpoch = null;
        }
      }, 0);
    };
    // Unlike an ignored finally() chain, the two-branch handler cannot mint a
    // second rejected promise if an unexpected lock implementation rejects.
    void settled.then(release, release);
  }
  return refreshPromise;
}

/** Public bootstrap/reauth entry point: the session, or null if there is none.
 *  This collapses "rejected" and "unavailable" together, so a caller that
 *  destroys session state on null would do so on one dropped request. Callers
 *  that act destructively must use refreshSessionDetailed instead. */
export async function refreshSession(): Promise<RefreshSessionResult | null> {
  const outcome = await refreshSessionOutcome();
  return outcome.kind === "session" ? outcome.body : null;
}

export type { RefreshOutcome };

/** Same single-flight refresh, but preserving WHY it produced no session.
 *  Only "rejected" is the server's authoritative "this session is over"; a
 *  caller must never sign the operator out on "unavailable". */
export function refreshSessionDetailed(): Promise<RefreshOutcome> {
  return refreshSessionOutcome();
}

/** The current authenticated-session epoch. Callers that stage a token and
 *  then await a follow-up request compare this before reacting to a failure:
 *  a newer sign-in owns the teardown, so the loser must not run one. */
export function authSessionEpochValue(): number {
  return authSessionEpoch;
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
  // The OpenAPI document exposes the two unauthenticated service probes at
  // the origin root; every application endpoint remains under /api/. Keep
  // this allowlist exact so the generated probe clients work without
  // weakening the same-origin credential boundary for arbitrary root paths.
  const isAllowedPath =
    (path.startsWith("/api/") && parsed.pathname.startsWith("/api/")) ||
    rawPathname === "/healthz" ||
    rawPathname === "/readyz";
  if (
    !isAllowedPath ||
    parsed.origin !== validationOrigin ||
    parsed.pathname !== rawPathname ||
    parsed.hash !== "" ||
    rawPathname.includes("%")
  ) {
    const error = new Error("API requests must use an approved same-origin backend path.");
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

/** The epoch asserts guard the RESOLVED path, but a transport-layer rejection
 *  (dropped connection, DNS failure, timeout abort) escapes the await before
 *  the next assert ever runs. A superseded caller then sees a bare TypeError
 *  and cannot tell "my request failed" from "a newer session replaced mine" —
 *  and callers such as AuthProvider.establishSession react to the former by
 *  tearing down the session, which by then belongs to somebody else. Re-check
 *  the epoch on the failure path too: a session change outranks the transport
 *  error, because the request is no longer this caller's to report on. */
async function runScopedToAuthSession<T>(
  operation: () => Promise<T>,
  expectedEpoch: number,
): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    assertAuthSession(expectedEpoch);
    throw error;
  }
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

/** Mutations carry no caller signal (TanStack Query supplies one to queries
 *  only), so before this bound they could stay pending forever behind a wedged
 *  proxy or captive portal. Any dialog that disables its own dismissal while a
 *  write is pending then became genuinely unclosable. Deliberately far longer
 *  than the refresh budget: a herd-wide health event or dispense legitimately
 *  takes seconds, and abandoning a write that may already have committed is
 *  worse than waiting. */
const REQUEST_TIMEOUT_MS = 60_000;
const REFRESH_COOKIE_POST_ROUTES = new Set([
  "/api/auth/register",
  "/api/auth/login",
  "/api/auth/refresh",
  "/api/auth/logout",
  "/api/auth/change-password",
]);

function isRefreshCookieMutation(path: string, method?: string): boolean {
  const requestPath = path.split(/[?#]/, 1)[0];
  const route =
    requestPath.length > 1 && requestPath.endsWith("/")
      ? requestPath.slice(0, -1)
      : requestPath;
  const verb = (method ?? "GET").toUpperCase();
  return (
    (verb === "POST" && REFRESH_COOKIE_POST_ROUTES.has(route)) ||
    (verb === "DELETE" && route === "/api/auth/account")
  );
}

function isLogoutRoute(path: string, method?: string): boolean {
  const requestPath = path.split(/[?#]/, 1)[0];
  const route =
    requestPath.length > 1 && requestPath.endsWith("/")
      ? requestPath.slice(0, -1)
      : requestPath;
  return (method ?? "GET").toUpperCase() === "POST" && route === "/api/auth/logout";
}

async function rawFetch(
  path: string,
  init: RequestInit = {},
  farmScope: string | null = currentFarmId,
  sessionScope: number = authSessionEpoch,
): Promise<Response> {
  const headers = new Headers(init.headers);
  const requestAccessToken = accessToken;
  if (requestAccessToken) headers.set("Authorization", `Bearer ${requestAccessToken}`);
  if (farmScope) headers.set("X-Farm-Id", farmScope);
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Never override a caller's signal: the idempotency registry treats it as
  // cancellation-ownership identity, and TanStack Query already aborts its own
  // queries on unmount and farm switch. A timeout here aborts, which
  // idempotent-request classifies as non-retryable while RETAINING the logical
  // key, so an explicit retry replays the same Idempotency-Key rather than
  // committing twice.
  const cookieMutation = isRefreshCookieMutation(path, init.method);
  const execute = () => {
    if (cookieMutation && authSessionEpoch !== sessionScope) {
      // AuthProvider intentionally starts logout with the old bearer, then
      // clears local state before a queued fetch gets the lock. Permit exactly
      // that one-token teardown transition. Any installed replacement token,
      // or any additional epoch transition, means this queued request belongs
      // to a superseded session and must never touch the shared cookie jar.
      const intentionalLogoutTeardown =
        isLogoutRoute(path, init.method) &&
        requestAccessToken !== null &&
        accessToken === null &&
        authSessionEpoch === sessionScope + 1;
      if (!intentionalLogoutTeardown) assertAuthSession(sessionScope);
    }
    // Start an internally owned request timeout only after a queued cookie
    // mutation acquires its lock. Otherwise most of its budget could expire
    // while another tab is legitimately finishing the preceding response.
    const signal = init.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS);
    return fetch(path, { ...init, headers, credentials: "include", signal });
  };
  if (!cookieMutation) return execute();
  try {
    return await withAuthCookieLock(
      execute,
      AUTH_COOKIE_MUTATION_LOCK_WAIT_TIMEOUT_MS,
    );
  } catch (error) {
    // Direct auth forms already surface ApiError.detail. Preserve the
    // actionable coordination message instead of misreporting a busy cookie
    // critical section as a generic backend/network failure.
    if (error instanceof AuthCookieCoordinationError) {
      throw new ApiError(429, error.message);
    }
    throw error;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const sessionScope = authSessionEpoch;
  const resp = await apiResponse(path, init);
  assertAuthSession(sessionScope);
  if (resp.status === 204) return undefined as T;
  // Response bodies are asynchronous streams. The actor can change after
  // headers arrive but before JSON parsing finishes, so guard both edges —
  // including the edge where the stream itself fails.
  const data = await runScopedToAuthSession(
    () => resp.json() as Promise<T>,
    sessionScope,
  );
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
  let resp = await runScopedToAuthSession(
    () => rawFetch(path, init, farmScope, sessionScope),
    sessionScope,
  );
  assertAuthSession(sessionScope);
  let responseSessionScope = sessionScope;
  const requestPath = path.split("?", 1)[0];
  const authRoute =
    requestPath.length > 1 && requestPath.endsWith("/")
      ? requestPath.slice(0, -1)
      : requestPath;
  if (resp.status === 401 && !NO_REFRESH_PATHS.has(authRoute)) {
    const outcome = await refreshSessionOutcome();
    assertAuthSession(sessionScope);
    if (outcome.kind === "session") {
      resp = await runScopedToAuthSession(
        () => rawFetch(path, init, farmScope, sessionScope),
        sessionScope,
      );
      assertAuthSession(sessionScope);
    } else if (outcome.kind === "rejected") {
      // The server answered: this session is over.
      setAccessToken(null);
      // The request is allowed to report its original 401 after performing
      // its own teardown, but not after a subsequent login has installed a
      // different actor while that error body is still streaming.
      responseSessionScope = authSessionEpoch;
      onAuthFailure?.();
    }
    // "unavailable": we never reached the server, so the still-installed token
    // and the httpOnly refresh cookie stay put and the caller sees the
    // original 401 as a transient error. A later request (or the user's next
    // action) retries once the network is back, instead of the operator being
    // thrown to /login mid-task with the query cache and farm selection gone.
  }
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      /* non-JSON error body */
    }
    // A failed refresh deliberately ended this request's own session, so its
    // post-clear epoch is the valid boundary for the original 401. A newer
    // login during delayed body parsing still supersedes it.
    assertAuthSession(responseSessionScope);
    throw new ApiError(resp.status, extractDetail(body, resp.statusText));
  }
  // Fully consume and validate protected successful JSON bodies before their
  // logical request is marked complete. A connection that drops after headers
  // or a proxy-corrupted JSON body is still ambiguous and must retry with the
  // same key rather than discarding it before apiFetch parses the response.
  if (!bufferSuccess || resp.status === 204) return resp;
  await resp.clone().json();
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
    assertRequestScope: () => assertAuthSession(sessionScope),
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
  const data =
    resp.status === 204
      ? undefined
      : await runScopedToAuthSession(() => resp.json(), sessionScope);
  assertAuthSession(sessionScope);
  return { data: data as T, status: resp.status, headers: resp.headers };
}
