/**
 * Branch tests for the parts of the central fetch wrapper that only a
 * carefully ordered interleaving can reach: the actor-scope bookkeeping inside
 * setAccessToken, the single-flight refresh slot, the Web Locks coordination
 * errors, and the one queued-cookie-mutation teardown exception.
 *
 * Global fetch is stubbed directly (no MSW) so lock ownership, call order and
 * the exact requests that reach the wire can be asserted precisely.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  refreshSessionDetailed,
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

/** A JWT-shaped token whose payload carries `sub` exactly as given — string
 *  subjects and JSON-number subjects are both legal in the wild. */
function actorToken(subject: string | number): string {
  const payload = globalThis
    .btoa(JSON.stringify({ sub: subject }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `eyJhbGciOiJSUzI1NiJ9.${payload}.signature`;
}

function refreshBody(accessToken: string, actorId: number) {
  return {
    access_token: accessToken,
    user: { id: actorId, email: `actor-${actorId}@example.test`, name: null },
  };
}

/** Lets the microtask-scheduled refresh-slot release (setTimeout 0) flush. */
function flushMacrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the request to reject");
}

/** Minimal FIFO Web Locks stand-in: a queued callback starts only once the
 *  previous holder has settled — i.e. once its Set-Cookie has been processed. */
function stubFifoLocks() {
  let tail = Promise.resolve<unknown>(undefined);
  const request = vi.fn(
    (
      _name: string,
      _options: LockOptions,
      callback: () => Promise<unknown>,
    ): Promise<unknown> => {
      const result = tail.then(callback);
      tail = result.then(
        () => undefined,
        () => undefined,
      );
      return result;
    },
  );
  vi.stubGlobal("navigator", { locks: { request } });
  return request;
}

describe("actor scope recorded by setAccessToken", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
  });

  afterEach(async () => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  /** The stored actor scope is deliberately not exported. A refresh describing
   *  `actorId` is the only window onto it: a refresh that names a different
   *  actor than the scope on file is rejected and signs the tab out. */
  function refreshDescribing(actorId: number, token = `opaque-token-${actorId}`) {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, refreshBody(token, actorId)));
    return refreshSessionDetailed();
  }

  it("keeps the attributed actor when bootstrap re-applies the same opaque token", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    setAccessToken("opaque-session-token", 101);
    // AuthProvider re-installs the very token refresh just handed it. An
    // opaque token carries no decodable subject, so this second call must not
    // erase the identity the first call established.
    setAccessToken("opaque-session-token");

    await expect(refreshDescribing(202)).resolves.toEqual({ kind: "rejected" });
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
  });

  it("drops the actor scope when the same token is re-installed unattributed", async () => {
    setAccessToken("opaque-session-token", 111);
    // An explicit null attribution is the caller saying "I cannot vouch for
    // whose token this is"; the earlier identity must not survive it, and it
    // must not be stringified into a scope of its own either.
    setAccessToken("opaque-session-token", null);

    await expect(refreshDescribing(222)).resolves.toMatchObject({ kind: "session" });
  });

  it("resets the actor scope when a different unattributed token replaces it", async () => {
    setAccessToken("opaque-token-a", 131);
    setAccessToken("opaque-token-b");

    // Nothing vouches for token-b, so the next refresh is free to establish
    // whichever actor the server reports.
    await expect(refreshDescribing(232)).resolves.toMatchObject({ kind: "session" });
  });

  it("lets a re-applied JWT's own subject override a stale attribution", async () => {
    // Caller-supplied attribution is the fallback for opaque tokens. Once the
    // token itself carries a subject, re-applying it adopts the signed
    // identity instead of keeping a contradictory earlier attribution.
    setAccessToken(actorToken("555"), 42);
    setAccessToken(actorToken("555"));

    await expect(
      refreshDescribing(555, actorToken("555")),
    ).resolves.toMatchObject({ kind: "session" });
  });

  it("clears an attribution that outlived its token", async () => {
    setAccessToken("opaque-session-token", 161);
    // Signing out may still name the actor whose session is ending; a plain
    // teardown afterwards must leave no attribution behind for the next
    // bootstrap refresh to be measured against.
    setAccessToken(null, 161);
    setAccessToken(null);

    await expect(refreshDescribing(262)).resolves.toMatchObject({ kind: "session" });
  });

  it("reads a numeric JWT subject as the actor scope", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    // Backends legitimately encode `sub` as a JSON number. Ignoring that would
    // leave the token unattributed, and a cross-account refresh would then be
    // installed under the identity the React tree is still displaying.
    setAccessToken(actorToken(171));

    await expect(refreshDescribing(272)).resolves.toEqual({ kind: "rejected" });
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
  });
});

describe("single-flight refresh slot", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
  });

  afterEach(async () => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  it("does not reuse an in-flight refresh that belongs to a superseded session", async () => {
    const firstGate = deferred();
    const secondGate = deferred();
    let refreshCalls = 0;
    fetchMock.mockImplementation(async (input) => {
      if (String(input) !== "/api/auth/refresh") return jsonResponse(200, {});
      refreshCalls += 1;
      if (refreshCalls === 1) {
        await firstGate.promise;
        return jsonResponse(200, refreshBody("token-for-1", 1));
      }
      await secondGate.promise;
      return jsonResponse(200, refreshBody("token-for-2", 2));
    });
    setAccessToken("token-a", 1);

    const first = refreshSessionDetailed();
    await vi.waitFor(() => expect(refreshCalls).toBe(1));
    // A different account signed in while the first refresh was on the wire.
    setAccessToken("token-b", 2);
    const second = refreshSessionDetailed();
    firstGate.resolve();

    // The in-flight answer belongs to the session that just ended; the new one
    // has to ask for itself rather than inherit it. The verdict is a local
    // supersession, not the server's answer, so it reports "unavailable".
    await expect(first).resolves.toEqual({ kind: "unavailable" });
    await vi.waitFor(() => expect(refreshCalls).toBe(2));
    secondGate.resolve();
    await expect(second).resolves.toMatchObject({ kind: "session" });
  });

  it("does not let a settled refresh release a newer session's slot", async () => {
    const firstGate = deferred();
    const secondGate = deferred();
    let refreshCalls = 0;
    fetchMock.mockImplementation(async (input) => {
      if (String(input) !== "/api/auth/refresh") return jsonResponse(200, {});
      refreshCalls += 1;
      if (refreshCalls === 1) {
        await firstGate.promise;
        return jsonResponse(200, refreshBody("token-for-1", 1));
      }
      if (refreshCalls === 2) {
        await secondGate.promise;
        return jsonResponse(200, refreshBody("token-for-2", 2));
      }
      return jsonResponse(200, refreshBody("token-surplus", 2));
    });
    setAccessToken("token-a", 1);

    const first = refreshSessionDetailed();
    await vi.waitFor(() => expect(refreshCalls).toBe(1));
    setAccessToken("token-b", 2);
    const second = refreshSessionDetailed();
    firstGate.resolve();
    // Local supersession: "unavailable", never an authoritative "rejected".
    await expect(first).resolves.toEqual({ kind: "unavailable" });
    // The superseded refresh's own cleanup runs a macrotask later. It owns
    // only its own slot: clearing the newer session's entry would fan a single
    // 401 storm back out into one refresh per caller.
    await flushMacrotasks();

    const joined = refreshSessionDetailed();
    await vi.waitFor(() => expect(refreshCalls).toBe(2));
    secondGate.resolve();
    await expect(second).resolves.toMatchObject({ kind: "session" });
    await expect(joined).resolves.toMatchObject({ kind: "session" });
    expect(refreshCalls).toBe(2);
  });

  it("reports a Web Lock held past the wait bound as a retryable coordination error", async () => {
    // A real lock manager rejects the waiter when its signal aborts. That is
    // the "another tab is still finishing" case, not the "this browser cannot
    // coordinate at all" case, and the operator-facing text differs.
    const lockRequest = vi.fn(
      (_name: string, options: LockOptions): Promise<unknown> =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            reject(new DOMException("The lock request was aborted.", "AbortError"));
          });
        }),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    vi.useFakeTimers();
    try {
      const login = apiFetch("/api/auth/login", { method: "POST" }).then(
        () => null,
        (error: unknown) => error,
      );

      await vi.advanceTimersByTimeAsync(62_000);

      await expect(login).resolves.toMatchObject({
        status: 429,
        detail: "Another authentication change is still finishing. Try again shortly.",
      });
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("propagates a lock failure raised after the grant instead of calling it unavailable", async () => {
    // Once the critical section has been entered, a failure is no longer the
    // bounded "try again shortly" condition. Collapsing it into "unavailable"
    // would hide a genuine defect behind an indefinite silent retry.
    const crash = new Error("lock manager failed after granting");
    const lockRequest = vi.fn(
      async (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ): Promise<unknown> => {
        await callback();
        throw crash;
      },
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, refreshBody("fresh-token", 1)),
    );

    await expect(refreshSessionDetailed()).rejects.toBe(crash);
  });

  it("disarms the bounded-wait timer once a cookie mutation takes its turn", async () => {
    // The waiter's rejection timer must not outlive the race it bounds: a
    // leaked 62s timer per auth mutation keeps the tab awake and rejects into
    // an outcome that was decided long ago.
    vi.stubGlobal("navigator", undefined);
    const callerSignal = new AbortController().signal;
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    vi.useFakeTimers();
    try {
      await apiFetch("/api/auth/logout", {
        method: "POST",
        signal: callerSignal,
      });

      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("error detail extraction", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("session-token", 1);
    setCurrentFarmId("1");
  });

  afterEach(async () => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  it.each([
    ["a JSON string", "upstream connect error"],
    ["a JSON number", 502],
    ["a JSON boolean", true],
  ])("falls back to the status text when the error body is %s", async (_label, body) => {
    // A CDN or proxy error page can serialise as a bare JSON scalar. Probing
    // one for a `detail` property throws, which would replace the ApiError the
    // whole UI branches on with an opaque TypeError.
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status: 502,
        statusText: "Bad Gateway",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const error = await rejectionOf(apiFetch("/api/animals"));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 502, detail: "Bad Gateway" });
  });
});

describe("queued cookie mutations across a session teardown", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
  });

  afterEach(async () => {
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  /** Records every request that actually reaches the wire and holds `gatedPath`
   *  open so the next cookie mutation has to queue behind it. */
  function recordingFetch(gatedPath: string, gate: Promise<void>) {
    const sent: string[] = [];
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      sent.push(path);
      if (path === gatedPath) await gate;
      return new Response(null, { status: 204 });
    });
    return sent;
  }

  it("never lets a queued non-logout auth mutation from the old session reach the wire", async () => {
    stubFifoLocks();
    setAccessToken("session-token", 1);
    const holderGate = deferred();
    const sent = recordingFetch("/api/auth/logout", holderGate.promise);

    const teardown = apiFetch<void>("/api/auth/logout", { method: "POST" });
    await vi.waitFor(() => expect(sent).toEqual(["/api/auth/logout"]));
    const queued = apiFetch<void>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: "old", new_password: "new" }),
    });
    // AuthProvider clears local state as soon as the logout is on the wire.
    setAccessToken(null);
    holderGate.resolve();

    await expect(teardown).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    await expect(queued).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    // The password change belonged to the session that just ended. Sending it
    // would hand this signed-out tab a brand new refresh cookie.
    expect(sent).toEqual(["/api/auth/logout"]);
  });

  it.each(["/api/auth/logout", "/api/auth/logout/"])(
    "still delivers the queued teardown logout %s after local state is cleared",
    async (path) => {
      stubFifoLocks();
      setAccessToken("session-token", 1);
      const holderGate = deferred();
      const sent = recordingFetch("/api/auth/login", holderGate.promise);

      const holder = apiFetch<void>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: "owner@example.test", password: "secret" }),
      });
      await vi.waitFor(() => expect(sent).toEqual(["/api/auth/login"]));
      const logout = apiFetch<void>(path, { method: "POST" });
      setAccessToken(null);
      await Promise.resolve();

      // Queued behind the login until its Set-Cookie has been processed.
      expect(sent).toEqual(["/api/auth/login"]);
      holderGate.resolve();
      await expect(holder).rejects.toMatchObject({ name: "AuthSessionChangedError" });
      await expect(logout).rejects.toMatchObject({ name: "AuthSessionChangedError" });
      // The one permitted transition: the server still has to revoke the
      // refresh cookie even though the tab already forgot the session.
      expect(sent).toEqual(["/api/auth/login", path]);
    },
  );

  it("refuses a queued logout once the session has moved on twice", async () => {
    stubFifoLocks();
    setAccessToken("session-token", 1);
    const holderGate = deferred();
    const sent = recordingFetch("/api/auth/login", holderGate.promise);

    const holder = apiFetch<void>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: "owner@example.test", password: "secret" }),
    });
    await vi.waitFor(() => expect(sent).toEqual(["/api/auth/login"]));
    const logout = apiFetch<void>("/api/auth/logout", { method: "POST" });
    // Another sign-in landed and was torn down again before the queued logout
    // got its turn. The exception covers exactly one transition, not a chain:
    // this logout would delete a cookie two sessions newer than itself.
    setAccessToken("replacement-token", 2);
    setAccessToken(null);
    holderGate.resolve();

    await expect(holder).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    await expect(logout).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    expect(sent).toEqual(["/api/auth/login"]);
  });

  it("refuses a queued logout that never carried a bearer token", async () => {
    stubFifoLocks();
    const refreshGate = deferred();
    const blockerGate = deferred();
    const sent: string[] = [];
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      sent.push(path);
      if (path === "/api/auth/refresh") {
        await refreshGate.promise;
        return jsonResponse(200, refreshBody("bootstrapped-token", 9));
      }
      if (path === "/api/auth/register") await blockerGate.promise;
      return new Response(null, { status: 204 });
    });

    // A signed-out tab: bootstrap refresh first, then a stray logout queued
    // while no bearer token exists at all.
    const bootstrap = refreshSessionDetailed();
    await vi.waitFor(() => expect(sent).toEqual(["/api/auth/refresh"]));
    const blocker = apiFetch<void>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email: "owner@example.test", password: "secret" }),
    });
    const logout = apiFetch<void>("/api/auth/logout", { method: "POST" });
    refreshGate.resolve();
    await expect(bootstrap).resolves.toMatchObject({ kind: "session" });
    await vi.waitFor(() =>
      expect(sent).toEqual(["/api/auth/refresh", "/api/auth/register"]),
    );
    // The bootstrapped session is torn down while the logout still waits.
    setAccessToken(null);
    blockerGate.resolve();

    await expect(blocker).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    await expect(logout).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    // This logout predates the session that refresh established, so it is not
    // that session's own teardown and must not delete its cookie.
    expect(sent).toEqual(["/api/auth/refresh", "/api/auth/register"]);
  });
});

describe("refresh payload guards — campaign kills", () => {
  beforeEach(() => {
    stubFifoLocks();
    setAccessToken(actorToken(1));
    setCurrentFarmId(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /** Each malformed refresh body must end the refresh attempt (a rejected
   *  outcome) rather than being accepted as a session. */
  async function expectRejectedRefresh(body: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(200, body)),
    );
    const outcome = await refreshSessionDetailed();
    expect(outcome).toEqual({ kind: "rejected" });
  }

  it("rejects a refresh body whose access token is not a string", async () => {
    await expectRejectedRefresh({
      access_token: 123,
      user: { id: 1, email: "a@example.test", name: null },
    });
  });

  it("rejects a refresh body whose user is malformed", async () => {
    await expectRejectedRefresh({
      access_token: actorToken(1),
      user: { id: "one", email: "a@example.test", name: null },
    });
  });

  it("rejects a refresh body whose user is absent", async () => {
    await expectRejectedRefresh({ access_token: actorToken(1) });
  });

  it("rejects a refresh body whose user is null", async () => {
    await expectRejectedRefresh({ access_token: actorToken(1), user: null });
  });
});

describe("auth-failure registration stack — campaign kills", () => {
  beforeEach(() => {
    stubFifoLocks();
    setAccessToken(actorToken(1));
    setCurrentFarmId(null);
  });

  afterEach(async () => {
    setOnAuthFailure(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    await flushMacrotasks();
  });

  it("restores the previous handler when the newer registration unregisters", async () => {
    const older = vi.fn();
    const newer = vi.fn();
    setOnAuthFailure(older);
    const unregister = setOnAuthFailure(newer);
    unregister();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) =>
        String(input) === "/api/auth/refresh"
          ? jsonResponse(200, { access_token: 7, user: { id: 1, email: "a@b.test", name: null } })
          : jsonResponse(401, { detail: "Expired" }),
      ),
    );

    const error = await rejectionOf(apiFetch("/api/animals"));
    expect(error).toMatchObject({ status: 401 });
    // The newest tree is gone: the still-mounted older handler owns failures.
    expect(older).toHaveBeenCalledTimes(1);
    expect(newer).not.toHaveBeenCalled();
  });
});
