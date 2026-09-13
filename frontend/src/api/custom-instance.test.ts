// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// customInstance used to wrap every successful call as
// {data, status: 200, headers: new Headers()} — discarding the response's
// real status and headers, so a 201 came out as 200 and every page's
// `status === 200` check was meaningless. It now resolves through
// apiFetchEnvelope, which keeps the actual status (201/204) and headers.
// Global fetch is stubbed directly (same style as lib/api-client.test.ts).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, setAccessToken, setCurrentFarmId } from "@/lib/api-client";

import { customInstance, type ErrorType } from "./custom-instance";
import { healthzHealthzGet, readyzReadyzGet } from "./generated/endpoints";

interface Envelope {
  data: unknown;
  status: number;
  headers: Headers;
}

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** Compile-time assertion for every Orval hook's resolved error generic. */
function generatedErrorStatus(error: ErrorType<unknown>): number {
  return error.status;
}

describe("customInstance", () => {
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
    fetchMock.mockReset();
    // Let any microtask-scheduled refreshPromise reset (setTimeout 0) flush.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("keeps a 201 Created status and parses the body", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(201, { id: 2, tag_number: "G-101" }),
    );

    const result = (await customInstance("/api/animals", { method: "POST" })) as Envelope;

    expect(result.status).toBe(201);
    expect(result.data).toEqual({ id: 2, tag_number: "G-101" });
  });

  it("passes the response headers through", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [], { "X-Total-Count": "42" }));

    const result = (await customInstance("/api/animals")) as Envelope;

    expect(result.status).toBe(200);
    expect(result.headers.get("x-total-count")).toBe("42");
  });

  it("handles a 204 No Content without parsing a body", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    const result = (await customInstance("/api/tasks/1", { method: "DELETE" })) as Envelope;

    expect(result.status).toBe(204);
    expect(result.data).toBeUndefined();
  });

  it.each([
    ["healthz", healthzHealthzGet, "/healthz", { status: "ok" }],
    ["readyz", readyzReadyzGet, "/readyz", { status: "ready" }],
  ] as const)(
    "allows the generated %s probe through the same-origin transport",
    async (_name, request, path, payload) => {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, payload));

      const result = await request();

      expect(fetchMock).toHaveBeenCalledWith(
        path,
        expect.objectContaining({ method: "GET", credentials: "include" }),
      );
      expect(result).toMatchObject({ status: 200, data: payload });
    },
  );

  it("types and throws non-2xx generated calls as ApiError", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, { detail: [{ loc: ["body", "name"], msg: "Field required" }] }),
    );

    let caught: unknown;
    try {
      await customInstance("/api/auth/farms", { method: "POST", body: "{}" });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    if (!(caught instanceof ApiError)) throw new Error("Expected ApiError");
    expect(generatedErrorStatus(caught)).toBe(422);
    expect(caught.detail).toBe(
      "The server rejected these values (name: Field required). Check the entered data and try again.",
    );
  });
});

function flushMacrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("customInstance — null query stripping", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));
    setAccessToken("session-token");
    setCurrentFarmId("1");
  });

  afterEach(async () => {
    setAccessToken(null);
    setCurrentFarmId(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await flushMacrotasks();
  });

  it("strips literal null params everywhere except the free-text q param", async () => {
    await customInstance("/api/animals?bucket=null&q=null&name=Asha&tag=null");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const calledUrl = String(fetchMock.mock.calls[0]![0]);
    expect(calledUrl).toBe("/api/animals?q=null&name=Asha");

    // The kept param must remain a genuine query pair: any mangling of the
    // question-mark split (whole-url or off-by-one parsing) re-encodes it.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, []));
    await customInstance("/api/animals?name=Asha&tag=null");
    expect(String(fetchMock.mock.calls[1]![0])).toBe("/api/animals?name=Asha");
  });

  it("preserves percent-encoded query characters byte-for-byte", async () => {
    await customInstance("/api/animals?name=Asha%20Devi");

    // URLSearchParams.toString() would re-encode the space as '+': only the
    // untouched fast path keeps the original bytes.
    expect(String(fetchMock.mock.calls[0]![0])).toBe("/api/animals?name=Asha%20Devi");
  });

  it("leaves a clean query string untouched, down to the exact characters", async () => {
    await customInstance("/api/animals?name=Asha&page=2");

    expect(String(fetchMock.mock.calls[0]![0])).toBe("/api/animals?name=Asha&page=2");
  });

  it("passes a path without a query straight through", async () => {
    await customInstance("/api/animals");

    expect(String(fetchMock.mock.calls[0]![0])).toBe("/api/animals");
  });
});

describe("customInstance — error detail extraction", () => {
  const detailFetch = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", detailFetch);
  });

  it("surfaces a plain string detail verbatim", async () => {
    detailFetch.mockResolvedValueOnce(jsonResponse(500, { detail: "Database is upgrading" }));
    const error = await rejectionOf(customInstance("/api/animals"));
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).detail).toBe("Database is upgrading");
  });

  it("renders a 422 issue whose loc is not an array without a location prefix", async () => {
    detailFetch.mockResolvedValueOnce(
      jsonResponse(422, { detail: [{ loc: "unexpected", msg: "Unlocatable issue." }] }),
    );
    const error = await rejectionOf(customInstance("/api/animals"));
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).detail).toBe(
      "The server rejected these values (Unlocatable issue.). Check the entered data and try again.",
    );
  });
});

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the request to reject");
}

describe("customInstance — issue extraction is 422-only", () => {
  const detailFetch = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", detailFetch);
  });

  it("keeps ApiError.issues empty for a non-422 with an array detail", async () => {
    detailFetch.mockResolvedValueOnce(
      jsonResponse(500, { detail: [{ loc: ["body", "dose"], msg: "Dose is required." }] }),
    );
    const error = (await rejectionOf(customInstance("/api/animals"))) as ApiError;
    expect(error.status).toBe(500);
    // The per-field array is a 422 contract; a 500's body is detail text only.
    expect(error.validationIssues).toEqual([]);
  });
});
