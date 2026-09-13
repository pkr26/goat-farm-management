import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, setAccessToken, setCurrentFarmId } from "./api-client";
import {
  clearIdempotencyRequestState,
  clearPersistedIdempotencyRequestState,
  IDEMPOTENCY_SESSION_STORAGE_KEY,
  isIdempotencyProtectedMutation,
} from "./idempotent-request";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function catchError(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected request to reject");
}

function requestKey(init?: RequestInit): string | null {
  return new Headers(init?.headers).get("Idempotency-Key");
}

function tokenForActor(subject: string): string {
  const payload = globalThis
    .btoa(JSON.stringify({ sub: subject }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `eyJhbGciOiJSUzI1NiJ9.${payload}.test-signature`;
}

const ACTOR_ONE_TOKEN = tokenForActor("101");
const ACTOR_TWO_TOKEN = tokenForActor("202");

type StoredRecord = {
  version: number;
  digest: string;
  key: string;
  expiresAt: number;
};

function storedRecords(): StoredRecord[] {
  const raw = window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY);
  return raw ? (JSON.parse(raw) as StoredRecord[]) : [];
}

describe("protected mutation idempotency transport", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    window.sessionStorage.clear();
    setAccessToken("access-token");
    setCurrentFarmId("17");
    clearIdempotencyRequestState();
  });

  afterEach(async () => {
    clearIdempotencyRequestState();
    setAccessToken(null);
    setCurrentFarmId(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    // Allow the auth refresh de-duplication slot to clear between tests.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it.each([
    "/api/auth/farms",
    "/api/finance/new",
    "/api/finance/transactions/42/correct",
    "/api/purchases/new",
    "/api/animals",
    "/api/animals/42/weight",
    "/api/tasks",
    "/api/team/workers",
    "/api/health/events",
    "/api/simulation/scenarios",
    "/api/feeding/dispense",
    "/api/feeding/mix",
    "/api/feeding/inventory/7/add",
  ])("adds a cryptographically generated UUID key to %s", async (path) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { ok: true }));

    await apiFetch(path, { method: "POST", body: JSON.stringify({ amount: 1 }) });

    expect(requestKey(fetchMock.mock.calls[0]?.[1])).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it("fails closed before send when secure random generation is unavailable", async () => {
    vi.stubGlobal("crypto", undefined);

    const error = await catchError(
      apiFetch("/api/finance/new", { method: "POST", body: "{}" }),
    );

    expect(error).toMatchObject({
      message: "Secure random generation is unavailable; mutation was not sent.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("builds a standards-compliant UUID when randomUUID is unavailable", async () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index));
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { ok: true }));

    await apiFetch("/api/finance/new", { method: "POST", body: "{}" });

    expect(getRandomValues).toHaveBeenCalledTimes(1);
    expect(requestKey(fetchMock.mock.calls[0]?.[1])).toBe(
      "00010203-0405-4607-8809-0a0b0c0d0e0f",
    );
  });

  it("supports replayable URLSearchParams without forcing a JSON content type", async () => {
    const body = new URLSearchParams({ amount: "10", kind: "FEED" });
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { ok: true }));

    await apiFetch("/api/finance/new", { method: "POST", body });

    const sent = fetchMock.mock.calls[0]?.[1];
    expect(sent?.body).toBe(body);
    expect(requestKey(sent)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(new Headers(sent?.headers).get("Content-Type")).toBeNull();
  });

  it("rejects an unreplayable protected body before it can be sent", async () => {
    const body = new FormData();
    body.set("amount", "10");

    const error = await catchError(
      apiFetch("/api/finance/new", { method: "POST", body }),
    );

    expect(error).toMatchObject({
      message: "Protected mutations require a replayable string request body.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("protects only the live routes and leaves mistaken collection roots untouched", () => {
    expect(isIdempotencyProtectedMutation("/api/auth/farms", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/finance/new", "POST")).toBe(true);
    expect(
      isIdempotencyProtectedMutation("/api/finance/transactions/42/correct", "post"),
    ).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/purchases/new", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/animals", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/animals/42/weight", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/tasks", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/team/workers", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/health/events", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/simulation/scenarios", "POST")).toBe(
      true,
    );
    expect(isIdempotencyProtectedMutation("/api/finance", "POST")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/finance/42/correct", "POST")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/purchases", "POST")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/animals/not-an-id/weight", "POST")).toBe(
      false,
    );
    expect(isIdempotencyProtectedMutation("/api/simulation/scenarios/7", "POST")).toBe(
      false,
    );
    expect(isIdempotencyProtectedMutation("/api/health", "POST")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/auth/farm", "POST")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/finance", "GET")).toBe(false);
  });

  it.each(["/api/finance", "/api/purchases"])(
    "does not inject a key into nonexistent collection mutation %s",
    async (path) => {
      fetchMock.mockResolvedValueOnce(jsonResponse(404, { detail: "Not found" }));

      expect(
        await catchError(apiFetch(path, { method: "POST", body: "{}" })),
      ).toBeInstanceOf(ApiError);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(requestKey(fetchMock.mock.calls[0]?.[1])).toBeNull();
    },
  );

  it("reuses the key across a 401 refresh replay", async () => {
    const keys: string[] = [];
    let protectedCalls = 0;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, {
          access_token: "refreshed-token",
          user: { id: 1, email: "actor-1@example.test", name: null },
        });
      }
      protectedCalls += 1;
      keys.push(requestKey(init)!);
      return protectedCalls === 1
        ? jsonResponse(401, { detail: "Expired" })
        : jsonResponse(201, { id: 9 });
    });

    await apiFetch("/api/finance/new", { method: "POST", body: "{}" });

    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/auth/refresh"))
      .toHaveLength(1);
  });

  it("blocks a 401 replay when login state changes during refresh", async () => {
    let protectedCalls = 0;
    fetchMock.mockImplementation(async (input) => {
      if (String(input) === "/api/auth/refresh") {
        setAccessToken("different-login-token");
        return jsonResponse(200, {
          access_token: "stale-refresh-token",
          user: { id: 1, email: "actor-1@example.test", name: null },
        });
      }
      protectedCalls += 1;
      return jsonResponse(401, { detail: "Expired" });
    });

    const error = await catchError(
      apiFetch("/api/finance/new", { method: "POST", body: "{}" }),
    );

    expect((error as Error).name).toBe("AuthSessionChangedError");
    expect(protectedCalls).toBe(1);
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/auth/refresh"))
      .toHaveLength(1);

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await apiFetch("/api/buckets");
    expect(
      new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization"),
    ).toBe("Bearer different-login-token");
  });

  it("retains an ambiguous key when the automatic retry ends in an auth failure", async () => {
    setAccessToken(ACTOR_ONE_TOKEN);
    const keys: string[] = [];
    let protectedCalls = 0;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input) === "/api/auth/refresh") {
        return new Response(null, { status: 401 });
      }
      protectedCalls += 1;
      keys.push(requestKey(init)!);
      if (protectedCalls === 1) {
        throw new TypeError("response lost after the mutation may have committed");
      }
      if (protectedCalls === 2) {
        return jsonResponse(401, { detail: "Session expired" });
      }
      return jsonResponse(201, { id: 71 });
    });
    const init = { method: "POST", body: JSON.stringify({ amount: 500 }) };

    expect(await catchError(apiFetch("/api/finance/new", init))).toBeInstanceOf(
      ApiError,
    );
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);

    // The same actor signs in again and explicitly retries the still-
    // ambiguous logical submission. It must recover the original key.
    setAccessToken(ACTOR_ONE_TOKEN);
    await apiFetch("/api/finance/new", init);

    expect(keys).toHaveLength(3);
    expect(keys[2]).toBe(keys[0]);
  });

  it("automatically retries one network failure with the same key", async () => {
    const keys: string[] = [];
    const farms: Array<string | null> = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      farms.push(new Headers(init?.headers).get("X-Farm-Id"));
      if (keys.length === 1) {
        setCurrentFarmId("99");
        throw new TypeError("connection reset");
      }
      return jsonResponse(201, { id: 1 });
    });

    await apiFetch("/api/purchases/new", { method: "POST", body: "{}" });

    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
    expect(farms).toEqual(["17", "17"]);
  });

  it("persists the exact task-create key across an ambiguous network retry", async () => {
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length === 1) throw new TypeError("response lost after task commit");
      return jsonResponse(201, { id: 44, recurring_series_id: "series-44" });
    });

    await apiFetch("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        title: "Daily water check",
        due_date: "2026-08-08",
        category: "OTHER",
        recur_days: 1,
      }),
    });

    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("keeps worker-create retries in memory without persisting a password verifier", async () => {
    setAccessToken(ACTOR_ONE_TOKEN);
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length <= 2) throw new TypeError("response lost after worker commit");
      return jsonResponse(201, { id: 51, user_id: 88 });
    });
    const init = {
      method: "POST",
      body: JSON.stringify({
        email: "new-worker@farm.in",
        password: "private-worker-password",
        role_id: 7,
      }),
    };

    expect(await catchError(apiFetch("/api/team/workers", init))).toBeInstanceOf(
      TypeError,
    );
    // The first TypeError is automatically retried in this realm with the
    // same key, but reload recovery must not persist a fast digest of the
    // password-bearing JSON body.
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
    expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();

    // Simulate a reload: memory is gone, and the fresh submission gets a new
    // key rather than recovering password-derived state from sessionStorage.
    clearIdempotencyRequestState();
    setAccessToken(null);
    setAccessToken(ACTOR_ONE_TOKEN);
    await apiFetch("/api/team/workers", init);

    expect(keys).toHaveLength(3);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("refuses to replay a protected mutation after the authenticated session changes", async () => {
    const keys: string[] = [];
    const authorizations: Array<string | null> = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      authorizations.push(new Headers(init?.headers).get("Authorization"));
      if (keys.length === 1) {
        setAccessToken("replacement-session-token");
        throw new TypeError("connection reset after send");
      }
      return jsonResponse(201, { id: 1 });
    });
    const init = { method: "POST", body: "{}" };

    const error = await catchError(apiFetch("/api/purchases/new", init));

    expect((error as Error).name).toBe("AuthSessionChangedError");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(authorizations).toEqual(["Bearer access-token"]);

    await apiFetch("/api/purchases/new", init);
    expect(authorizations[1]).toBe("Bearer replacement-session-token");
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("retries an interrupted response body with the same key", async () => {
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length === 1) {
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.error(new TypeError("response body interrupted"));
            },
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      return jsonResponse(201, { id: 1 });
    });

    await apiFetch("/api/feeding/mix", { method: "POST", body: "{}" });

    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("retries a malformed successful JSON response with the same key", async () => {
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length === 1) {
        return new Response("{not-valid-json", {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }
      return jsonResponse(201, { id: 73 });
    });

    await expect(
      apiFetch("/api/finance/new", { method: "POST", body: "{}" }),
    ).resolves.toEqual({ id: 73 });

    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it("does not turn an intentional abort into an automatic retry", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("cancelled", "AbortError"));

    const error = await catchError(
      apiFetch("/api/feeding/mix", { method: "POST", body: "{}" }),
    );

    expect((error as Error).name).toBe("AbortError");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retains the key for an explicit retry after an ambiguous network failure", async () => {
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length <= 2) throw new TypeError("offline");
      return jsonResponse(201, { id: 1 });
    });
    const request = () =>
      apiFetch("/api/feeding/dispense", {
        method: "POST",
        body: JSON.stringify({ bucket: "KIDS" }),
      });

    expect(await catchError(request())).toBeInstanceOf(TypeError);
    await request();

    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(1);
  });

  it("shares one fetch and one key for simultaneous identical submissions", async () => {
    let release: ((response: Response) => void) | undefined;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const init = { method: "POST", body: JSON.stringify({ recipe_id: 5 }) };

    const first = apiFetch<{ id: number }>("/api/feeding/mix", init);
    const second = apiFetch<{ id: number }>("/api/feeding/mix", init);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    release?.(jsonResponse(201, { id: 44 }));
    await expect(Promise.all([first, second])).resolves.toEqual([{ id: 44 }, { id: 44 }]);
  });

  it("rejects a concurrent identical caller with different cancellation ownership", async () => {
    let release: ((response: Response) => void) | undefined;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const firstController = new AbortController();
    const secondController = new AbortController();
    const base = { method: "POST", body: "{}" };

    const first = apiFetch("/api/feeding/mix", {
      ...base,
      signal: firstController.signal,
    });
    const conflict = await catchError(
      apiFetch("/api/feeding/mix", { ...base, signal: secondController.signal }),
    );

    expect((conflict as Error).name).toBe("ProtectedMutationSignalConflictError");
    expect((conflict as Error).message).toBe(
      "An identical protected mutation is already running with a different cancellation signal.",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release?.(jsonResponse(201, { id: 44 }));
    await expect(first).resolves.toEqual({ id: 44 });
  });

  it("keeps sharing an in-flight request even after its retry-key TTL passes", async () => {
    let now = 10_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    let release: ((response: Response) => void) | undefined;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const init = { method: "POST", body: "{}" };

    const first = apiFetch("/api/feeding/mix", init);
    now += 2 * 60 * 1000 + 1;
    const second = apiFetch("/api/feeding/mix", init);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    release?.(jsonResponse(201, { id: 44 }));
    await expect(Promise.all([first, second])).resolves.toEqual([
      { id: 44 },
      { id: 44 },
    ]);
  });

  it("does not let an older success delete a replacement in-flight entry", async () => {
    const releases: Array<(response: Response) => void> = [];
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          releases.push(resolve);
        }),
    );
    const init = { method: "POST", body: "{}" };
    const first = apiFetch("/api/feeding/mix", init);
    clearIdempotencyRequestState();
    const replacement = apiFetch("/api/feeding/mix", init);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    releases[0](jsonResponse(201, { id: 1 }));
    await first;
    const shared = apiFetch("/api/feeding/mix", init);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    releases[1](jsonResponse(201, { id: 2 }));
    await expect(Promise.all([replacement, shared])).resolves.toEqual([
      { id: 2 },
      { id: 2 },
    ]);
  });

  it("does not let an older rejection delete a replacement in-flight entry", async () => {
    const releases: Array<(response: Response) => void> = [];
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          releases.push(resolve);
        }),
    );
    const init = { method: "POST", body: "{}" };
    const firstOutcome = catchError(apiFetch("/api/feeding/mix", init));
    clearIdempotencyRequestState();
    const replacement = apiFetch("/api/feeding/mix", init);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    releases[0](jsonResponse(422, { detail: "definite rejection" }));
    await expect(firstOutcome).resolves.toBeInstanceOf(ApiError);
    const shared = apiFetch("/api/feeding/mix", init);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    releases[1](jsonResponse(201, { id: 2 }));
    await expect(Promise.all([replacement, shared])).resolves.toEqual([
      { id: 2 },
      { id: 2 },
    ]);
  });

  it("shares cancellation only when identical callers deliberately share one signal", async () => {
    fetchMock.mockImplementation(
      async (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("cancelled", "AbortError")),
          );
        }),
    );
    const controller = new AbortController();
    const init = { method: "POST", body: "{}", signal: controller.signal };

    const first = apiFetch("/api/feeding/mix", init);
    const second = apiFetch("/api/feeding/mix", init);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    controller.abort();

    const [firstError, secondError] = await Promise.all([
      catchError(first),
      catchError(second),
    ]);
    expect((firstError as Error).name).toBe("AbortError");
    expect((secondError as Error).name).toBe("AbortError");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("honors a caller-provided key verbatim", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: 1 }));

    await apiFetch("/api/finance/new", {
      method: "POST",
      headers: { "Idempotency-Key": "caller-logical-submission-7" },
      body: "{}",
    });

    expect(requestKey(fetchMock.mock.calls[0]?.[1])).toBe("caller-logical-submission-7");
  });

  it("generates a fresh key after a successful logical submission completes", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(201, { id: 1 }));
    const init = { method: "POST", body: JSON.stringify({ amount: 50 }) };

    await apiFetch("/api/finance/new", init);
    await apiFetch("/api/finance/new", init);

    const keys = fetchMock.mock.calls.map(([, requestInit]) => requestKey(requestInit));
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("drops a key after a completed non-retryable HTTP rejection", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(422, { detail: "Invalid amount" }))
      .mockResolvedValueOnce(jsonResponse(201, { id: 1 }));
    const init = { method: "POST", body: JSON.stringify({ amount: -1 }) };

    expect(await catchError(apiFetch("/api/finance/new", init))).toBeInstanceOf(ApiError);
    await apiFetch("/api/finance/new", init);

    const keys = fetchMock.mock.calls.map(([, requestInit]) => requestKey(requestInit));
    expect(keys[0]).not.toBe(keys[1]);
  });

  it.each([408, 409, 429])(
    "retains the key after retryable HTTP status %s",
    async (status) => {
      const keys: string[] = [];
      fetchMock.mockImplementationOnce(async (_input, init) => {
        keys.push(requestKey(init)!);
        return jsonResponse(status, { detail: "retry later" });
      });
      fetchMock.mockImplementationOnce(async (_input, init) => {
        keys.push(requestKey(init)!);
        return jsonResponse(201, { id: 1 });
      });
      const init = { method: "POST", body: "{}" };

      await catchError(apiFetch("/api/finance/new", init));
      await apiFetch("/api/finance/new", init);

      expect(keys).toHaveLength(2);
      expect(keys[1]).toBe(keys[0]);
    },
  );

  it("expires a retained ambiguous-failure key after the bounded retry TTL", async () => {
    let now = 10_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length <= 2) throw new TypeError("offline");
      return jsonResponse(201, { id: 1 });
    });
    const init = { method: "POST", body: "{}" };

    await catchError(apiFetch("/api/feeding/inventory/7/add", init));
    now += 3 * 60 * 1000;
    await apiFetch("/api/feeding/inventory/7/add", init);

    expect(keys).toHaveLength(3);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("expires a retained key at the exact TTL boundary", async () => {
    let now = 10_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const keys: string[] = [];
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (keys.length <= 2) throw new TypeError("offline");
      return jsonResponse(201, { id: 1 });
    });
    const init = { method: "POST", body: "{}" };

    await catchError(apiFetch("/api/feeding/inventory/7/add", init));
    now += 2 * 60 * 1000;
    await apiFetch("/api/feeding/inventory/7/add", init);

    expect(keys).toHaveLength(3);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("extends persisted recovery when an explicit retry is still ambiguous", async () => {
    let now = 10_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    setAccessToken(ACTOR_ONE_TOKEN);
    const keys: string[] = [];
    let succeed = false;
    fetchMock.mockImplementation(async (_input, init) => {
      keys.push(requestKey(init)!);
      if (!succeed) {
        // The explicit retry itself can take long enough that the pre-send
        // persistence window is no longer sufficient after it fails.
        if (keys.length === 4) now += 30_000;
        throw new TypeError("offline");
      }
      return jsonResponse(201, { id: 1 });
    });
    const init = { method: "POST", body: "{}" };

    await catchError(apiFetch("/api/finance/new", init));
    now += 2 * 60 * 1000 - 1;
    await catchError(apiFetch("/api/finance/new", init));
    clearIdempotencyRequestState();
    // Past the pre-send expiry, but still within the post-failure extension.
    now += 90_001;
    succeed = true;
    await apiFetch("/api/finance/new", init);

    expect(keys).toHaveLength(5);
    expect(new Set(keys).size).toBe(1);
  });

  it("evicts the oldest settled retry key at the retained-state count bound", async () => {
    const firstRequestKeys: string[] = [];
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input) === "/api/feeding/inventory/1/add") {
        firstRequestKeys.push(requestKey(init)!);
      }
      throw new TypeError("offline");
    });

    // Fill the 128-entry retry registry, then force its oldest entry out.
    for (let itemId = 1; itemId <= 129; itemId += 1) {
      await catchError(
        apiFetch(`/api/feeding/inventory/${itemId}/add`, {
          method: "POST",
          body: "{}",
        }),
      );
    }
    await catchError(
      apiFetch("/api/feeding/inventory/1/add", { method: "POST", body: "{}" }),
    );

    expect(firstRequestKeys).toHaveLength(4);
    expect(firstRequestKeys[0]).toBe(firstRequestKeys[1]);
    expect(firstRequestKeys[2]).toBe(firstRequestKeys[3]);
    expect(firstRequestKeys[2]).not.toBe(firstRequestKeys[0]);
  });

  it("rejects a 129th protected mutation while every registry slot is in flight", async () => {
    const releases: Array<(response: Response) => void> = [];
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          releases.push(resolve);
        }),
    );
    const requests = Array.from({ length: 128 }, (_, index) =>
      apiFetch(`/api/feeding/inventory/${index + 1}/add`, {
        method: "POST",
        body: "{}",
      }),
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(128));

    const overflow = await catchError(
      apiFetch("/api/feeding/inventory/129/add", { method: "POST", body: "{}" }),
    );

    expect(overflow).toMatchObject({
      message: "Too many protected mutations are already in flight. Try again shortly.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(128);

    for (const release of releases) release(jsonResponse(201, { ok: true }));
    await expect(Promise.all(requests)).resolves.toHaveLength(128);
  });

  it("leaves non-protected routes unchanged: no key, retry, or in-flight sharing", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));

    expect(
      await catchError(apiFetch("/api/breeding/9/ultrasound", { method: "POST", body: "{}" })),
    ).toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(requestKey(fetchMock.mock.calls[0]?.[1])).toBeNull();

    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => jsonResponse(201, { id: 1 }));
    await Promise.all([
      apiFetch("/api/breeding/9/ultrasound", { method: "POST", body: "{}" }),
      apiFetch("/api/breeding/9/ultrasound", { method: "POST", body: "{}" }),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => requestKey(init) === null)).toBe(true);
  });

  it.each([
    "https://evil.example/api/finance/new",
    "//evil.example/api/finance/new",
    "/animals",
    "/api/../finance/new",
  ])("rejects unsafe API URL %s before credentials or farm headers can leave", async (path) => {
    const error = await catchError(apiFetch(path, { method: "POST", body: "{}" }));

    expect((error as Error).name).toBe("UnsafeApiPathError");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  describe("same-tab reload persistence", () => {
    it("does not recreate an old session's retry key when logout wins the digest race", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      const cryptography = globalThis.crypto;
      const realDigest = cryptography.subtle.digest.bind(cryptography.subtle);
      let releaseDigest!: () => void;
      const digestGate = new Promise<void>((resolve) => {
        releaseDigest = resolve;
      });
      const digest = vi.fn(async (...args: Parameters<SubtleCrypto["digest"]>) => {
        await digestGate;
        return realDigest(...args);
      });
      vi.stubGlobal("crypto", {
        randomUUID: cryptography.randomUUID.bind(cryptography),
        getRandomValues: cryptography.getRandomValues.bind(cryptography),
        subtle: { digest },
      });

      const request = apiFetch("/api/finance/new", {
        method: "POST",
        body: JSON.stringify({ amount: 500 }),
      });
      await vi.waitFor(() => expect(digest).toHaveBeenCalledTimes(1));

      // Logout clears both the realm registry and its durable recovery keys
      // while SHA-256 is still in flight. The old continuation must observe
      // the session boundary before it can put either one back.
      clearPersistedIdempotencyRequestState();
      setAccessToken(null);
      releaseDigest();

      await expect(request).rejects.toMatchObject({
        name: "AuthSessionChangedError",
      });
      expect(fetchMock).not.toHaveBeenCalled();
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
    });

    it("recovers farm creation by actor without a farm scope and isolates actors", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      setCurrentFarmId(null);
      const keys: string[] = [];
      const sentFarmScopes: Array<string | null> = [];
      let fail = true;
      fetchMock.mockImplementation(async (_input, init) => {
        keys.push(requestKey(init)!);
        sentFarmScopes.push(new Headers(init?.headers).get("X-Farm-Id"));
        if (fail) throw new TypeError("farm response lost after commit");
        return jsonResponse(201, { id: 91, name: "Recovered Farm" });
      });
      const init = {
        method: "POST",
        body: JSON.stringify({ name: "Recovered Farm" }),
      };

      expect(await catchError(apiFetch("/api/auth/farms", init))).toBeInstanceOf(
        TypeError,
      );
      const actorOneKey = keys[0];
      expect(keys[1]).toBe(actorOneKey);
      expect(storedRecords()).toHaveLength(1);

      // A reload and stale farm selection still recover the actor-scoped key.
      clearIdempotencyRequestState();
      setAccessToken(null);
      setAccessToken(ACTOR_ONE_TOKEN);
      setCurrentFarmId("999");
      fail = false;
      await apiFetch("/api/auth/farms", init);
      expect(keys[2]).toBe(actorOneKey);
      expect(sentFarmScopes).toEqual([null, null, null]);

      // Retain another ambiguous submission, then prove a different actor
      // cannot load that same persisted key even with no tenant selected.
      fail = true;
      await catchError(apiFetch("/api/auth/farms", init));
      const retainedActorOneKey = keys[3];
      expect(keys[4]).toBe(retainedActorOneKey);
      clearIdempotencyRequestState();
      setAccessToken(ACTOR_TWO_TOKEN);
      setCurrentFarmId(null);
      fail = false;
      await apiFetch("/api/auth/farms", init);
      expect(keys[5]).not.toBe(retainedActorOneKey);
    });

    it("writes only an opaque digest/key before send, then reuses and removes it", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      setCurrentFarmId("private-farm-name");
      const keys: string[] = [];
      const recordsSeenBeforeSend: string[] = [];
      fetchMock.mockImplementation(async (_input, init) => {
        keys.push(requestKey(init)!);
        recordsSeenBeforeSend.push(
          window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY) ?? "",
        );
        if (keys.length <= 2) throw new TypeError("offline after send");
        return jsonResponse(201, { id: 1, response_secret: "server-response-secret" });
      });
      const init = {
        method: "POST",
        headers: { "X-Private-Note": "header-secret" },
        body: JSON.stringify({ seller: "body-secret", amount: 500 }),
      };

      expect(await catchError(apiFetch("/api/purchases/new", init))).toBeInstanceOf(
        TypeError,
      );

      const persisted = window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)!;
      expect(recordsSeenBeforeSend[0]).not.toBe("");
      expect(storedRecords()).toHaveLength(1);
      expect(Object.keys(storedRecords()[0]).sort()).toEqual([
        "digest",
        "expiresAt",
        "key",
        "version",
      ]);
      expect(storedRecords()[0].digest).toMatch(/^[0-9a-f]{64}$/);
      expect(storedRecords()[0].key).toBe(keys[0]);
      expect(storedRecords()[0].expiresAt).toBeLessThanOrEqual(Date.now() + 2 * 60 * 1000);
      // The stamp is exactly one TTL in the future — a flipped sign would
      // expire the recovery key the moment it is written.
      expect(storedRecords()[0].expiresAt).toBeGreaterThan(Date.now());
      for (const secret of [
        "body-secret",
        "header-secret",
        ACTOR_ONE_TOKEN,
        "private-farm-name",
        "server-response-secret",
      ]) {
        expect(persisted).not.toContain(secret);
      }

      // Realm memory and the auth epoch are both replaced, as on reload or a
      // logout/login round trip by the same actor.
      clearIdempotencyRequestState();
      setAccessToken(null);
      setAccessToken(ACTOR_ONE_TOKEN);
      await apiFetch("/api/purchases/new", init);

      expect(keys).toHaveLength(3);
      expect(new Set(keys).size).toBe(1);
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
    });

    it("removes persisted state after success and a definite 422 rejection", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      let recordDuringSuccess: string | null = null;
      fetchMock.mockImplementationOnce(async () => {
        recordDuringSuccess = window.sessionStorage.getItem(
          IDEMPOTENCY_SESSION_STORAGE_KEY,
        );
        return jsonResponse(201, { id: 1 });
      });

      await apiFetch("/api/finance/new", { method: "POST", body: "{}" });
      expect(recordDuringSuccess).not.toBeNull();
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();

      fetchMock.mockResolvedValueOnce(
        jsonResponse(422, { detail: "definite validation rejection" }),
      );
      expect(
        await catchError(apiFetch("/api/finance/new", { method: "POST", body: "{}" })),
      ).toBeInstanceOf(ApiError);
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
    });

    it("retains a retryable HTTP failure without persisting its response body", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      fetchMock.mockResolvedValueOnce(
        jsonResponse(500, { detail: "private-server-response" }),
      );

      expect(
        await catchError(apiFetch("/api/finance/new", { method: "POST", body: "{}" })),
      ).toBeInstanceOf(ApiError);

      const persisted = window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)!;
      expect(persisted).not.toContain("private-server-response");
      expect(storedRecords()).toHaveLength(1);
    });

    it("drops malformed and expired records and never reuses their keys", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      window.sessionStorage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, "{not-json");
      fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: 1 }));

      await expect(
        apiFetch("/api/feeding/mix", { method: "POST", body: "{}" }),
      ).resolves.toEqual({ id: 1 });
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();

      const keys: string[] = [];
      fetchMock.mockImplementation(async (_input, init) => {
        keys.push(requestKey(init)!);
        if (keys.length <= 2) throw new TypeError("offline");
        return jsonResponse(201, { id: 1 });
      });
      await catchError(apiFetch("/api/feeding/mix", { method: "POST", body: "{}" }));
      const records = storedRecords();
      records[0].expiresAt = Date.now() - 1;
      window.sessionStorage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, JSON.stringify(records));
      clearIdempotencyRequestState();

      await apiFetch("/api/feeding/mix", { method: "POST", body: "{}" });
      expect(keys[2]).not.toBe(keys[0]);
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
    });

    it("caps and sanitizes persisted records at 128 entries", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      const expiresAt = Date.now() + 60_000;
      const oversized = Array.from({ length: 160 }, (_, index) => ({
        version: 1,
        digest: index.toString(16).padStart(64, "0"),
        key: `00000000-0000-4000-8000-${index.toString(16).padStart(12, "0")}`,
        expiresAt,
        forbidden_extra_field: "must be removed",
      }));
      window.sessionStorage.setItem(
        IDEMPOTENCY_SESSION_STORAGE_KEY,
        JSON.stringify(oversized),
      );
      fetchMock.mockRejectedValue(new TypeError("offline"));

      await catchError(
        apiFetch("/api/feeding/inventory/7/add", { method: "POST", body: "{}" }),
      );

      const records = storedRecords();
      expect(records).toHaveLength(128);
      expect(
        records.every(
          (record) =>
            Object.keys(record).sort().join(",") === "digest,expiresAt,key,version",
        ),
      ).toBe(true);
    });

    it("continues realm-only when storage access or quota writes fail", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new DOMException("blocked", "SecurityError");
      });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("quota", "QuotaExceededError");
      });
      vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
        throw new DOMException("blocked", "SecurityError");
      });
      fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: 1 }));

      await expect(
        apiFetch("/api/finance/new", { method: "POST", body: "{}" }),
      ).resolves.toEqual({ id: 1 });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("falls back to realm-only when Web Crypto digesting fails", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      const cryptography = globalThis.crypto;
      vi.stubGlobal("crypto", {
        randomUUID: cryptography.randomUUID.bind(cryptography),
        getRandomValues: cryptography.getRandomValues.bind(cryptography),
        subtle: { digest: vi.fn().mockRejectedValue(new Error("digest unavailable")) },
      });
      const keys: string[] = [];
      fetchMock.mockImplementation(async (_input, init) => {
        keys.push(requestKey(init)!);
        if (keys.length <= 2) throw new TypeError("offline");
        return jsonResponse(201, { id: 1 });
      });
      const request = () =>
        apiFetch("/api/feeding/dispense", { method: "POST", body: "{}" });

      await catchError(request());
      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
      await request();
      expect(new Set(keys.slice(0, 3)).size).toBe(1);

      clearIdempotencyRequestState();
      await request();
      expect(keys[3]).not.toBe(keys[0]);
    });

    it("isolates persisted keys by actor and farm while memory stays session-scoped", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      setCurrentFarmId("17");
      const keys: string[] = [];
      let fail = true;
      fetchMock.mockImplementation(async (_input, init) => {
        keys.push(requestKey(init)!);
        if (fail) throw new TypeError("offline");
        return jsonResponse(201, { id: 1 });
      });
      const request = () =>
        apiFetch("/api/finance/new", { method: "POST", body: "{}" });

      await catchError(request());
      const actorOneFarm17Key = keys[0];
      expect(keys[1]).toBe(actorOneFarm17Key);
      fail = false;

      clearIdempotencyRequestState();
      setCurrentFarmId("18");
      await request();
      const actorOneFarm18Key = keys[2];
      expect(actorOneFarm18Key).not.toBe(actorOneFarm17Key);

      clearIdempotencyRequestState();
      setCurrentFarmId("17");
      setAccessToken(ACTOR_TWO_TOKEN);
      await request();
      const actorTwoFarm17Key = keys[3];
      expect(actorTwoFarm17Key).not.toBe(actorOneFarm17Key);
      expect(actorTwoFarm17Key).not.toBe(actorOneFarm18Key);
    });

    it("never persists a caller-owned key", async () => {
      setAccessToken(ACTOR_ONE_TOKEN);
      fetchMock.mockResolvedValueOnce(jsonResponse(201, { id: 1 }));

      await apiFetch("/api/finance/new", {
        method: "POST",
        headers: { "Idempotency-Key": "caller-key-must-not-be-stored" },
        body: "{}",
      });

      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
    });
  });
});
