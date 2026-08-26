/**
 * Behavioural cover for the fetch wrapper's remaining unguarded edges.
 *
 * Four hazards are pinned here:
 *   1. Actor identity decoded from an undecodable token. A token whose payload
 *      segment is not base64 JSON has NO known actor; treating it as "some
 *      other actor" would make every legitimate refresh look like a
 *      cross-account switch and sign the operator out.
 *   2. Coordination deadlines that outlive their wait. The 62 s auth-cookie
 *      queue bound must be disarmed the moment the wait ends — whether the
 *      lock was granted, or the request failed before grant — otherwise every
 *      auth mutation leaves a live timer behind and a slow-but-legitimate
 *      write can have its own lock request signalled as aborted mid-flight.
 *   3. A queued logout that has already torn down local state. It must still
 *      reach the server (its whole job is deleting the origin-wide refresh
 *      cookie), including on the trailing-slash spelling of the route.
 *   4. Session fences around response bodies. A superseded request must stop
 *      at the fence: no refresh spent, no body read for the actor that is no
 *      longer on screen.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  apiFetchEnvelope,
  authSessionEpochValue,
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

function base64Url(value: string): string {
  return globalThis
    .btoa(value)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function tokenForActor(subject: string): string {
  return `eyJhbGciOiJSUzI1NiJ9.${base64Url(JSON.stringify({ sub: subject }))}.signature`;
}

function refreshBody(accessToken: string, actorId: number) {
  return {
    access_token: accessToken,
    user: { id: actorId, email: `actor-${actorId}@goatfarm.test`, name: null },
  };
}

/** A Response whose body reads are observable. `onFirstRead` fires from inside
 *  the first parse, which is how a session change lands mid-stream. */
function trackedResponse(body: unknown, onFirstRead?: () => void) {
  let reads = 0;
  const json = vi.fn(async () => {
    reads += 1;
    if (reads === 1) onFirstRead?.();
    return body;
  });
  const response: Response = {
    ok: true,
    status: 200,
    statusText: "OK",
    headers: new Headers({ "Content-Type": "application/json" }),
    json,
    clone: () => response,
  } as unknown as Response;
  return { response, json };
}

/** Lets the macrotask-scheduled single-flight refresh slot reset. */
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

describe("api-client behaviour", () => {
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

  describe("actor identity of an undecodable token", () => {
    it.each([
      ["is not valid base64", "eyJhbGciOiJSUzI1NiJ9.@@@@.signature"],
      ["does not decode to JSON", `eyJhbGciOiJSUzI1NiJ9.${base64Url("not-json")}.signature`],
    ])(
      "keeps a refresh usable when the installed token's payload %s",
      async (_label, token) => {
        // Three dot-separated segments do not make a token a readable JWT. A
        // payload we cannot decode means "actor unknown", not "actor X" — so
        // the refresh below is the first thing that establishes an identity
        // and must be accepted rather than mistaken for another account.
        const failure = vi.fn();
        setOnAuthFailure(failure);
        setAccessToken(token);
        fetchMock
          .mockResolvedValueOnce(jsonResponse(401, { detail: "Expired" }))
          .mockResolvedValueOnce(
            jsonResponse(200, refreshBody("rotated-opaque-token", 202)),
          )
          .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

        await expect(apiFetch("/api/animals")).resolves.toEqual({ ok: true });

        expect(fetchMock).toHaveBeenCalledTimes(3);
        expect(failure).not.toHaveBeenCalled();
        expect(
          new Headers(fetchMock.mock.calls[2][1]?.headers).get("Authorization"),
        ).toBe("Bearer rotated-opaque-token");
      },
    );
  });

  describe("paths the URL parser cannot resolve", () => {
    it.each(["http://", "//"])(
      "refuses the unparseable path %s before any request is sent",
      async (path) => {
        const error = await rejectionOf(apiFetch(path));

        expect(error).toMatchObject({ name: "UnsafeApiPathError" });
        expect(fetchMock).not.toHaveBeenCalled();
      },
    );
  });

  describe("auth-cookie coordination deadlines", () => {
    it("disarms the local queue deadline once the turn is taken", async () => {
      // Without Web Locks the queue bound is a plain timer. Leaving it armed
      // after the wait ends parks a live 62 s timer (and its closure) behind
      // every single auth mutation the tab performs.
      vi.stubGlobal("navigator", undefined);
      const callerSignal = new AbortController().signal;
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

      vi.useFakeTimers();
      try {
        await expect(
          apiFetch("/api/auth/logout", { method: "POST", signal: callerSignal }),
        ).resolves.toBeUndefined();

        expect(vi.getTimerCount()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it("stops applying the queue deadline once the Web Lock is granted", async () => {
      // The deadline bounds the WAIT, not the write. A herd-wide dispense or a
      // slow password change legitimately holds the lock for a while; its own
      // request timeout takes over there, and the lock request must not be
      // signalled as aborted underneath it.
      let waitSignal: AbortSignal | undefined;
      let releaseFetch: (() => void) | undefined;
      const lockRequest = vi.fn(
        async (
          _name: string,
          options: LockOptions,
          callback: () => Promise<unknown>,
        ) => {
          waitSignal = options.signal ?? undefined;
          return callback();
        },
      );
      vi.stubGlobal("navigator", { locks: { request: lockRequest } });
      const callerSignal = new AbortController().signal;
      fetchMock.mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            releaseFetch = () => resolve(new Response(null, { status: 204 }));
          }),
      );

      vi.useFakeTimers();
      try {
        const pending = apiFetch("/api/auth/change-password", {
          method: "POST",
          body: JSON.stringify({ current_password: "old", new_password: "new" }),
          signal: callerSignal,
        });
        await vi.advanceTimersByTimeAsync(0);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);

        await vi.advanceTimersByTimeAsync(62_000);

        expect(waitSignal?.aborted).toBe(false);
        releaseFetch?.();
        await expect(pending).resolves.toBeUndefined();
      } finally {
        vi.useRealTimers();
      }
    });

    it("disarms the queue deadline when the lock request fails before grant", async () => {
      let waitSignal: AbortSignal | undefined;
      const lockRequest = vi.fn((_name: string, options: LockOptions) => {
        waitSignal = options.signal ?? undefined;
        return Promise.reject(
          new DOMException("locks unavailable", "NotSupportedError"),
        );
      });
      vi.stubGlobal("navigator", { locks: { request: lockRequest } });

      vi.useFakeTimers();
      try {
        const error = await rejectionOf(
          apiFetch("/api/auth/login", { method: "POST" }),
        );

        expect(error).toBeInstanceOf(ApiError);
        expect(error).toMatchObject({
          status: 429,
          detail:
            "Secure authentication coordination is temporarily unavailable. Try again shortly.",
        });
        expect(vi.getTimerCount()).toBe(0);

        await vi.advanceTimersByTimeAsync(62_000);

        expect(waitSignal?.aborted).toBe(false);
        expect(fetchMock).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it("reports a lock that never frees up as a retryable coordination conflict", async () => {
      // A wedged tab holding the lock is a different failure from Web Locks
      // being unusable: this one clears by itself, so the operator is told to
      // retry shortly rather than that secure coordination is unavailable.
      const lockRequest = vi.fn(
        (_name: string, options: LockOptions) =>
          new Promise<never>((_resolve, reject) => {
            options.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      );
      vi.stubGlobal("navigator", { locks: { request: lockRequest } });

      vi.useFakeTimers();
      try {
        const settled = rejectionOf(
          apiFetch("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({ email: "worker@goatfarm.test", password: "x" }),
          }),
        );

        await vi.advanceTimersByTimeAsync(62_000);

        const error = await settled;
        expect(error).toBeInstanceOf(ApiError);
        expect(error).toMatchObject({
          status: 429,
          detail: "Another authentication change is still finishing. Try again shortly.",
        });
        expect(fetchMock).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("the queued logout teardown exception", () => {
    it("still sends a queued /api/auth/logout/ after local state is cleared", async () => {
      // AuthProvider starts logout with the old bearer and clears local state
      // before the queued fetch gets the lock. The request must still go out —
      // it is the only thing that deletes the origin-wide refresh cookie — and
      // the trailing-slash spelling of the route is the same route.
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
        if (path === "/api/auth/change-password") await blockerGate;
        return new Response(null, { status: 204 });
      });

      const blocker = apiFetch("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: "old", new_password: "new" }),
      });
      await vi.waitFor(() => expect(sent).toEqual(["/api/auth/change-password"]));
      const logout = apiFetch("/api/auth/logout/", { method: "POST" });
      setAccessToken(null);
      releaseBlocker?.();

      await expect(blocker).rejects.toMatchObject({
        name: "AuthSessionChangedError",
      });
      await expect(logout).rejects.toMatchObject({
        name: "AuthSessionChangedError",
      });
      expect(sent).toEqual(["/api/auth/change-password", "/api/auth/logout/"]);
    });
  });

  describe("refresh outcomes with no auth-failure handler registered", () => {
    it("reports a token/user actor mismatch as an authoritative rejection", async () => {
      // A refresh can run before any provider has mounted (bootstrap) or after
      // the last one unmounted. The outcome still has to be "rejected": a
      // caller that saw "unavailable" would keep a session the server has
      // already contradicted.
      setAccessToken(tokenForActor("101"));
      const epochBefore = authSessionEpochValue();
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, refreshBody(tokenForActor("202"), 101)),
      );

      await expect(refreshSessionDetailed()).resolves.toEqual({ kind: "rejected" });

      expect(fetchMock).toHaveBeenCalledTimes(1);
      // The contradictory token was dropped, not installed.
      expect(authSessionEpochValue()).toBeGreaterThan(epochBefore);
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
      await apiFetch("/api/animals");
      expect(
        new Headers(fetchMock.mock.calls[1][1]?.headers).get("Authorization"),
      ).toBeNull();
    });

    it("reports another tab's account swap as an authoritative rejection", async () => {
      setAccessToken(tokenForActor("101"));
      fetchMock.mockResolvedValueOnce(
        jsonResponse(200, refreshBody(tokenForActor("202"), 202)),
      );

      await expect(refreshSessionDetailed()).resolves.toEqual({ kind: "rejected" });

      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });

  describe("session fences around a response", () => {
    it("does not spend the refresh cookie on a 401 for a superseded session", async () => {
      setAccessToken("token-1", 1);
      fetchMock
        .mockImplementationOnce(async () => {
          // A newer sign-in lands while this request is on the wire.
          setAccessToken("token-2", 2);
          return jsonResponse(401, { detail: "Expired" });
        })
        .mockResolvedValueOnce(jsonResponse(200, refreshBody("rotated", 1)));

      const error = await rejectionOf(apiFetch("/api/animals"));

      expect(error).toMatchObject({ name: "AuthSessionChangedError" });
      // The queued refresh response above is deliberately never consumed: a
      // 401 that belongs to nobody must not rotate the shared refresh cookie.
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls.map(([input]) => String(input))).not.toContain(
        "/api/auth/refresh",
      );
    });

    it("does not read a retried response that lands after the session changed", async () => {
      setAccessToken("token-1", 1);
      const retried = trackedResponse({ private_value: "actor-one-only" });
      fetchMock
        .mockResolvedValueOnce(jsonResponse(401, { detail: "Expired" }))
        .mockResolvedValueOnce(jsonResponse(200, refreshBody("rotated", 1)))
        .mockImplementationOnce(async () => {
          setAccessToken("token-2", 2);
          return retried.response;
        });

      const error = await rejectionOf(
        apiFetch("/api/animals", {
          method: "POST",
          body: JSON.stringify({ tag_number: "A-1" }),
        }),
      );

      expect(error).toMatchObject({ name: "AuthSessionChangedError" });
      expect(retried.json).not.toHaveBeenCalled();
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it("does not parse a body for apiFetch once the session changed mid-stream", async () => {
      // Protected mutations buffer their success body before the logical
      // request is retired. If a newer login lands during that read, the
      // caller is superseded and its stream must be left alone.
      setAccessToken("token-1", 1);
      const created = trackedResponse({ id: 7, title: "Trim hooves" }, () => {
        setAccessToken("token-2", 2);
      });
      fetchMock.mockResolvedValueOnce(created.response);

      const error = await rejectionOf(
        apiFetch("/api/tasks", {
          method: "POST",
          body: JSON.stringify({ title: "Trim hooves" }),
        }),
      );

      expect(error).toMatchObject({ name: "AuthSessionChangedError" });
      expect(created.json).toHaveBeenCalledTimes(1);
    });

    it("does not parse a body for apiFetchEnvelope once the session changed mid-stream", async () => {
      setAccessToken("token-1", 1);
      const created = trackedResponse({ id: 8, title: "Weigh kids" }, () => {
        setAccessToken("token-2", 2);
      });
      fetchMock.mockResolvedValueOnce(created.response);

      const error = await rejectionOf(
        apiFetchEnvelope("/api/tasks", {
          method: "POST",
          body: JSON.stringify({ title: "Weigh kids" }),
        }),
      );

      expect(error).toMatchObject({ name: "AuthSessionChangedError" });
      expect(created.json).toHaveBeenCalledTimes(1);
    });
  });
});
