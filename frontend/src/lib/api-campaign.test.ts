/**
 * api layer — fresh-domain mutation campaign kills (2026-09): the idempotency
 * persistence rules (shape validation, expiry window, dedupe, ordering, cap,
 * canonical rewrite) and route allowlist, backend URL safety, 422 validation
 * mapping, and farm-data invalidation prefixes.
 */

import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  apiValidationErrors,
  applyApiValidationToForm,
  extractValidationIssues,
  setAccessToken,
} from "@/lib/api-client";
import { assertSafeBackendUrl } from "@/lib/backend-rewrites";
import {
  IDEMPOTENCY_SESSION_STORAGE_KEY,
  isIdempotencyProtectedMutation,
  readPersistedRecords,
} from "@/lib/idempotent-request";
import { invalidateFarmData } from "@/lib/query-invalidation";

const NOW = 1_700_000_000_000;
const KEY = "11111111-2222-4333-8444-555555555555";
const DIGEST = "a".repeat(64);

function seedStorage(records: unknown[]): Storage {
  const store: Storage = {
    length: 0,
    key: () => null,
    getItem: (k: string) => (k === IDEMPOTENCY_SESSION_STORAGE_KEY ? JSON.stringify(records) : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  } as unknown as Storage;
  return store;
}

const record = (overrides: Record<string, unknown> = {}) => ({
  version: 1,
  digest: DIGEST,
  key: KEY,
  expiresAt: NOW + 30_000,
  ...overrides,
});

describe("readPersistedRecords — campaign kills", () => {
  it("accepts a well-formed in-window record unchanged", () => {
    const storage = seedStorage([record()]);
    expect(readPersistedRecords(storage, NOW)).toEqual([record()]);
  });

  it("rejects malformed storage content without throwing", () => {
    for (const garbage of ["not json", "42", '"string"', "null"]) {
      const storage: Storage = {
        length: 0,
        key: () => null,
        getItem: () => garbage,
        setItem: vi.fn(),
        removeItem: vi.fn(),
        clear: vi.fn(),
      } as unknown as Storage;
      expect(readPersistedRecords(storage, NOW)).toEqual([]);
    }
  });

  it("rewrites malformed storage to the empty canonical form on read", () => {
    const removeItem = vi.fn();
    const storage: Storage = {
      length: 0,
      key: () => null,
      getItem: () => "not json",
      setItem: vi.fn(),
      removeItem,
      clear: vi.fn(),
    } as unknown as Storage;
    expect(readPersistedRecords(storage, NOW)).toEqual([]);
    expect(removeItem).toHaveBeenCalledWith(IDEMPOTENCY_SESSION_STORAGE_KEY);
  });

  it("drops records outside the retry window on both ends", () => {
    const storage = seedStorage([
      record({ digest: "b".repeat(64), key: KEY, expiresAt: NOW }), // expired
      record({ digest: "c".repeat(64), key: KEY, expiresAt: NOW + 1 }), // live
    ]);
    const kept = readPersistedRecords(storage, NOW);
    expect(kept.map((r) => r.digest)).toEqual(["c".repeat(64)]);

    // A key dated far in the future is as untrustworthy as an expired one.
    const farFuture = seedStorage([record({ expiresAt: NOW + 10 * 60 * 1000 })]);
    expect(readPersistedRecords(farFuture, NOW)).toEqual([]);
  });

  it("drops structurally invalid records field by field", () => {
    const bad = [
      record({ version: 2 }),
      record({ digest: "zz" }), // not sha hex
      record({ digest: "b".repeat(63) }), // wrong length
      record({ key: "not-a-uuid" }),
      record({ key: "11111111-2222-1333-8444-555555555555" }), // not v4
      record({ key: "11111111-2222-4333-f444-555555555555" }), // bad variant nibble
      record({ expiresAt: "soon" }),
      record({ expiresAt: Number.NaN }),
      "just a string",
      null,
      42,
    ];
    expect(readPersistedRecords(seedStorage(bad), NOW)).toEqual([]);
  });

  it("deduplicates digests keeping the first occurrence", () => {
    const storage = seedStorage([
      record({ key: "11111111-2222-4333-8444-555555555555" }),
      record({ key: "99999999-2222-4333-8444-555555555555" }),
    ]);
    const kept = readPersistedRecords(storage, NOW);
    expect(kept).toHaveLength(1);
    expect(kept[0]!.key).toBe("11111111-2222-4333-8444-555555555555");
  });

  it("sorts by descending expiry and caps the list", () => {
    const many = Array.from({ length: 140 }, (_, i) =>
      record({
        digest: String(i).padStart(64, "0").replace(/ /g, "0").slice(0, 64),
        key: `${String(i).padStart(8, "0")}-2222-4333-8444-555555555555`,
        expiresAt: NOW + (i + 1) * 1000,
      }),
    );
    const kept = readPersistedRecords(seedStorage(many), NOW);
    // The TTL window tops valid expiries at now+120s; everything newer is
    // dropped, the rest sort newest-first.
    expect(kept[0]!.expiresAt).toBe(NOW + 120_000);
    expect(kept).toHaveLength(120);
  });

  it("rewrites non-canonical storage to the bounded canonical form", () => {
    const setItem = vi.fn();
    const storage: Storage = {
      length: 0,
      key: () => null,
      getItem: () =>
        JSON.stringify([
          record({ expiresAt: NOW - 1 }), // stale → dropped → rewrite needed
          record(),
        ]),
      setItem,
      removeItem: vi.fn(),
      clear: vi.fn(),
    } as unknown as Storage;
    readPersistedRecords(storage, NOW);
    expect(setItem).toHaveBeenCalledWith(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([record()]),
    );
  });
});

describe("isIdempotencyProtectedMutation — campaign kills", () => {
  it("matches exactly the protected POST routes", () => {
    const protectedRoutes = [
      "/api/auth/farms",
      "/api/finance/new",
      "/api/finance/transactions/7/correct",
      "/api/purchases/new",
      "/api/animals",
      "/api/animals/9/weight",
      "/api/tasks",
      "/api/team/workers",
      "/api/health/events",
      "/api/simulation/scenarios",
      "/api/breeding",
      "/api/kidding",
      "/api/feeding/dispense",
      "/api/feeding/mix",
      "/api/feeding/inventory/3/add",
    ];
    for (const route of protectedRoutes) {
      expect(isIdempotencyProtectedMutation(route, "POST")).toBe(true);
    }
  });

  it("refuses everything else", () => {
    for (const [route, method] of [
      ["/api/animals", "GET"],
      ["/api/animals", "PUT"],
      ["/api/animals", undefined],
      ["/api/auth/login", "POST"],
      ["/api/finance/transactions", "POST"],
      ["/api/finance/transactions/7", "POST"],
      ["/api/unknown", "POST"],
      ["/api/animals/weight", "POST"],
      ["/api/feeding/inventory/add", "POST"],
    ] as const) {
      expect(isIdempotencyProtectedMutation(route, method)).toBe(false);
    }
  });
});

describe("assertSafeBackendUrl — campaign kills", () => {
  it("accepts loopback and single-label compose hosts over either scheme", () => {
    expect(() => assertSafeBackendUrl("http://localhost:8000")).not.toThrow();
    expect(() => assertSafeBackendUrl("http://127.0.0.1:8000")).not.toThrow();
    expect(() => assertSafeBackendUrl("http://[::1]:8000")).not.toThrow();
    expect(() => assertSafeBackendUrl("http://backend:8000")).not.toThrow();
    // Internal names over TLS stay fine.
    expect(() => assertSafeBackendUrl("https://backend:8443")).not.toThrow();
  });

  it("refuses anything reachable from other hosts — https no longer bypasses (INFRA-2)", () => {
    expect(() => assertSafeBackendUrl("http://10.0.0.5:8000")).toThrow();
    expect(() => assertSafeBackendUrl("http://api.goatfarm.example")).toThrow();
    // The proxy forwards the bearer token and refresh cookie; a public
    // hostname is an exfil path over TLS exactly as over plaintext.
    expect(() => assertSafeBackendUrl("https://api.goatfarm.example")).toThrow();
    expect(() => assertSafeBackendUrl("https://10.0.0.5:8443")).toThrow();
    expect(() => assertSafeBackendUrl("not a url")).toThrow();
  });

  it("admits digit-suffixed compose hosts but nothing IP-like, bracketed or punctuated", () => {
    expect(() => assertSafeBackendUrl("http://backend9:8000")).not.toThrow();
    expect(() => assertSafeBackendUrl("http://1backend:8000")).toThrow();
    expect(() => assertSafeBackendUrl("http://-backend:8000")).toThrow();
    expect(() => assertSafeBackendUrl("http://backend_x:8000")).toThrow();
    expect(() => assertSafeBackendUrl("http://fe80::1:8000")).toThrow();
  });
});

describe("extractValidationIssues — campaign kills", () => {
  it("maps a well-formed 422 detail into issues with stringified loc segments", () => {
    expect(
      extractValidationIssues({ detail: [{ msg: "field required", loc: ["body", "kids", 0, "tag"] }] }),
    ).toEqual([{ loc: ["body", "kids", "0", "tag"], msg: "field required" }]);
  });

  it("ignores malformed detail shapes", () => {
    expect(extractValidationIssues({ detail: "plain string" })).toEqual([]);
    expect(extractValidationIssues({})).toEqual([]);
    expect(extractValidationIssues({ detail: [{ nope: 1 }] })).toEqual([]);
    expect(extractValidationIssues({ detail: [{ msg: "x", loc: [1, {}] }] })).toEqual([]);
    expect(extractValidationIssues({ detail: [{ msg: 7, loc: ["a"] }] })).toEqual([]);
    expect(extractValidationIssues(null)).toEqual([]);
    expect(extractValidationIssues("body")).toEqual([]);
    // A non-array, non-iterable detail and null entries must not throw.
    expect(extractValidationIssues({ detail: { nested: true } })).toEqual([]);
    expect(extractValidationIssues({ detail: 42 })).toEqual([]);
    expect(extractValidationIssues({ detail: [null] })).toEqual([]);
  });

  it("carries the issues on ApiError through apiValidationErrors", () => {
    const issues = [{ loc: ["name"], msg: "too long" }];
    const err = new ApiError(422, "Request failed", issues);
    expect(apiValidationErrors(err)).toBe(issues);
    expect(apiValidationErrors(new Error("network"))).toEqual([]);
  });
});

describe("applyApiValidationToForm — campaign kills", () => {
  const fields = ["doe_id", "buck_id", "method", "breeding_date"];

  function errWith(issues: Array<{ loc: string[]; msg: string }>) {
    return new ApiError(422, "Request failed", issues);
  }

  it("maps known fields inline and reports the remainder", () => {
    const setError = vi.fn();
    const unmapped = applyApiValidationToForm(
      errWith([
        { loc: ["body", "doe_id"], msg: "Select a doe" },
        { loc: ["body", "breeding_date"], msg: "Date can't be in the future" },
      ]),
      setError,
      fields,
    );
    expect(unmapped).toEqual([]);
    expect(setError).toHaveBeenCalledWith("doe_id", "Select a doe");
    expect(setError).toHaveBeenCalledWith("breeding_date", "Date can't be in the future");
  });

  it("falls through unknown fields for the banner", () => {
    const setError = vi.fn();
    const unmapped = applyApiValidationToForm(
      errWith([
        { loc: ["body", "doe_id"], msg: "Select a doe" },
        { loc: ["body", "mystery"], msg: "unknown field" },
        { loc: ["query", "offset"], msg: "bad offset" },
      ]),
      setError,
      fields,
    );
    expect(setError).toHaveBeenCalledTimes(1);
    expect(unmapped.map((i) => i.msg)).toEqual(["unknown field", "bad offset"]);
    // Unmapped issues keep their full loc verbatim for the banner.
    expect(unmapped.map((i) => i.loc)).toEqual([["body", "mystery"], ["query", "offset"]]);
  });

  it("maps by root segment when the full dotted path is unknown", () => {
    const setError = vi.fn();
    const unmapped = applyApiValidationToForm(
      errWith([{ loc: ["body", "kids", "0", "tag"], msg: "Max 50 characters" }]),
      setError,
      ["kids", "notes"],
    );
    // "kids.0.tag" is not a registered field, but its root "kids" is.
    expect(setError).toHaveBeenCalledWith("kids", "Max 50 characters");
    expect(unmapped).toEqual([]);
  });

  it("ignores non-ApiError inputs", () => {
    expect(applyApiValidationToForm(new Error("network"), vi.fn(), fields)).toEqual([]);
  });

  it("reports a body-only loc for the banner without touching any field", () => {
    const setError = vi.fn();
    const unmapped = applyApiValidationToForm(
      errWith([{ loc: ["body"], msg: "request body invalid" }]),
      setError,
      fields,
    );
    expect(setError).not.toHaveBeenCalled();
    expect(unmapped.map((i) => i.msg)).toEqual(["request body invalid"]);
  });
});

describe("invalidateFarmData — campaign kills", () => {
  it("invalidates farm-scoped and snapshot queries but not auth or unrelated keys", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const key of [
      ["/api/animals"],
      ["/api/animals", 5],
      ["/api/simulation/herd-snapshot"],
      ["/api/breeding", "list"],
      ["/api/auth/permissions"],
      ["/api/unrelated"],
    ]) {
      client.setQueryData(key, "seed");
    }

    invalidateFarmData(client);
    // Invalidation marks land synchronously on matching cache entries.
    const state = (key: unknown[]) => client.getQueryState(key);
    expect(state(["/api/animals"])?.isInvalidated).toBe(true);
    expect(state(["/api/animals", 5])?.isInvalidated).toBe(true);
    expect(state(["/api/simulation/herd-snapshot"])?.isInvalidated).toBe(true);
    expect(state(["/api/breeding", "list"])?.isInvalidated).toBe(true);
    expect(state(["/api/auth/permissions"])?.isInvalidated).toBe(false);
    expect(state(["/api/unrelated"])?.isInvalidated).toBe(false);
  });
});

describe("apiFetch error sentences — campaign kills", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("session-token");
  });

  afterEach(async () => {
    setAccessToken(null);
    fetchMock.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  function json(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  it("composes a plain-language sentence with per-field specifics for 422s", async () => {
    fetchMock.mockResolvedValueOnce(
      json(422, { detail: [{ loc: ["body", "kids", 0, "tag"], msg: "field required" }] }),
    );
    const error = (await rejectionOf(apiFetch("/api/kidding"))) as ApiError;
    expect(error.status).toBe(422);
    expect(error.detail).toBe(
      "The server rejected these values (kids.0.tag: field required). Check the entered data and try again.",
    );
    expect(error.validationIssues).toEqual([{ loc: ["body", "kids", "0", "tag"], msg: "field required" }]);
  });

  it("surfaces array specifics verbatim for non-422 statuses and a fallback for empty arrays", async () => {
    fetchMock.mockResolvedValueOnce(
      json(502, { detail: [{ loc: ["path", "item_id"], msg: "not found" }] }),
    );
    const gateway = (await rejectionOf(apiFetch("/api/animals/7"))) as ApiError;
    expect(gateway.detail).toBe("path.item_id: not found");

    fetchMock.mockResolvedValueOnce(json(422, { detail: [] }));
    const empty = (await rejectionOf(apiFetch("/api/animals"))) as ApiError;
    expect(empty.detail).toBe(
      "The server rejected these values. Check the entered data and try again.",
    );
  });

  it("rejects URL-invalid paths with the unsafe-path error, not a TypeError", async () => {
    const error = (await rejectionOf(apiFetch("//[::1"))) as Error;
    expect(error.name).toBe("UnsafeApiPathError");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected a rejection");
}
