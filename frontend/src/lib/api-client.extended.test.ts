/**
 * Extended unit tests for the central fetch wrapper, complementing
 * api-client.test.ts: header injection rules (Authorization / X-Farm-Id /
 * Content-Type / credentials), 204 + JSON success handling, ApiError
 * detail-extraction edge cases, and the refresh-retry paths not covered
 * there (retry still 401, refresh network failure, dedupe reset, refresh
 * request shape). Global fetch is stubbed directly, same as the base file.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  refreshSession,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "./api-client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: `Status ${status}`,
    headers: { "Content-Type": "application/json" },
  });
}

/** Authorization header of the most recent protected request. */
function lastAuthorizationHeader(mock: ReturnType<typeof vi.fn>): string | null {
  for (let index = mock.mock.calls.length - 1; index >= 0; index -= 1) {
    const [input, init] = mock.mock.calls[index] as [unknown, RequestInit | undefined];
    if (String(input) === "/api/auth/refresh") continue;
    return new Headers(init?.headers).get("Authorization");
  }
  return null;
}

function refreshPayload(accessToken: string, actorId = 1) {
  return {
    access_token: accessToken,
    user: {
      id: actorId,
      email: `actor-${actorId}@example.test`,
      name: null,
    },
  };
}

/** Lets each microtask-scheduled refreshPromise reset (setTimeout 0) flush. */
function flushMacrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** Awaits a rejecting apiFetch and returns the ApiError, typed. */
async function catchApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("expected apiFetch to reject");
}

describe("apiFetch header injection", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("omits the Authorization header when no token is stored", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBeNull();
  });

  it("sends the stored token as a Bearer Authorization header", async () => {
    setAccessToken("token-abc");
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-abc");
  });

  it("omits the X-Farm-Id header when no farm is selected", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("X-Farm-Id")).toBeNull();
  });

  it("sends X-Farm-Id with the selected farm", async () => {
    setCurrentFarmId("42");
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("X-Farm-Id")).toBe("42");
  });

  it("defaults Content-Type to application/json when a body is sent", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals", {
      method: "POST",
      body: JSON.stringify({ tag_number: "G-001" }),
    });

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("preserves a caller-provided Content-Type", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/uploads", {
      method: "POST",
      body: "raw",
      headers: { "Content-Type": "text/csv" },
    });

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("text/csv");
  });

  it("does not set Content-Type on bodyless requests", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("still identifies an explicitly empty request body as JSON", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/uploads", { method: "POST", body: "" });

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("leaves Content-Type unset for native non-string request bodies", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/uploads", {
      method: "POST",
      body: new URLSearchParams({ search: "ear tag" }),
    });

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("always sends cookies (credentials: include)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));

    await apiFetch("/api/animals");

    expect(fetchMock.mock.calls[0][1]?.credentials).toBe("include");
  });

  it.each([
    "/api/private/../auth/logout",
    "/api/private/%2e%2e/auth/logout",
    "/api/private/%252e%252e/auth/logout",
    "/api\\auth/logout",
    "/api/animals#unexpected-fragment",
  ])("rejects non-canonical API path %s before fetch", async (path) => {
    const error = await catchApiError(apiFetch(path, { method: "POST" }));

    expect(error.name).toBe("UnsafeApiPathError");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("apiFetch success handling", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("parses and returns the JSON body of a 200 response", async () => {
    const payload = [{ id: 1, tag_number: "G-001" }];
    fetchMock.mockResolvedValueOnce(jsonResponse(200, payload));

    const result = await apiFetch<typeof payload>("/api/animals");

    expect(result).toEqual(payload);
  });

  it("returns undefined for a 204 No Content response", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    const result = await apiFetch<string>("/api/animals/1", { method: "DELETE" });

    expect(result).toBeUndefined();
  });
});

describe("apiFetch ApiError detail extraction", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("falls back to the HTTP status text when the error body has no detail", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ other: "x" }), {
        status: 500,
        statusText: "Internal Server Error",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(500);
    expect(err.detail).toBe("Internal Server Error");
  });

  it("falls back to the status text when the error body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("<html>Bad Gateway</html>", {
        status: 502,
        statusText: "Bad Gateway",
        headers: { "Content-Type": "text/html" },
      }),
    );

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.detail).toBe("Bad Gateway");
  });

  it("stringifies a non-string, non-array detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(400, { detail: 42 }));

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.detail).toBe("42");
  });

  it("stringifies non-object entries in a detail array", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, { detail: ["plain string error", 7] }),
    );

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.detail).toBe(
      "The server rejected these values (plain string error; 7). Check the entered data and try again.",
    );
  });

  it("stringifies array entries that lack a msg field", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, { detail: [{ loc: ["body", "x"] }] }),
    );

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.detail).toBe(
      "The server rejected these values ([object Object]). Check the entered data and try again.",
    );
  });

  it("exposes status and detail and behaves as an Error", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { detail: "Forbidden" }));

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Forbidden");
    expect(err.detail).toBe("Forbidden");
    expect(err.status).toBe(403);
  });
});

describe("apiFetch refresh-retry edge cases", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("old-token");
    setCurrentFarmId("1");
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("posts to /api/auth/refresh with cookies included", async () => {
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      return jsonResponse(401, { detail: "Expired" });
    });

    await catchApiError(apiFetch("/api/animals")).catch(() => undefined);
    // First call 401 → refresh succeeds → retry 401 → throws. Just inspect the refresh call.
    const refreshCall = fetchMock.mock.calls.find(
      ([input]) => String(input) === "/api/auth/refresh",
    );
    expect(refreshCall).toBeDefined();
    expect(refreshCall?.[1]?.method).toBe("POST");
    expect(refreshCall?.[1]?.credentials).toBe("include");
  });

  it("rejects a non-2xx refresh even when its body looks successful", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, refreshPayload("must-not-be-installed")),
    );

    await expect(refreshSession()).resolves.toBeNull();

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("/api/buckets");
    expect(
      new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization"),
    ).toBe("Bearer old-token");
  });

  it("falls back when navigator is unavailable", async () => {
    vi.stubGlobal("navigator", undefined);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, refreshPayload("worker-token")),
    );

    await expect(refreshSession()).resolves.toEqual(refreshPayload("worker-token"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("uses a same-origin Web Lock to coordinate refresh across tabs", async () => {
    const lockRequest = vi.fn(
      async (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) => callback(),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        access_token: "coordinated-token",
        user: { id: 1, email: "user@farm.in", name: null },
      }),
    );

    const result = await refreshSession();

    expect(result?.access_token).toBe("coordinated-token");
    expect(lockRequest).toHaveBeenCalledTimes(1);
    expect(lockRequest.mock.calls[0][0]).toBe("goatfarm-auth-refresh");
    // The queue wait is bounded, so one wedged tab cannot stall the rest.
    expect(lockRequest.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  });

  it("fails transiently instead of bypassing a timed-out auth-cookie lock", async () => {
    // A tab holding the lock behind a black-holed connection never releases
    // it; the waiter must settle, but an uncoordinated refresh could race a
    // login/logout Set-Cookie response and overwrite the newer cookie.
    const lockRequest = vi.fn(
      (_name: string, options: LockOptions) =>
        // Never grants: the callback is deliberately ignored.
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    vi.useFakeTimers();
    try {
      const pending = refreshSession();
      await vi.advanceTimersByTimeAsync(62_000);
      const result = await pending;

      expect(result).toBeNull();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not bypass Web Locks when the request rejects before grant", async () => {
    const lockError = new DOMException("locks unavailable", "NotSupportedError");
    const lockRequest = vi.fn().mockRejectedValue(lockError);
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });

    const result = await refreshSession();

    expect(result).toBeNull();
    expect(lockRequest).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not consume the refresh cookie after a queued caller changes session", async () => {
    let grantLock: (() => void) | undefined;
    const lockRequest = vi.fn(
      (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) =>
        new Promise<unknown>((resolve, reject) => {
          grantLock = () => {
            void callback().then(resolve, reject);
          };
        }),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });

    const pending = refreshSession();
    setAccessToken("replacement-login-token", 2);
    grantLock?.();

    await expect(pending).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears the request timeout after a successful refresh", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, refreshPayload("fresh-token")),
    );

    vi.useFakeTimers();
    try {
      await expect(refreshSession()).resolves.toEqual(refreshPayload("fresh-token"));
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      expect(requestSignal.aborted).toBe(false);

      await vi.advanceTimersByTimeAsync(10_000);

      expect(requestSignal.aborted).toBe(false);
      await vi.runOnlyPendingTimersAsync();
    } finally {
      vi.useRealTimers();
    }
  });

  it("aborts a refresh request that exceeds its network timeout", async () => {
    fetchMock.mockImplementationOnce(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal as AbortSignal;
          signal.addEventListener("abort", () => {
            reject(new DOMException("timed out", "AbortError"));
          });
        }),
    );

    vi.useFakeTimers();
    try {
      const pending = refreshSession();
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      expect(requestSignal.aborted).toBe(false);

      await vi.advanceTimersByTimeAsync(10_000);

      await expect(pending).resolves.toBeNull();
      expect(requestSignal.aborted).toBe(true);
      await vi.runOnlyPendingTimersAsync();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ["a null body", null],
    ["a missing token", { user: refreshPayload("ignored").user }],
    ["an empty token", refreshPayload("")],
    ["a token containing whitespace", refreshPayload("not a valid token")],
    ["a missing user", { access_token: "new-token" }],
    [
      "a non-integer user id",
      { ...refreshPayload("new-token"), user: { id: 1.5, email: "a@b.test", name: null } },
    ],
    [
      "a non-positive user id",
      { ...refreshPayload("new-token"), user: { id: 0, email: "a@b.test", name: null } },
    ],
    [
      "an unsafe user id",
      {
        ...refreshPayload("new-token"),
        user: { id: Number.MAX_SAFE_INTEGER + 1, email: "a@b.test", name: null },
      },
    ],
    [
      "a non-string email",
      { ...refreshPayload("new-token"), user: { id: 1, email: 7, name: null } },
    ],
    [
      "a missing name",
      { access_token: "new-token", user: { id: 1, email: "a@b.test" } },
    ],
    [
      "a non-string name",
      { ...refreshPayload("new-token"), user: { id: 1, email: "a@b.test", name: 7 } },
    ],
  ])("treats a 200 refresh with %s as an auth failure", async (_label, body) => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    fetchMock.mockImplementation(async (input) =>
      String(input) === "/api/auth/refresh"
        ? jsonResponse(200, body)
        : jsonResponse(401, { detail: "Expired" }),
    );

    const error = await catchApiError(apiFetch("/api/animals"));

    expect(error).toMatchObject({ status: 401, detail: "Expired" });
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input) === "/api/animals"),
    ).toHaveLength(1);

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("/api/buckets");
    expect(
      new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization"),
    ).toBeNull();
  });

  it("throws the retry's 401 without looping when the retry is still unauthorized", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      return jsonResponse(401, { detail: "Still expired" });
    });

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(401);
    expect(err.detail).toBe("Still expired");
    // Exactly: initial call + one refresh + one retry. No further attempts.
    const calls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(calls.filter((u) => u === "/api/animals")).toHaveLength(2);
    expect(calls.filter((u) => u === "/api/auth/refresh")).toHaveLength(1);
    // Refresh succeeded, so the auth-failure handler stays silent.
    expect(onAuthFailure).not.toHaveBeenCalled();
  });

  it("keeps the session when refresh cannot reach the server", async () => {
    // A transport failure is not the server saying the session ended. Tearing
    // down here logged the operator out of a session still valid for the rest
    // of the refresh cookie's 14-day life — losing the query cache, the farm
    // selection and any unsaved dialog state — on one dropped request.
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    setAccessToken("live-token", 7);

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") throw new Error("network down");
      return jsonResponse(401, { detail: "Expired" });
    });

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(401);
    expect(onAuthFailure).not.toHaveBeenCalled();
    // The bearer is still installed: a later request once the network returns
    // still authenticates, so no re-login is needed.
    expect(lastAuthorizationHeader(fetchMock)).toBe("Bearer live-token");
  });

  it("keeps the session when refresh returns a 5xx", async () => {
    // Same reasoning for a rolling backend deploy: 502/503 is "ask again",
    // not "you are signed out".
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    setAccessToken("live-token", 7);

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") return jsonResponse(503, { detail: "unavailable" });
      return jsonResponse(401, { detail: "Expired" });
    });

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(401);
    expect(onAuthFailure).not.toHaveBeenCalled();
    expect(lastAuthorizationHeader(fetchMock)).toBe("Bearer live-token");
  });

  it("ends the session when refresh is authoritatively rejected", async () => {
    // The other half of the contract: a 401 from /api/auth/refresh IS the
    // server's answer, and must still sign the user out.
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    setAccessToken("live-token", 7);

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") return jsonResponse(401, { detail: "Revoked" });
      return jsonResponse(401, { detail: "Expired" });
    });

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(401);
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
  });

  it("dedupes a refresh shared by three concurrent 401s", async () => {
    const retried = new Set<string>();

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      if (!retried.has(url)) {
        retried.add(url);
        return jsonResponse(401, { detail: "Expired" });
      }
      return jsonResponse(200, { ok: url });
    });

    const results = await Promise.all([
      apiFetch<{ ok: string }>("/api/animals"),
      apiFetch<{ ok: string }>("/api/tasks"),
      apiFetch<{ ok: string }>("/api/buckets"),
    ]);

    expect(results.map((r) => r.ok)).toEqual([
      "/api/animals",
      "/api/tasks",
      "/api/buckets",
    ]);
    const refreshes = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/api/auth/refresh",
    );
    expect(refreshes).toHaveLength(1);
  });

  it("starts a fresh refresh for a 401 after the previous refresh settled", async () => {
    let refreshCount = 0;
    let expiredCalls = 0;

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        refreshCount += 1;
        return jsonResponse(200, refreshPayload(`token-${refreshCount}`));
      }
      expiredCalls += 1;
      if (expiredCalls % 2 === 1) return jsonResponse(401, { detail: "Expired" });
      return jsonResponse(200, { ok: true });
    });

    // First 401 → refresh #1 → retry succeeds.
    await apiFetch("/api/animals");
    await flushMacrotasks();
    // After the dedupe slot resets, a new 401 triggers refresh #2.
    await apiFetch("/api/animals");

    expect(refreshCount).toBe(2);
  });

  it("does not clear the token for a 401 on /api/auth/* paths", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);

    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "No cookie" }));

    await catchApiError(apiFetch("/api/auth/refresh", { method: "POST" }));

    expect(onAuthFailure).not.toHaveBeenCalled();
    // Token survives: the next request still carries it.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("/api/animals");
    const headers = fetchMock.mock.calls[1][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer old-token");
  });

  it.each([
    "/api/auth/login?returnTo=%2Fdashboard",
    "/api/auth/register?invitation=abc",
    "/api/auth/refresh?source=bootstrap",
    "/api/auth/logout?all=true",
    "/api/auth/login/",
    "/api/auth/register/",
    "/api/auth/refresh/",
    "/api/auth/logout/",
  ])("does not refresh a terminal auth path spelling %s", async (path) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "Unauthorized" }));

    const error = await catchApiError(apiFetch(path, { method: "POST" }));

    expect(error.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe(path);
  });

  it("forwards method and body on the retried request after a refresh", async () => {
    const retried = new Set<string>();

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      if (!retried.has(url)) {
        retried.add(url);
        return jsonResponse(401, { detail: "Expired" });
      }
      return jsonResponse(200, { id: 1 });
    });

    const body = JSON.stringify({ tag_number: "G-009", sex: "FEMALE" });
    await apiFetch("/api/animals", { method: "POST", body });

    const animalCalls = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/api/animals",
    );
    expect(animalCalls).toHaveLength(2);
    for (const [, init] of animalCalls) {
      expect(init?.method).toBe("POST");
      expect(init?.body).toBe(body);
    }
  });
});

describe("apiFetch token semantics after refresh", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("old-token");
    setCurrentFarmId("1");
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("remembers the actor established by an opaque refresh token", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, refreshPayload("opaque-one", 1)))
      .mockResolvedValueOnce(jsonResponse(200, refreshPayload("opaque-two", 2)));

    await expect(refreshSession()).resolves.toEqual(
      refreshPayload("opaque-one", 1),
    );
    await flushMacrotasks();
    await expect(refreshSession()).resolves.toBeNull();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
  });

  it("uses the refreshed token for subsequent unrelated requests", async () => {
    const retried = new Set<string>();
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      if (url === "/api/animals" && !retried.has(url)) {
        retried.add(url);
        return jsonResponse(401, { detail: "Expired" });
      }
      return jsonResponse(200, {});
    });

    await apiFetch("/api/animals");
    await flushMacrotasks();
    await apiFetch("/api/tasks");

    const tasksInit = fetchMock.mock.calls.find(
      ([input]) => String(input) === "/api/tasks",
    )?.[1];
    expect((tasksInit?.headers as Headers).get("Authorization")).toBe(
      "Bearer new-token",
    );
  });

  it("keeps X-Farm-Id on the retried request after a refresh", async () => {
    let retried = false;
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      if (!retried) {
        retried = true;
        return jsonResponse(401, { detail: "Expired" });
      }
      return jsonResponse(200, {});
    });

    await apiFetch("/api/animals");

    const animalCalls = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/api/animals",
    );
    for (const [, init] of animalCalls) {
      expect((init?.headers as Headers).get("X-Farm-Id")).toBe("1");
    }
  });

  it("does not attempt a refresh for non-401 errors", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { detail: "Forbidden" }));

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not attempt a refresh for 500 errors", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, { detail: "boom" }));

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.status).toBe(500);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes and retries a 401 on a POST request too", async () => {
    let retried = false;
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, refreshPayload("new-token"));
      }
      if (!retried) {
        retried = true;
        return jsonResponse(401, { detail: "Expired" });
      }
      return jsonResponse(201, { id: 7 });
    });

    const result = await apiFetch<{ id: number }>("/api/animals", {
      method: "POST",
      body: JSON.stringify({ tag_number: "G-007" }),
    });

    expect(result).toEqual({ id: 7 });
    const calls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(calls.filter((u) => u === "/api/auth/refresh")).toHaveLength(1);
  });

  it("parses a 201 Created body like any other success", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: 3, name: "Shed A" }));

    const result = await apiFetch<{ id: number }>("/api/buckets", {
      method: "POST",
      body: "{}",
    });

    expect(result).toEqual({ id: 3, name: "Shed A" });
  });
});

describe("apiFetch detail extraction — remaining shapes", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("yields an empty detail for an empty validation-error array", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(422, { detail: [] }));

    const err = await catchApiError(apiFetch("/api/animals"));

    // Empty specifics still get the plain-language 422 sentence.
    expect(err.detail).toBe(
      "The server rejected these values. Check the entered data and try again.",
    );
    expect(err.status).toBe(422);
  });

  it("stringifies a null detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(400, { detail: null }));

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err.detail).toBe("null");
  });
});
