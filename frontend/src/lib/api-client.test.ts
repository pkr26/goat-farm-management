/**
 * Unit tests for the central fetch wrapper: ApiError detail extraction,
 * the single deduped 401→refresh→retry flow, and the auth-failure path.
 * Global fetch is stubbed directly (no MSW) so call counts and headers
 * can be asserted precisely.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
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

function actorToken(actorId: number): string {
  const payload = btoa(JSON.stringify({ sub: actorId }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${payload}.signature`;
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

describe("apiFetch", () => {
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

  it("throws ApiError with the string detail from a FastAPI error body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(400, { detail: "Tag already exists" }));

    const err = await catchApiError(apiFetch("/api/animals", { method: "POST" }));

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.detail).toBe("Tag already exists");
    expect(err.message).toBe("Tag already exists");
  });

  it("joins the msg fields of a 422 validation-error array", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        detail: [
          { loc: ["body", "tag_number"], msg: "field required", type: "missing" },
          { loc: ["body", "sex"], msg: "value is not valid", type: "value_error" },
        ],
      }),
    );

    const err = await catchApiError(apiFetch("/api/animals", { method: "POST" }));

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.detail).toBe("field required; value is not valid");
  });

  it("refreshes once for concurrent 401s, then retries with the new token", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);
    const retried = new Set<string>();

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, { access_token: "new-token" });
      }
      if (!retried.has(url)) {
        retried.add(url);
        return jsonResponse(401, { detail: "Expired" });
      }
      return jsonResponse(200, { ok: url });
    });

    const [a, b] = await Promise.all([
      apiFetch<{ ok: string }>("/api/animals"),
      apiFetch<{ ok: string }>("/api/tasks"),
    ]);

    expect(a).toEqual({ ok: "/api/animals" });
    expect(b).toEqual({ ok: "/api/tasks" });
    expect(onAuthFailure).not.toHaveBeenCalled();

    const calls = fetchMock.mock.calls.map(([input]) => String(input));
    // One refresh only, despite two concurrent 401s.
    expect(calls.filter((u) => u === "/api/auth/refresh")).toHaveLength(1);
    // Each resource fetched twice: initial 401 + retry.
    expect(calls.filter((u) => u === "/api/animals")).toHaveLength(2);
    expect(calls.filter((u) => u === "/api/tasks")).toHaveLength(2);

    // Retry calls carry the new bearer token; initial calls carried the old one.
    const headersOf = (url: string) =>
      fetchMock.mock.calls
        .filter(([input]) => String(input) === url)
        .map(([, init]) => (init?.headers as Headers).get("Authorization"));
    expect(headersOf("/api/animals")).toEqual(["Bearer old-token", "Bearer new-token"]);
    expect(headersOf("/api/tasks")).toEqual(["Bearer old-token", "Bearer new-token"]);
    // Farm scoping header is sent on API calls, not on the refresh call.
    const animalsInit = fetchMock.mock.calls.find(
      ([input]) => String(input) === "/api/animals",
    )?.[1];
    expect((animalsInit?.headers as Headers).get("X-Farm-Id")).toBe("1");
  });

  it("never replays a request when refresh resolves to a different actor", async () => {
    const actorOne = actorToken(1);
    const actorTwo = actorToken(2);
    const onAuthFailure = vi.fn();
    setAccessToken(actorOne, 1);
    setOnAuthFailure(onAuthFailure);
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, {
          access_token: actorTwo,
          user: { id: 2, email: "actor-two@example.test", name: "Actor Two" },
        });
      }
      return jsonResponse(401, { detail: "Expired" });
    });

    await expect(
      apiFetch("/api/tasks", { method: "POST", body: JSON.stringify({ title: "Do it" }) }),
    ).rejects.toMatchObject({ name: "AuthSessionChangedError" });

    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.filter((url) => url === "/api/tasks")).toHaveLength(1);
    expect(urls.filter((url) => url === "/api/auth/refresh")).toHaveLength(1);
    expect(onAuthFailure).toHaveBeenCalledTimes(1);

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("/api/buckets");
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBeNull();
  });

  it("calls onAuthFailure and clears the token when the refresh fails", async () => {
    const onAuthFailure = vi.fn();
    setOnAuthFailure(onAuthFailure);

    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") return jsonResponse(401, { detail: "No cookie" });
      return jsonResponse(401, { detail: "Expired" });
    });

    const err = await catchApiError(apiFetch("/api/animals"));

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.detail).toBe("Expired");
    expect(onAuthFailure).toHaveBeenCalledTimes(1);

    // Token was cleared: a later request goes out without Authorization.
    fetchMock.mockClear();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("/api/buckets");
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBeNull();
    // Farm scoping is untouched — only the token was dropped.
    expect(headers.get("X-Farm-Id")).toBe("1");
  });

  it("does not attempt a refresh for a 401 on /api/auth/login", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, { detail: "Invalid email or password." }),
    );

    const err = await catchApiError(apiFetch("/api/auth/login", { method: "POST" }));

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.detail).toBe("Invalid email or password.");
    // Exactly one request: no /api/auth/refresh follow-up.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["/api/auth/farms", "/api/auth/me"])(
    "refreshes once and retries a 401 on %s",
    async (path) => {
      const seen = new Set<string>();
      fetchMock.mockImplementation(async (input) => {
        const url = String(input);
        if (url === "/api/auth/refresh") {
          return jsonResponse(200, { access_token: "new-token" });
        }
        if (!seen.has(url)) {
          seen.add(url);
          return jsonResponse(401, { detail: "Expired" });
        }
        return jsonResponse(200, [{ id: 1, name: "Farm One" }]);
      });

      const result = await apiFetch<unknown>(path);

      expect(result).toEqual([{ id: 1, name: "Farm One" }]);
      const calls = fetchMock.mock.calls.map(([input]) => String(input));
      expect(calls.filter((u) => u === "/api/auth/refresh")).toHaveLength(1);
      expect(calls.filter((u) => u === path)).toHaveLength(2);
    },
  );

  it("retries only once — a second 401 surfaces without another refresh", async () => {
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, { access_token: "new-token" });
      }
      return jsonResponse(401, { detail: "Still expired" });
    });

    const err = await catchApiError(apiFetch("/api/auth/farms"));

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    const calls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(calls.filter((u) => u === "/api/auth/refresh")).toHaveLength(1);
    expect(calls.filter((u) => u === "/api/auth/farms")).toHaveLength(2);
  });
});
