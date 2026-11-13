/**
 * Query stripping keeps the first decoded key intact. URLSearchParams owns the
 * query delimiter; removing a key character or a literal question mark from
 * the key changes the request that goes on the wire.
 */

import { describe, expect, it, vi } from "vitest";

import { customInstance } from "./custom-instance";

const apiClientMocks = vi.hoisted(() => ({ apiFetchEnvelope: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
  ApiError: class MockApiError extends Error {},
  apiFetchEnvelope: apiClientMocks.apiFetchEnvelope,
}));

describe("stripNullQueryValues first-pair precision", () => {
  it("keeps the first query key intact while stripping a later null value", async () => {
    apiClientMocks.apiFetchEnvelope.mockResolvedValue({ data: {}, status: 200 });

    await customInstance("/api/things?a=1&bucket=null");

    expect(apiClientMocks.apiFetchEnvelope).toHaveBeenCalledTimes(1);
    expect(apiClientMocks.apiFetchEnvelope.mock.calls[0]?.[0]).toBe("/api/things?a=1");
  });

  it("leaves a url untouched when no value needs stripping", async () => {
    apiClientMocks.apiFetchEnvelope.mockClear();
    apiClientMocks.apiFetchEnvelope.mockResolvedValue({ data: {}, status: 200 });

    await customInstance("/api/things?a=1&bucket=2");

    expect(apiClientMocks.apiFetchEnvelope.mock.calls[0]?.[0]).toBe(
      "/api/things?a=1&bucket=2",
    );
  });
});
