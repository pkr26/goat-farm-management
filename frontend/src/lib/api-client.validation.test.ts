/**
 * Structured 422 handling: FastAPI validation arrays are preserved next to
 * the flattened detail sentence, apiValidationErrors exposes them, and
 * applyApiValidationToForm maps loc tails onto known RHF field names while
 * unknown fields fall through for the caller's banner. Global fetch is
 * stubbed directly, same as the other api-client suites.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  apiValidationErrors,
  applyApiValidationToForm,
  setAccessToken,
} from "./api-client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: `Status ${status}`,
    headers: { "Content-Type": "application/json" },
  });
}

async function rejectWith(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (err) {
    return err;
  }
  throw new Error("expected the request to reject");
}

describe("apiValidationErrors", () => {
  beforeEach(() => {
    setAccessToken("token", 1);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(422, {
        detail: [
          { loc: ["body", "dose"], msg: "Input should be a valid string" },
          { loc: ["body", "expected_animal_ids", 0], msg: "Input should be a valid integer" },
        ],
      })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("preserves loc (with numeric indices stringified) and msg from a 422", async () => {
    const err = await rejectWith(apiFetch("/api/health/events", { method: "POST" }));
    expect(apiValidationErrors(err)).toEqual([
      { loc: ["body", "dose"], msg: "Input should be a valid string" },
      { loc: ["body", "expected_animal_ids", "0"], msg: "Input should be a valid integer" },
    ]);
    // The flattened sentence the banner uses is unchanged behaviour.
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).detail).toContain("dose: Input should be a valid string");
  });

  it("yields an empty list for non-422 errors, non-ApiErrors and 422s without an array", async () => {
    expect(apiValidationErrors(new ApiError(500, "boom"))).toEqual([]);
    expect(apiValidationErrors(new Error("network"))).toEqual([]);
    expect(apiValidationErrors(null)).toEqual([]);
    expect(apiValidationErrors(undefined)).toEqual([]);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(422, { detail: "plain sentence" })),
    );
    const err = await rejectWith(apiFetch("/api/health/events", { method: "POST" }));
    expect(apiValidationErrors(err)).toEqual([]);
  });
});

describe("applyApiValidationToForm", () => {
  const FIELDS = ["dose", "cost", "kids", "kids.0.tag"];

  function collect(err: unknown, fields: readonly string[] = FIELDS) {
    const applied: Array<[string, string]> = [];
    const unmapped = applyApiValidationToForm(
      err,
      (field, message) => applied.push([field, message]),
      fields,
    );
    return { applied, unmapped };
  }

  function issue422(issues: Array<{ loc: Array<string | number>; msg: string }>): ApiError {
    return new ApiError(
      422,
      "The server rejected these values. Check the entered data and try again.",
      issues.map((i) => ({ loc: i.loc.map(String), msg: i.msg })),
    );
  }

  it("maps a body-rooted loc tail onto the matching field", () => {
    const { applied, unmapped } = collect(
      issue422([{ loc: ["body", "dose"], msg: "Enter a valid dose." }]),
    );
    expect(applied).toEqual([["dose", "Enter a valid dose."]]);
    expect(unmapped).toEqual([]);
  });

  it("maps locs without the body prefix and full dotted paths", () => {
    const { applied } = collect(
      issue422([
        { loc: ["cost"], msg: "Cost must be finite." },
        { loc: ["body", "kids", "0", "tag"], msg: "Tag is too long." },
      ]),
    );
    // The nested path resolves against the registered dotted name.
    expect(applied).toContainEqual(["cost", "Cost must be finite."]);
    expect(applied).toContainEqual(["kids.0.tag", "Tag is too long."]);
  });

  it("falls back to the root segment for a registered parent field", () => {
    const { applied, unmapped } = collect(
      issue422([{ loc: ["body", "kids", "3", "weight"], msg: "Weight must be ≥ 0." }]),
    );
    expect(applied).toEqual([["kids", "Weight must be ≥ 0."]]);
    expect(unmapped).toEqual([]);
  });

  it("falls through unknown fields, an empty tail and non-422 errors", () => {
    const unknownField = collect(issue422([{ loc: ["body", "not_a_form_field"], msg: "Nope." }]));
    expect(unknownField.applied).toEqual([]);
    expect(unknownField.unmapped).toHaveLength(1);

    const bareBody = collect(issue422([{ loc: ["body"], msg: "Body was invalid." }]));
    expect(bareBody.applied).toEqual([]);
    expect(bareBody.unmapped).toHaveLength(1);

    const queryScoped = collect(
      issue422([{ loc: ["query", "dose"], msg: "Bad query param." }]),
    );
    // Only the synthetic "body" root is dropped; a query-scoped loc maps
    // solely if its root segment were a registered field, which it is not.
    expect(queryScoped.applied).toEqual([]);
    expect(queryScoped.unmapped).toHaveLength(1);

    const notAnApiError = collect(new TypeError("fetch failed"));
    expect(notAnApiError.applied).toEqual([]);
    expect(notAnApiError.unmapped).toEqual([]);
  });

  it("splits a mixed 422 into mapped fields plus an unmapped remainder", () => {
    const { applied, unmapped } = collect(
      issue422([
        { loc: ["body", "cost"], msg: "Cost cannot be negative." },
        { loc: ["body", "drifted_field"], msg: "Unknown to this form." },
      ]),
    );
    expect(applied).toEqual([["cost", "Cost cannot be negative."]]);
    // The remainder keeps the original loc verbatim — it is diagnostic
    // output for the banner, not a form path.
    expect(unmapped).toEqual([{ loc: ["body", "drifted_field"], msg: "Unknown to this form." }]);
  });
});
