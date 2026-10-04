/**
 * custom-instance query stripping, first-pair precision: `slice(queryStart + 1)`
 * must skip ONLY the "?" — slicing one character deeper decapitates the first query
 * key ("a=1" becomes "=1"), so a "null"-valued first param is no longer recognised
 * while the URL that goes on the wire silently changes shape.
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
