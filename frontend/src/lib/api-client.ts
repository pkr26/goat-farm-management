/**
 * Central fetch wrapper: same-origin /api calls (proxied to FastAPI by
 * next.config rewrites), bearer-token auth, X-Farm-Id injection, and a
 * single 401 → /api/auth/refresh retry.
 *
 * The access token lives in memory only (module store) — never localStorage.
 */

let accessToken: string | null = null;
let currentFarmId: string | null = null;
let onAuthFailure: (() => void) | null = null;
let refreshPromise: Promise<boolean> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setCurrentFarmId(farmId: string | null): void {
  currentFarmId = farmId;
}

export function setOnAuthFailure(handler: (() => void) | null): void {
  onAuthFailure = handler;
}

async function tryRefresh(): Promise<boolean> {
  // De-duplicate concurrent refreshes.
  refreshPromise ??= (async () => {
    try {
      const resp = await fetch("/api/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      if (!resp.ok) return false;
      const body = (await resp.json()) as { access_token: string };
      accessToken = body.access_token;
      return true;
    } catch {
      return false;
    } finally {
      const settled = refreshPromise;
      setTimeout(() => {
        if (refreshPromise === settled) refreshPromise = null;
      }, 0);
    }
  })();
  return refreshPromise;
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

async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (currentFarmId) headers.set("X-Farm-Id", currentFarmId);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { ...init, headers, credentials: "include" });
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await apiResponse(path, init);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
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
async function apiResponse(path: string, init: RequestInit = {}): Promise<Response> {
  let resp = await rawFetch(path, init);
  if (resp.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      resp = await rawFetch(path, init);
    } else {
      accessToken = null;
      onAuthFailure?.();
    }
  }
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, extractDetail(body, resp.statusText));
  }
  return resp;
}

/** apiFetch variant that keeps the real status and headers — the orval
 *  custom instance wraps every generated call in this {data, status,
 *  headers} envelope, and pages branch on the status. */
export async function apiFetchEnvelope<T>(
  path: string,
  init: RequestInit = {},
): Promise<{ data: T; status: number; headers: Headers }> {
  const resp = await apiResponse(path, init);
  const data = resp.status === 204 ? undefined : await resp.json();
  return { data: data as T, status: resp.status, headers: resp.headers };
}
