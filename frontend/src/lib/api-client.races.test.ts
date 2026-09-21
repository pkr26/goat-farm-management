/**
 * Regression tests for the race-condition fixes in the central fetch wrapper.
 *
 * Two distinct hazards are pinned here:
 *   1. Unbounded mutations. Requests carried no signal and no timer, so a
 *      wedged proxy left them pending forever — which any "block while
 *      pending" dialog guard then turned into a genuinely unclosable modal.
 *   2. Transport failures escaping the session-epoch asserts. Those asserts sit
 *      after each await, so a rejection skipped them entirely and a superseded
 *      caller could not tell "my request failed" from "a newer session replaced
 *      mine" — and AuthProvider tears the session down on the former.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  apiFetch,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "./api-client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("request timeout (CC-1)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("token-1", 1);
    setCurrentFarmId("1");
  });

  afterEach(() => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("attaches an abort signal to a request that supplies none", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1");

    const signal = fetchMock.mock.calls[0][1]?.signal as AbortSignal | undefined;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal!.aborted).toBe(false);
  });

  it("composes (never replaces) a caller's own signal with the timeout", async () => {
    // The idempotency registry uses the caller signal as cancellation-ownership
    // identity, and TanStack Query aborts its queries through it. Replacing it
    // would break both — but so did DROPPING the timeout for signalled calls
    // (P3, 2026-09-20 audit): the composed signal aborts on EITHER source, so
    // caller cancellation still fires immediately and every request stays
    // bounded.
    const controller = new AbortController();
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1", { signal: controller.signal });

    const wired = fetchMock.mock.calls[0][1]?.signal;
    expect(wired).toBeDefined();
    expect(wired?.aborted).toBe(false);
    // The caller's cancellation still reaches the wire.
    controller.abort();
    expect(wired?.aborted).toBe(true);
  });

  it("bounds an unsignalled request with a finite timeout", async () => {
    // The timeout is a real Node timer that fake timers do not intercept, and
    // waiting it out would take a minute — so pin the contract at its source.
    // Without a bound the request stays pending forever behind a wedged proxy,
    // and every dialog that disables its own dismissal while a write is in
    // flight stays shut with only a reload to escape.
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1");

    expect(timeoutSpy).toHaveBeenCalledTimes(1);
    const [ms] = timeoutSpy.mock.calls[0];
    expect(Number.isFinite(ms)).toBe(true);
    expect(ms).toBeGreaterThan(0);
    // Generous on purpose: a herd-wide health event or dispense legitimately
    // takes seconds, and aborting a write that may already have committed is
    // worse than waiting.
    expect(ms).toBeGreaterThanOrEqual(30_000);
  });

  it("arms the composed timeout for signalled requests too", async () => {
    // The timeout is no longer skipped when the caller owns a signal: it is
    // composed via AbortSignal.any so unbounded signalled requests cannot
    // outlive their budget (P3, 2026-09-20 audit).
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    const controller = new AbortController();
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1", { signal: controller.signal });

    expect(timeoutSpy).toHaveBeenCalled();
  });

  it("propagates an abort as a rejection rather than hanging", async () => {
    const aborted = new Error("The operation was aborted.");
    aborted.name = "AbortError";
    fetchMock.mockRejectedValue(aborted);

    // idempotent-request classifies AbortError as non-retryable while RETAINING
    // the logical key, so an explicit retry replays the same Idempotency-Key
    // instead of committing a second time.
    await expect(apiFetch("/api/animals/1")).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});

describe("refresh-cookie mutation ordering", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("token-1", 1);
    setCurrentFarmId("1");
  });

  afterEach(() => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps a new login behind an older logout until its cookie deletion arrives", async () => {
    // Minimal FIFO Web Locks implementation: callback B cannot start until
    // callback A (and therefore A's fetch response headers) has settled.
    let lockTail = Promise.resolve<unknown>(undefined);
    const lockRequest = vi.fn(
      (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) => {
        const result = lockTail.then(callback);
        lockTail = result.then(
          () => undefined,
          () => undefined,
        );
        return result;
      },
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });

    let releaseLogout!: () => void;
    let markLogoutStarted!: () => void;
    const logoutStarted = new Promise<void>((resolve) => {
      markLogoutStarted = resolve;
    });
    const logoutGate = new Promise<void>((resolve) => {
      releaseLogout = resolve;
    });
    const fetchOrder: string[] = [];
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      fetchOrder.push(path);
      if (path === "/api/auth/logout") {
        markLogoutStarted();
        await logoutGate;
        return new Response(null, { status: 204 });
      }
      return jsonResponse(200, { ok: true });
    });

    const logout = apiFetch<void>("/api/auth/logout", { method: "POST" });
    await logoutStarted;
    const login = apiFetch<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: "worker@example.test", password: "secret" }),
    });
    await Promise.resolve();

    expect(fetchOrder).toEqual(["/api/auth/logout"]);
    releaseLogout();
    await expect(logout).resolves.toBeUndefined();
    await expect(login).resolves.toEqual({ ok: true });
    expect(fetchOrder).toEqual(["/api/auth/logout", "/api/auth/login"]);
    expect(lockRequest).toHaveBeenCalledTimes(2);
    expect(lockRequest.mock.calls.map((call) => call[0])).toEqual([
      "goatfarm-auth-refresh",
      "goatfarm-auth-refresh",
    ]);
  });

  it("keeps local FIFO ordering after an intermediate waiter times out", async () => {
    vi.stubGlobal("navigator", undefined);
    const callerSignal = new AbortController().signal;
    let releaseLogout!: () => void;
    let markLogoutStarted!: () => void;
    const logoutStarted = new Promise<void>((resolve) => {
      markLogoutStarted = resolve;
    });
    const logoutGate = new Promise<void>((resolve) => {
      releaseLogout = resolve;
    });
    const fetchOrder: string[] = [];
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      fetchOrder.push(path);
      if (path === "/api/auth/logout") {
        markLogoutStarted();
        await logoutGate;
      }
      return new Response(null, { status: 204 });
    });

    vi.useFakeTimers();
    try {
      // The caller-owned signal prevents the first network request's ordinary
      // 60s timeout from ending the lock before the 62s queue bound under test.
      const logout = apiFetch<void>("/api/auth/logout", {
        method: "POST",
        signal: callerSignal,
      });
      await logoutStarted;
      const timedOutLogin = apiFetch<void>("/api/auth/login", { method: "POST" })
        .then(
          () => null,
          (error: unknown) => error,
        );

      await vi.advanceTimersByTimeAsync(62_000);
      await expect(timedOutLogin).resolves.toMatchObject({
        status: 429,
        detail: "Another authentication change is still finishing. Try again shortly.",
      });
      expect(fetchOrder).toEqual(["/api/auth/logout"]);

      // Its already-released ticket must not let this later request skip the
      // actual holder. Once the holder resolves, the third request proceeds;
      // the timed-out middle request is never sent.
      const registration = apiFetch<void>("/api/auth/register", { method: "POST" });
      await Promise.resolve();
      expect(fetchOrder).toEqual(["/api/auth/logout"]);
      releaseLogout();
      await expect(logout).resolves.toBeUndefined();
      await expect(registration).resolves.toBeUndefined();
      expect(fetchOrder).toEqual(["/api/auth/logout", "/api/auth/register"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("drops a queued cookie mutation when its authenticated session is superseded", async () => {
    let lockTail = Promise.resolve<unknown>(undefined);
    const lockRequest = vi.fn(
      (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) => {
        const result = lockTail.then(callback);
        lockTail = result.then(
          () => undefined,
          () => undefined,
        );
        return result;
      },
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    let releaseLogin!: () => void;
    let markLoginStarted!: () => void;
    const loginStarted = new Promise<void>((resolve) => {
      markLoginStarted = resolve;
    });
    const loginGate = new Promise<void>((resolve) => {
      releaseLogin = resolve;
    });
    const fetchOrder: string[] = [];
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      fetchOrder.push(path);
      if (path === "/api/auth/login") {
        markLoginStarted();
        await loginGate;
      }
      return new Response(null, { status: 204 });
    });

    const blocker = apiFetch<void>("/api/auth/login", { method: "POST" });
    await loginStarted;
    const staleChange = apiFetch<void>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: "old", new_password: "new" }),
    });
    setAccessToken("replacement-session", 2);
    releaseLogin();

    await expect(blocker).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    await expect(staleChange).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    expect(fetchOrder).toEqual(["/api/auth/login"]);
  });

  it("surfaces a pre-grant Web Lock failure without sending an uncoordinated login", async () => {
    const lockRequest = vi
      .fn()
      .mockRejectedValue(new DOMException("locks unavailable", "NotSupportedError"));
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });

    await expect(
      apiFetch<void>("/api/auth/login", { method: "POST" }),
    ).rejects.toMatchObject({
      status: 429,
      detail: "Secure authentication coordination is temporarily unavailable. Try again shortly.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["register", "/api/auth/register", "POST"],
    ["login", "/api/auth/login", "POST"],
    ["refresh", "/api/auth/refresh", "POST"],
    ["logout", "/api/auth/logout", "POST"],
    ["password change", "/api/auth/change-password", "POST"],
    ["account deletion", "/api/auth/account", "DELETE"],
  ])("serializes %s through the shared cookie lock", async (_label, path, method) => {
    const lockRequest = vi.fn(
      async (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) => callback(),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await apiFetch<void>(path, { method });

    expect(lockRequest).toHaveBeenCalledTimes(1);
    expect(lockRequest.mock.calls[0][0]).toBe("goatfarm-auth-refresh");
  });
});

describe("transport failures and the session epoch (F5)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("token-1", 1);
    setCurrentFarmId("1");
  });

  afterEach(() => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports a transport failure as AuthSessionChangedError once a newer session took over", async () => {
    // The whole point: AuthProvider.establishSession only spares a superseded
    // session when it sees this error name. Before the fix a dropped
    // connection surfaced as a bare TypeError, and the losing bootstrap call
    // ran clearSession() — destroying the session the user had just signed
    // into.
    fetchMock.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          // A newer sign-in installs its token while this request is on the
          // wire, then the connection drops.
          setTimeout(() => {
            setAccessToken("token-2", 2);
            reject(new TypeError("Failed to fetch"));
          }, 0);
        }),
    );

    await expect(apiFetch("/api/auth/farms")).rejects.toMatchObject({
      name: "AuthSessionChangedError",
    });
  });

  it("still surfaces the real transport error when the session did not change", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(apiFetch("/api/auth/farms")).rejects.toThrow("Failed to fetch");
  });

  it("reports a broken response body as AuthSessionChangedError once a newer session took over", async () => {
    // Bodies are streams: the actor can change after headers arrive but before
    // parsing finishes, and a stream failure skips the post-await assert too.
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      headers: new Headers(),
      json: () =>
        new Promise((_resolve, reject) => {
          setTimeout(() => {
            setAccessToken("token-2", 2);
            reject(new TypeError("network error while reading body"));
          }, 0);
        }),
    } as unknown as Response);

    await expect(apiFetch("/api/auth/farms")).rejects.toMatchObject({
      name: "AuthSessionChangedError",
    });
  });

  it("does not deliver an old forced-logout error after a newer login takes over", async () => {
    let releaseErrorBody!: (body: unknown) => void;
    let errorBodyStarted!: () => void;
    const bodyStarted = new Promise<void>((resolve) => {
      errorBodyStarted = resolve;
    });
    const expiredResponse = {
      status: 401,
      statusText: "Unauthorized",
      ok: false,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: () => {
        errorBodyStarted();
        return new Promise<unknown>((resolve) => {
          releaseErrorBody = resolve;
        });
      },
    } as Response;
    fetchMock.mockImplementation(async (input) =>
      String(input) === "/api/auth/refresh"
        ? jsonResponse(401, { detail: "Session expired" })
        : expiredResponse,
    );
    setOnAuthFailure(vi.fn());

    const oldRequest = apiFetch("/api/animals");
    await bodyStarted;
    // The rejected refresh already cleared actor one. A fresh login can now
    // install actor two while the old 401 body is still streaming.
    setAccessToken("actor-two-token", 2);
    releaseErrorBody({ detail: "Expired actor-one request" });

    await expect(oldRequest).rejects.toMatchObject({
      name: "AuthSessionChangedError",
    });
  });

  it("does not let an older provider cleanup remove a newer auth-failure handler", async () => {
    const olderHandler = vi.fn();
    const newerHandler = vi.fn();
    const cleanupOlder = setOnAuthFailure(olderHandler);
    setOnAuthFailure(newerHandler);
    cleanupOlder();
    fetchMock.mockImplementation(async (input) =>
      String(input) === "/api/auth/refresh"
        ? jsonResponse(401, { detail: "No session" })
        : jsonResponse(401, { detail: "Expired" }),
    );

    await apiFetch("/api/animals").catch(() => undefined);

    expect(olderHandler).not.toHaveBeenCalled();
    expect(newerHandler).toHaveBeenCalledTimes(1);
  });

  it("restores an older mounted provider when the overlapping newer provider unmounts", async () => {
    const olderHandler = vi.fn();
    const newerHandler = vi.fn();
    const cleanupOlder = setOnAuthFailure(olderHandler);
    const cleanupNewer = setOnAuthFailure(newerHandler);
    cleanupNewer();
    fetchMock.mockImplementation(async (input) =>
      String(input) === "/api/auth/refresh"
        ? jsonResponse(401, { detail: "No session" })
        : jsonResponse(401, { detail: "Expired" }),
    );

    await apiFetch("/api/animals").catch(() => undefined);

    expect(newerHandler).not.toHaveBeenCalled();
    expect(olderHandler).toHaveBeenCalledTimes(1);
    cleanupOlder();
  });
});
