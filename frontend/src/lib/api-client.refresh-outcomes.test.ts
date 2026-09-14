/**
 * Refresh-outcome vocabulary and operator-facing diagnostics.
 *
 * refreshSessionDetailed is the only surface that reports WHY a refresh
 * produced no session, and AuthProvider destroys session state on exactly one
 * of those answers ("rejected") while treating every other answer as a
 * transient blip. The sibling files mostly assert "did a session come back";
 * these tests assert the WHOLE outcome object at every site that produces one,
 * so a collapsed or mistyped verdict cannot pass itself off as the other kind.
 *
 * The second block pins the two error messages that reach the operator when a
 * request is refused before or after the wire. Global fetch is stubbed
 * directly (no MSW), same as the sibling files.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  apiFetch,
  refreshSessionDetailed,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "./api-client";

const UNSAFE_PATH_MESSAGE =
  "API requests must use an approved same-origin backend path.";
const SESSION_CHANGED_MESSAGE =
  "Your authenticated session changed while this request was in progress. " +
  "The request was not replayed.";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: `Status ${status}`,
    headers: { "Content-Type": "application/json" },
  });
}

function refreshPayload(accessToken: string, actorId = 1) {
  return {
    access_token: accessToken,
    user: { id: actorId, email: `actor-${actorId}@example.test`, name: null },
  };
}

/** An unsigned JWT — the client only ever reads the unverified `sub` claim. */
function jwtWithClaims(claims: Record<string, unknown>): string {
  const payload = globalThis
    .btoa(JSON.stringify(claims))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${payload}.signature`;
}

/** Lets each microtask-scheduled refreshPromise reset (setTimeout 0) flush. */
function flushMacrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the request to reject");
}

describe("refresh outcome vocabulary", () => {
  const fetchMock = vi.fn<typeof fetch>();

  /** The Authorization header the next protected request would present. */
  async function nextRequestAuthorization(): Promise<string | null> {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    await apiFetch("/api/buckets");
    return new Headers(fetchMock.mock.calls.at(-1)?.[1]?.headers).get(
      "Authorization",
    );
  }

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
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  it("reports a refresh superseded before it ever reached the network as unavailable", async () => {
    // No Web Locks: the local FIFO gate still defers the refresh past the
    // synchronous sign-in below, which is exactly what the entry epoch check
    // exists to catch. The refresh cookie belongs to the new session now —
    // this is a local supersession, never the server's verdict, so the
    // outcome must not masquerade as an authoritative "rejected".
    vi.stubGlobal("navigator", undefined);

    const pending = refreshSessionDetailed();
    setAccessToken("replacement-login-token", 2);

    await expect(pending).resolves.toEqual({ kind: "unavailable" });
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(nextRequestAuthorization()).resolves.toBe(
      "Bearer replacement-login-token",
    );
  });

  it("reports a refresh the server could not answer as unavailable", async () => {
    setAccessToken("still-valid-token", 1);
    fetchMock.mockResolvedValueOnce(jsonResponse(503, { detail: "deploying" }));

    await expect(refreshSessionDetailed()).resolves.toEqual({
      kind: "unavailable",
    });
    // "unavailable" must never destroy session state: the installed token and
    // the httpOnly refresh cookie both stay put for the next attempt.
    await expect(nextRequestAuthorization()).resolves.toBe(
      "Bearer still-valid-token",
    );
  });

  it("reports an authoritatively refused refresh as rejected", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "no cookie" }));

    await expect(refreshSessionDetailed()).resolves.toEqual({
      kind: "rejected",
    });
  });

  it("reports a refresh whose body lands after a newer sign-in as unavailable", async () => {
    let deliver: ((response: Response) => void) | undefined;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          deliver = resolve;
        }),
    );

    const pending = refreshSessionDetailed();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    setAccessToken("newer-session-token", 7);
    deliver?.(jsonResponse(200, refreshPayload("rotated-token", 7)));

    // A local epoch supersession is not the server's answer: "rejected" stays
    // exclusively for verdicts a caller may destroy session state on.
    await expect(pending).resolves.toEqual({ kind: "unavailable" });
    // The rotated token answers a session this caller no longer owns; the
    // one the newer sign-in installed must survive untouched.
    await expect(nextRequestAuthorization()).resolves.toBe(
      "Bearer newer-session-token",
    );
  });

  it.each([
    ["a string subject", "999"],
    ["a numeric subject", 999],
  ])(
    "rejects a bootstrap refresh whose token carries %s contradicting its user",
    async (_label, subject) => {
      const failure = vi.fn();
      setOnAuthFailure(failure);
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, refreshPayload(jwtWithClaims({ sub: subject }), 1)),
      );

      await expect(refreshSessionDetailed()).resolves.toEqual({
        kind: "rejected",
      });
      expect(failure).toHaveBeenCalledTimes(1);
      await expect(nextRequestAuthorization()).resolves.toBeNull();
    },
  );

  it("decodes a base64url JWT subject before trusting the refreshed actor", async () => {
    // Real signed payloads routinely encode to '-' and '_'. Decoding one as
    // plain base64 loses the subject entirely, and a lost subject waves the
    // token/user contradiction straight through.
    const token = jwtWithClaims({ sub: "999", kid: ">>>?" });
    const payload = token.split(".")[1];
    expect(payload).toContain("-");
    expect(payload).toContain("_");
    const failure = vi.fn();
    setOnAuthFailure(failure);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, refreshPayload(token, 1)));

    await expect(refreshSessionDetailed()).resolves.toEqual({
      kind: "rejected",
    });
    expect(failure).toHaveBeenCalledTimes(1);
    await expect(nextRequestAuthorization()).resolves.toBeNull();
  });

  it("rejects a refresh that switched accounts under the caller", async () => {
    const failure = vi.fn();
    setOnAuthFailure(failure);
    // An opaque token carries no subject, so the actor comes from the sign-in.
    setAccessToken("installed-opaque-token", 101);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, refreshPayload("other-account-token", 202)),
    );

    await expect(refreshSessionDetailed()).resolves.toEqual({
      kind: "rejected",
    });
    expect(failure).toHaveBeenCalledTimes(1);
    await expect(nextRequestAuthorization()).resolves.toBeNull();
  });

  it("reports a refresh that never reached the server as unavailable", async () => {
    const failure = vi.fn();
    setOnAuthFailure(failure);
    setAccessToken("still-valid-token", 1);
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(refreshSessionDetailed()).resolves.toEqual({
      kind: "unavailable",
    });
    expect(failure).not.toHaveBeenCalled();
    await expect(nextRequestAuthorization()).resolves.toBe(
      "Bearer still-valid-token",
    );
  });

  it("reports an unusable auth-cookie lock as unavailable, not as an ended session", async () => {
    const lockRequest = vi
      .fn()
      .mockRejectedValue(new DOMException("locks unavailable", "NotSupportedError"));
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    setAccessToken("still-valid-token", 1);

    await expect(refreshSessionDetailed()).resolves.toEqual({
      kind: "unavailable",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(nextRequestAuthorization()).resolves.toBe(
      "Bearer still-valid-token",
    );
  });
});

describe("request refusal diagnostics", () => {
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
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  it.each([
    "/animals",
    "https://goatfarm.invalid/api/animals",
    "/api/animals#section",
  ])("names the approved-path rule when refusing %s", async (path) => {
    const error = await rejectionOf(apiFetch(path));

    expect(error).toMatchObject({
      name: "UnsafeApiPathError",
      message: UNSAFE_PATH_MESSAGE,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a path the URL parser cannot parse at all", async () => {
    // `new URL("//[::1", origin)` throws on the malformed IPv6 authority. The
    // fallback parse must still land on the deny path instead of escaping as
    // a bare TypeError from inside the path allowlist.
    const error = await rejectionOf(apiFetch("//[::1"));

    expect(error).toMatchObject({
      name: "UnsafeApiPathError",
      message: UNSAFE_PATH_MESSAGE,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("tells a superseded caller its request was not replayed", async () => {
    let deliver: ((response: Response) => void) | undefined;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          deliver = resolve;
        }),
    );
    setAccessToken("original-token", 1);

    const pending = apiFetch("/api/animals");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    setAccessToken("newer-session-token", 2);
    deliver?.(jsonResponse(200, { ok: true }));

    expect(await rejectionOf(pending)).toMatchObject({
      name: "AuthSessionChangedError",
      message: SESSION_CHANGED_MESSAGE,
    });
  });
});
