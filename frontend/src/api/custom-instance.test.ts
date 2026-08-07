// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// customInstance used to wrap every successful call as
// {data, status: 200, headers: new Headers()} — discarding the response's
// real status and headers, so a 201 came out as 200 and every page's
// `status === 200` check was meaningless. It now resolves through
// apiFetchEnvelope, which keeps the actual status (201/204) and headers.
// Global fetch is stubbed directly (same style as lib/api-client.test.ts).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";

import { customInstance } from "./custom-instance";

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
});
