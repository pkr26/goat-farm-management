import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearIdempotencyRequestState,
  clearPersistedIdempotencyRequestState,
  IDEMPOTENCY_SESSION_STORAGE_KEY,
  isIdempotencyProtectedMutation,
  runIdempotencyProtectedRequest,
} from "./idempotent-request";

const NOW = 1_800_000_000_000;
const TTL_MS = 2 * 60 * 1000;
const VALID_DIGEST = "a".repeat(64);
const VALID_KEY = "12345678-1234-4123-8123-123456789abc";

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

function runProtected<T>(
  execute: (init: RequestInit) => Promise<T>,
  overrides: Partial<{
    url: string;
    init: RequestInit;
    farmScope: string | null;
    sessionScope: number;
    actorScope: string | null;
  }> = {},
): Promise<T> {
  return runIdempotencyProtectedRequest({
    url: overrides.url ?? "/api/finance/new",
    init: overrides.init ?? { method: "POST", body: "{}" },
    farmScope:
      overrides.farmScope === undefined ? "farm-17" : overrides.farmScope,
    sessionScope: overrides.sessionScope ?? 4,
    actorScope:
      overrides.actorScope === undefined ? "actor-101" : overrides.actorScope,
    execute,
  });
}

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the protected request to reject");
}

describe("idempotency mutation boundaries", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearIdempotencyRequestState();
    vi.spyOn(Date, "now").mockReturnValue(NOW);
  });

  afterEach(() => {
    clearIdempotencyRequestState();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    "/prefix/api/finance/transactions/42/correct",
    "/api/finance/transactions/42/correct/suffix",
    "/prefix/api/animals/42/weight",
    "/api/animals/42/weight/suffix",
    "/prefix/api/feeding/inventory/7/add",
    "/api/feeding/inventory/7/add/suffix",
  ])("does not protect a route that only contains the allowlisted shape: %s", (url) => {
    expect(isIdempotencyProtectedMutation(url, "POST")).toBe(false);
  });

  it("normalizes method case and permits a query on an exact protected route", () => {
    expect(
      isIdempotencyProtectedMutation(
        "/api/finance/transactions/42/correct?source=review",
        "post",
      ),
    ).toBe(true);
    expect(
      isIdempotencyProtectedMutation(
        "/api/finance/transactions/42/correct?source=review",
        "GET",
      ),
    ).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/finance/new", undefined)).toBe(
      false,
    );
  });

  it.each([
    ["a primitive", 7],
    ["null", null],
    ["an array", []],
    ["the wrong version", { version: 2 }],
    ["a non-string digest", { digest: 42 }],
    ["a digest with a prefix", { digest: `x${VALID_DIGEST}` }],
    ["a digest with a suffix", { digest: `${VALID_DIGEST}x` }],
    ["a non-string key", { key: 42 }],
    ["a UUID with a prefix", { key: `x${VALID_KEY}` }],
    ["a UUID with a suffix", { key: `${VALID_KEY}x` }],
    ["a non-number expiry", { expiresAt: "later" }],
    ["an expiry at the current instant", { expiresAt: NOW }],
    ["an expiry beyond the retry window", { expiresAt: NOW + TTL_MS + 1 }],
  ])("sanitizes a persisted record containing %s", async (_label, change) => {
    const candidate =
      change === null || typeof change !== "object" || Array.isArray(change)
        ? change
        : {
            version: 1,
            digest: VALID_DIGEST,
            key: VALID_KEY,
            expiresAt: NOW + 30_000,
            ...change,
          };
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([candidate]),
    );
    const retryable = { status: 503 };

    expect(
      await rejectionOf(runProtected(vi.fn().mockRejectedValue(retryable))),
    ).toBe(retryable);
    await Promise.resolve();

    const records = storedRecords();
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({
      version: 1,
      expiresAt: NOW + TTL_MS,
    });
    expect(records[0].digest).toMatch(/^[0-9a-f]{64}$/);
    expect(records[0].key).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it.each(["{not-json", JSON.stringify({ version: 1 })])(
    "replaces a malformed persisted container instead of retaining it: %s",
    async (raw) => {
      window.sessionStorage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, raw);
      const retryable = { status: 503 };

      await rejectionOf(runProtected(vi.fn().mockRejectedValue(retryable)));
      await Promise.resolve();

      const records = storedRecords();
      expect(records).toHaveLength(1);
      expect(records[0].version).toBe(1);
      expect(records[0].expiresAt).toBe(NOW + TTL_MS);
    },
  );

  it("accepts the exact storage-size limit and rejects a container one byte over it", async () => {
    const storageLimit = 64 * 1024;
    const rawAtSize = (size: number) => {
      const record = {
        version: 1,
        digest: VALID_DIGEST,
        key: VALID_KEY,
        expiresAt: NOW + 30_000,
        padding: "",
      };
      const base = JSON.stringify([record]);
      const raw = JSON.stringify([
        { ...record, padding: "x".repeat(size - base.length) },
      ]);
      expect(raw).toHaveLength(size);
      return raw;
    };
    const loadContainer = async (size: number) => {
      window.sessionStorage.setItem(
        IDEMPOTENCY_SESSION_STORAGE_KEY,
        rawAtSize(size),
      );
      await rejectionOf(
        runProtected(vi.fn().mockRejectedValue({ status: 503 })),
      );
      await Promise.resolve();
      return storedRecords();
    };

    const atLimit = await loadContainer(storageLimit);
    expect(atLimit.some((record) => record.key === VALID_KEY)).toBe(true);

    clearIdempotencyRequestState();
    window.sessionStorage.clear();
    const overLimit = await loadContainer(storageLimit + 1);
    expect(overLimit.some((record) => record.key === VALID_KEY)).toBe(false);
  });

  it("deduplicates stored digests and keeps the newest valid record", async () => {
    const older = {
      version: 1,
      digest: VALID_DIGEST,
      key: VALID_KEY,
      expiresAt: NOW + 20_000,
    };
    const newer = {
      ...older,
      key: "abcdefab-cdef-4abc-8def-abcdefabcdef",
      expiresAt: NOW + 40_000,
    };
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([newer, older]),
    );
    const retryable = { status: 503 };

    await rejectionOf(runProtected(vi.fn().mockRejectedValue(retryable)));
    await Promise.resolve();

    const matching = storedRecords().filter(
      (record) => record.digest === VALID_DIGEST,
    );
    expect(matching).toEqual([newer]);
  });

  it("sorts persisted records newest-first and enforces the exact 128-record cap", async () => {
    const records = Array.from({ length: 130 }, (_, index) => ({
      version: 1,
      digest: index.toString(16).padStart(64, "0"),
      key: `00000000-0000-4000-8000-${index.toString(16).padStart(12, "0")}`,
      expiresAt: NOW + index + 1,
    }));
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify(records),
    );
    const retryable = { status: 503 };

    await rejectionOf(runProtected(vi.fn().mockRejectedValue(retryable)));
    await Promise.resolve();

    const bounded = storedRecords();
    expect(bounded).toHaveLength(128);
    expect(bounded.map((record) => record.expiresAt)).toEqual(
      [...bounded]
        .map((record) => record.expiresAt)
        .sort((left, right) => right - left),
    );
    expect(bounded.some((record) => record.digest === "0".repeat(64))).toBe(
      false,
    );
  });

  it("does not recover a matching key that falls outside the newest 128 records", async () => {
    const sentKeys: Array<string | null> = [];
    const firstExecute = vi.fn(async (init: RequestInit) => {
      sentKeys.push(new Headers(init.headers).get("Idempotency-Key"));
      throw { status: 503 };
    });
    await rejectionOf(runProtected(firstExecute));
    await Promise.resolve();
    const retained = storedRecords()[0];
    expect(retained).toBeDefined();

    const newer = Array.from({ length: 129 }, (_, index) => ({
      version: 1,
      digest: (index + 1).toString(16).padStart(64, "0"),
      key: `00000000-0000-4000-8000-${(index + 1).toString(16).padStart(12, "0")}`,
      expiresAt: NOW + index + 2,
    }));
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([...newer, { ...retained, expiresAt: NOW + 1 }]),
    );
    clearIdempotencyRequestState();
    const secondExecute = vi.fn(async (init: RequestInit) => {
      sentKeys.push(new Headers(init.headers).get("Idempotency-Key"));
      return { ok: true };
    });

    await expect(runProtected(secondExecute)).resolves.toEqual({ ok: true });

    expect(sentKeys).toHaveLength(2);
    expect(sentKeys[1]).not.toBe(sentKeys[0]);
  });

  it("uses all request dimensions when deciding whether in-flight work is identical", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn((init: RequestInit) => {
      void init;
      return new Promise<{ sequence: number }>((resolve) => {
        const sequence = releases.length;
        releases.push(() => resolve({ sequence }));
      });
    });
    const first = runProtected(execute, {
      init: {
        method: "post",
        body: '{"amount":1}',
        headers: { "X-Mode": "one" },
      },
    });
    const second = runProtected(execute, {
      init: {
        method: "POST",
        body: '{"amount":1}',
        headers: { "X-Mode": "two" },
      },
    });
    await vi.waitFor(() => expect(execute).toHaveBeenCalledTimes(2));
    const sentKeys = execute.mock.calls.map((call) =>
      new Headers(call[0].headers).get("Idempotency-Key"),
    );
    expect(sentKeys[0]).not.toBe(sentKeys[1]);

    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { sequence: 0 },
      { sequence: 1 },
    ]);
  });

  it("does not coalesce identical work submitted for different farms", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn((init: RequestInit) => {
      void init;
      return new Promise<{ ok: true }>((resolve) => {
        releases.push(() => resolve({ ok: true }));
      });
    });

    const first = runProtected(execute, {
      farmScope: "farm-17",
      actorScope: null,
    });
    const second = runProtected(execute, {
      farmScope: "farm-18",
      actorScope: null,
    });

    expect(execute).toHaveBeenCalledTimes(2);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it("does not coalesce different caller-owned idempotency keys", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn((init: RequestInit) => {
      void init;
      return new Promise<{ ok: true }>((resolve) => {
        releases.push(() => resolve({ ok: true }));
      });
    });

    const first = runProtected(execute, {
      actorScope: null,
      init: {
        method: "POST",
        body: "{}",
        headers: { "Idempotency-Key": "caller-key-one" },
      },
    });
    const second = runProtected(execute, {
      actorScope: null,
      init: {
        method: "POST",
        body: "{}",
        headers: { "Idempotency-Key": "caller-key-two" },
      },
    });

    expect(execute).toHaveBeenCalledTimes(2);
    expect(
      execute.mock.calls.map(([init]) =>
        new Headers(init.headers).get("Idempotency-Key"),
      ),
    ).toEqual(["caller-key-one", "caller-key-two"]);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it("keeps adjacent signature fields separated against concatenation collisions", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn(
      () =>
        new Promise<{ ok: true }>((resolve) => {
          releases.push(() => resolve({ ok: true }));
        }),
    );

    const first = runProtected(execute, {
      farmScope: "1",
      sessionScope: 23,
      actorScope: null,
    });
    const second = runProtected(execute, {
      farmScope: "12",
      sessionScope: 3,
      actorScope: null,
    });

    expect(execute).toHaveBeenCalledTimes(2);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it.each([
    ["null", null],
    ["a string", "offline"],
    ["an object without status", { reason: "offline" }],
  ])("automatically retries a transport rejection represented by %s", async (_label, error) => {
    const execute = vi
      .fn()
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce({ ok: true });

    await expect(runProtected(execute)).resolves.toEqual({ ok: true });
    expect(execute).toHaveBeenCalledTimes(2);
  });

  it("treats a non-numeric status property as a transport rejection", async () => {
    const execute = vi
      .fn()
      .mockRejectedValueOnce({ status: "503" })
      .mockResolvedValueOnce({ ok: true });

    await expect(runProtected(execute)).resolves.toEqual({ ok: true });
    expect(execute).toHaveBeenCalledTimes(2);
  });

  it("prefers randomUUID and does not touch the fallback entropy path", async () => {
    const randomUUID = vi.fn(() => VALID_KEY);
    const getRandomValues = vi.fn(() => {
      throw new Error("fallback entropy path should not run");
    });
    vi.stubGlobal("crypto", {
      randomUUID,
      getRandomValues,
      subtle: globalThis.crypto.subtle,
    });
    const execute = vi.fn().mockResolvedValue({ ok: true });

    await expect(
      runProtected(execute, { actorScope: null }),
    ).resolves.toEqual({ ok: true });
    expect(randomUUID).toHaveBeenCalledTimes(1);
    expect(getRandomValues).not.toHaveBeenCalled();
    expect(
      new Headers(execute.mock.calls[0][0].headers).get("Idempotency-Key"),
    ).toBe(VALID_KEY);
  });

  it("keeps a retryable key alive in memory for the complete TTL", async () => {
    let now = NOW;
    vi.mocked(Date.now).mockImplementation(() => now);
    const keys: Array<string | null> = [];
    const retryable = { status: 503 };
    const execute = vi.fn(async (init: RequestInit) => {
      keys.push(new Headers(init.headers).get("Idempotency-Key"));
      if (keys.length === 1) throw retryable;
      return { ok: true };
    });

    expect(
      await rejectionOf(runProtected(execute, { actorScope: null })),
    ).toBe(retryable);
    now += 1;
    await expect(
      runProtected(execute, { actorScope: null }),
    ).resolves.toEqual({ ok: true });
    expect(keys[1]).toBe(keys[0]);
  });

  it.each([401, 408, 409, 429, 500])(
    "retains the same key for an explicit retry after HTTP %s",
    async (status) => {
      const keys: Array<string | null> = [];
      const rejection = { status };
      const execute = vi.fn(async (init: RequestInit) => {
        keys.push(new Headers(init.headers).get("Idempotency-Key"));
        if (keys.length === 1) throw rejection;
        return { ok: true };
      });

      expect(await rejectionOf(runProtected(execute))).toBe(rejection);
      await expect(runProtected(execute)).resolves.toEqual({ ok: true });
      expect(keys).toHaveLength(2);
      expect(keys[1]).toBe(keys[0]);
    },
  );

  it.each([400, 403, 422, 499])(
    "discards the key after a definite HTTP %s rejection",
    async (status) => {
      const keys: Array<string | null> = [];
      const rejection = { status };
      const execute = vi.fn(async (init: RequestInit) => {
        keys.push(new Headers(init.headers).get("Idempotency-Key"));
        if (keys.length === 1) throw rejection;
        return { ok: true };
      });

      expect(await rejectionOf(runProtected(execute))).toBe(rejection);
      await expect(runProtected(execute)).resolves.toEqual({ ok: true });
      expect(keys).toHaveLength(2);
      expect(keys[1]).not.toBe(keys[0]);
    },
  );

  it("clears both realm and persisted retry state at a session boundary", async () => {
    const retryable = { status: 503 };
    const execute = vi.fn().mockRejectedValue(retryable);
    await rejectionOf(runProtected(execute));
    expect(storedRecords()).toHaveLength(1);

    clearPersistedIdempotencyRequestState();

    expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
    execute.mockResolvedValueOnce({ ok: true });
    await runProtected(execute);
    const firstKey = new Headers(execute.mock.calls[0][0].headers).get(
      "Idempotency-Key",
    );
    const retryKey = new Headers(execute.mock.calls.at(-1)?.[0].headers).get(
      "Idempotency-Key",
    );
    expect(retryKey).not.toBe(firstKey);
  });
});
