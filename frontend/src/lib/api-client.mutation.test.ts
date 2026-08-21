import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  apiFetchEnvelope,
  authSessionEpochValue,
  farmScopeEpochValue,
  refreshSessionDetailed,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "./api-client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Test": "present" },
  });
}

function tokenForActor(subject: string): string {
  const payload = globalThis
    .btoa(JSON.stringify({ sub: subject }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `eyJhbGciOiJSUzI1NiJ9.${payload}.signature`;
}

async function flushRefreshSlot(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected request to reject");
}

describe("api-client mutation boundaries", () => {
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
    await flushRefreshSlot();
  });

  it("advances farm ownership only when the selected farm actually changes", () => {
    const initial = farmScopeEpochValue();

    setCurrentFarmId("17");
    const selected = farmScopeEpochValue();
    setCurrentFarmId("17");
    const repeated = farmScopeEpochValue();
    setCurrentFarmId(null);
    const cleared = farmScopeEpochValue();

    expect(selected).toBeGreaterThan(initial);
    expect(repeated).toBe(selected);
    expect(cleared).toBeGreaterThan(selected);
  });

  it("advances auth ownership only when the installed token changes", () => {
    const initial = authSessionEpochValue();

    setAccessToken("token-a", 1);
    const installed = authSessionEpochValue();
    setAccessToken("token-a", 1);
    const repeated = authSessionEpochValue();
    setAccessToken("token-b", 1);
    const replaced = authSessionEpochValue();

    expect(installed).toBeGreaterThan(initial);
    expect(repeated).toBe(installed);
    expect(replaced).toBeGreaterThan(installed);
  });

  it.each([
    ["a primitive body", 7],
    ["a null body", null],
    ["a null user", { access_token: "token", user: null }],
    [
      "a string user id",
      { access_token: "token", user: { id: "1", email: "a@test", name: null } },
    ],
    [
      "a zero user id",
      { access_token: "token", user: { id: 0, email: "a@test", name: null } },
    ],
    [
      "an unsafe user id",
      {
        access_token: "token",
        user: { id: Number.MAX_SAFE_INTEGER + 1, email: "a@test", name: null },
      },
    ],
    [
      "a non-string email",
      { access_token: "token", user: { id: 1, email: 7, name: null } },
    ],
    [
      "a non-string name",
      { access_token: "token", user: { id: 1, email: "a@test", name: 7 } },
    ],
    [
      "a whitespace token",
      { access_token: "two words", user: { id: 1, email: "a@test", name: null } },
    ],
  ])("rejects a successful refresh containing %s", async (_label, body) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, body));

    await expect(refreshSessionDetailed()).resolves.toEqual({ kind: "rejected" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("decodes a padded JWT subject before accepting a cross-account refresh", async () => {
    const originalAtob = globalThis.atob.bind(globalThis);
    vi.stubGlobal("atob", (value: string) => {
      if (value.length % 4 !== 0) throw new DOMException("Invalid base64");
      return originalAtob(value);
    });
    const oldToken = tokenForActor("101");
    const replacementToken = tokenForActor("202");
    expect(oldToken.split(".")[1].length % 4).not.toBe(0);
    setAccessToken(oldToken);
    const failure = vi.fn();
    setOnAuthFailure(failure);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: replacementToken,
          user: { id: 202, email: "actor-202@test", name: null },
        }),
      );

    const error = await rejectionOf(apiFetch("/api/animals"));

    expect(error).toMatchObject({ name: "AuthSessionChangedError" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(failure).toHaveBeenCalledTimes(1);
  });

  it("updates the actor fallback when the same opaque token is explicitly rebound", async () => {
    setAccessToken("opaque-token", 101);
    setAccessToken("opaque-token", 202);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: "another-opaque-token",
          user: { id: 101, email: "actor-101@test", name: null },
        }),
      );

    const error = await rejectionOf(apiFetch("/api/animals"));

    expect(error).toMatchObject({ name: "AuthSessionChangedError" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("treats a token without a JWT payload as opaque instead of throwing", () => {
    expect(() => setAccessToken("opaque-without-dots")).not.toThrow();
  });

  it("does not invent an actor identity from a non-scalar JWT subject", async () => {
    const payload = globalThis
      .btoa(JSON.stringify({ sub: { unexpected: true } }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    setAccessToken(`header.${payload}.signature`);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: "replacement-opaque-token",
          user: { id: 202, email: "actor-202@test", name: null },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

    await expect(apiFetch("/api/animals")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("removes an auth-failure registration idempotently without disturbing peers", async () => {
    const older = vi.fn();
    const newer = vi.fn();
    const removeOlder = setOnAuthFailure(older);
    const removeNewer = setOnAuthFailure(newer);
    removeOlder();
    removeOlder();
    setAccessToken("expired", 1);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "refresh rejected" }));

    await rejectionOf(apiFetch("/api/animals"));

    expect(older).not.toHaveBeenCalled();
    expect(newer).toHaveBeenCalledTimes(1);
    removeNewer();
  });

  it("clears all auth-failure registrations when explicitly reset", async () => {
    const failure = vi.fn();
    setOnAuthFailure(failure);
    const disposeReset = setOnAuthFailure(null);
    // The reset disposer is intentionally inert. If reset were accidentally
    // registered as another stack entry, disposing it would resurrect the
    // handler that reset was meant to erase.
    disposeReset();
    setAccessToken("expired", 1);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "refresh rejected" }));

    await rejectionOf(apiFetch("/api/animals"));

    expect(failure).not.toHaveBeenCalled();
  });

  it("preserves the operation's own error after a Web Lock was granted", async () => {
    const sentinel = new TypeError("connection dropped after lock grant");
    const lockRequest = vi.fn(
      async (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) => callback(),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    fetchMock.mockRejectedValueOnce(sentinel);

    expect(
      await rejectionOf(apiFetch("/api/auth/login", { method: "POST" })),
    ).toBe(sentinel);
    expect(lockRequest).toHaveBeenCalledTimes(1);
  });

  it("does not grant the logout teardown exception to a replacement token", async () => {
    setAccessToken("old-token", 1);
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
    let releaseBlocker: (() => void) | undefined;
    const blockerGate = new Promise<void>((resolve) => {
      releaseBlocker = resolve;
    });
    const sent: string[] = [];
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      sent.push(path);
      if (path === "/api/auth/login") await blockerGate;
      return new Response(null, { status: 204 });
    });

    const blocker = apiFetch("/api/auth/login", { method: "POST" });
    await vi.waitFor(() => expect(sent).toEqual(["/api/auth/login"]));
    const staleLogout = apiFetch("/api/auth/logout", { method: "POST" });
    setAccessToken("replacement-token", 2);
    releaseBlocker?.();

    await expect(blocker).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    await expect(staleLogout).rejects.toMatchObject({
      name: "AuthSessionChangedError",
    });
    expect(sent).toEqual(["/api/auth/login"]);
  });

  it.each([408, 429, 500])(
    "treats an exact %s refresh response as transient",
    async (status) => {
      const failure = vi.fn();
      setOnAuthFailure(failure);
      setAccessToken("expired", 1);
      fetchMock
        .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
        .mockResolvedValueOnce(
          jsonResponse(status, { detail: "temporarily unavailable" }),
        );

      const error = await rejectionOf(apiFetch("/api/animals"));

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 401, detail: "expired" });
      expect(failure).not.toHaveBeenCalled();
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
      await expect(apiFetch("/api/animals")).resolves.toEqual({ ok: true });
      expect(
        new Headers(fetchMock.mock.calls.at(-1)?.[1]?.headers).get("Authorization"),
      ).toBe("Bearer expired");
    },
  );

  it.each([
    ["/api/auth/login/", "POST"],
    ["/api/auth/register/?from=invite", "post"],
    ["/api/auth/change-password/?reauth=1", "POST"],
    ["/api/auth/account/?confirm=1", "DELETE"],
  ])("serializes the normalized cookie mutation %s", async (path, method) => {
    const lockRequest = vi.fn(
      async (
        _name: string,
        _options: LockOptions,
        callback: () => Promise<unknown>,
      ) => callback(),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await apiFetch(path, { method });

    expect(lockRequest).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["/api/auth/login-extra", "POST"],
    ["/api/auth/login", "GET"],
    ["/api/auth/account", "POST"],
    ["/api/auth/accounting", "DELETE"],
  ])("does not serialize the non-cookie mutation %s %s", async (path, method) => {
    const lockRequest = vi.fn();
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await apiFetch(path, { method });

    expect(lockRequest).not.toHaveBeenCalled();
  });

  it.each([
    "/api",
    "/api/../healthz",
    "/healthz/extra",
    "/readyz/extra",
    "https://goatfarm.invalid/api/animals",
    "//goatfarm.invalid/api/animals",
    "/api/animals#fragment",
    "/api/animals%2F1",
  ])("rejects a path outside the exact same-origin allowlist: %s", async (path) => {
    const error = await rejectionOf(apiFetch(path));
    expect(error).toMatchObject({ name: "UnsafeApiPathError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["/healthz", "/readyz"])(
    "allows the exact unauthenticated service probe %s",
    async (path) => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
      await expect(apiFetch(path)).resolves.toEqual({ ok: true });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("returns the full envelope for a 204 response without parsing a body", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(null, { status: 204, headers: { "X-Test": "present" } }),
    );

    const envelope = await apiFetchEnvelope("/api/animals/1", {
      method: "DELETE",
    });

    expect(envelope).toMatchObject({ data: undefined, status: 204 });
    expect(envelope.headers.get("X-Test")).toBe("present");
  });

  it("does not parse a protected 204 response while validating it for retry safety", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(
      apiFetch("/api/tasks", { method: "POST", body: "{}" }),
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
