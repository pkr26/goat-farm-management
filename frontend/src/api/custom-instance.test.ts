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
