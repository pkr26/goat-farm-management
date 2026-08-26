import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearIdempotencyRequestState,
  IDEMPOTENCY_SESSION_STORAGE_KEY,
  isIdempotencyProtectedMutation,
  runIdempotencyProtectedRequest,
} from "./idempotent-request";

const NOW = 1_800_000_000_000;
const TTL_MS = 2 * 60 * 1000;
const MAX_STORAGE_BYTES = 64 * 1024;
const MAX_RECORDS = 128;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
// A well-formed record for some other logical request, used to prove that
// pruning/removal only ever touches the record it is aimed at.
const OTHER_DIGEST = "b".repeat(64);
const OTHER_KEY = "12345678-1234-4123-8123-123456789abc";

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

function sentKey(init: RequestInit): string | null {
  return new Headers(init.headers).get("Idempotency-Key");
}

/** Every container this module writes must be a list of canonical records. */
function expectRecordContainer(value: unknown): void {
  expect(Array.isArray(value)).toBe(true);
  for (const entry of value as unknown[]) {
    expect(Object.keys(entry as object).sort()).toEqual([
      "digest",
      "expiresAt",
      "key",
      "version",
    ]);
    const record = entry as StoredRecord;
    expect(record.version).toBe(1);
    expect(record.digest).toMatch(SHA256_PATTERN);
    expect(record.key).toMatch(UUID_PATTERN);
  }
}

function containerWrites(calls: [string, string][]): unknown[] {
  return calls
    .filter(([key]) => key === IDEMPOTENCY_SESSION_STORAGE_KEY)
    .map(([, value]) => JSON.parse(value) as unknown);
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
    farmScope: overrides.farmScope === undefined ? "farm-17" : overrides.farmScope,
    sessionScope: overrides.sessionScope ?? 4,
    actorScope: overrides.actorScope === undefined ? "actor-101" : overrides.actorScope,
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

describe("idempotency persistence and signature branches", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearIdempotencyRequestState();
    vi.spyOn(Date, "now").mockReturnValue(NOW);
  });

  afterEach(() => {
    // Globals first: a realm test can leave `window` itself stubbed away.
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    clearIdempotencyRequestState();
    window.sessionStorage.clear();
  });

  it("stores recovery keys under one stable namespaced session key", async () => {
    expect(IDEMPOTENCY_SESSION_STORAGE_KEY).toBe("goatfarm:idempotency:v1");

    await rejectionOf(runProtected(vi.fn().mockRejectedValue({ status: 503 })));
    await Promise.resolve();

    expect(Object.keys(window.sessionStorage)).toEqual([
      "goatfarm:idempotency:v1",
    ]);
  });

  it("writes exactly this request's record before the send starts", async () => {
    const containersDuringSend: string[] = [];
    const execute = vi.fn(async () => {
      containersDuringSend.push(
        window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY) ?? "",
      );
      throw { status: 503 };
    });

    await rejectionOf(runProtected(execute));

    const container = JSON.parse(containersDuringSend[0]) as unknown[];
    expectRecordContainer(container);
    expect(container).toHaveLength(1);
    expect((container[0] as StoredRecord).expiresAt).toBe(NOW + TTL_MS);
  });

  it("keeps the container it writes before send within the retained-key bound", async () => {
    const full = Array.from({ length: MAX_RECORDS }, (_, index) => ({
      version: 1,
      digest: index.toString(16).padStart(64, "0"),
      key: `00000000-0000-4000-8000-${index.toString(16).padStart(12, "0")}`,
      expiresAt: NOW + index + 1,
    }));
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify(full),
    );
    const containersDuringSend: string[] = [];
    const execute = vi.fn(async () => {
      containersDuringSend.push(
        window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY) ?? "",
      );
      throw { status: 503 };
    });

    await rejectionOf(runProtected(execute));

    const container = JSON.parse(containersDuringSend[0]) as StoredRecord[];
    expect(container).toHaveLength(MAX_RECORDS);
    // The new record is newest, so the oldest stored one is evicted for it.
    expect(container[0].expiresAt).toBe(NOW + TTL_MS);
    expect(container.some((record) => record.digest === "0".repeat(64))).toBe(false);
  });

  it("writes a clean record even when reading the container is blocked", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    await rejectionOf(runProtected(vi.fn().mockRejectedValue({ status: 503 })));
    await Promise.resolve();

    const writes = containerWrites(setItem.mock.calls);
    expect(writes.length).toBeGreaterThan(0);
    for (const container of writes) {
      expectRecordContainer(container);
      expect(container).toHaveLength(1);
    }
  });

  it.each([
    [
      "is larger than the storage budget",
      JSON.stringify([
        {
          version: 1,
          digest: OTHER_DIGEST,
          key: OTHER_KEY,
          expiresAt: NOW + 30_000,
          padding: "x".repeat(MAX_STORAGE_BYTES),
        },
      ]),
    ],
    ["is not JSON", "{not-json"],
    ["is not an array", JSON.stringify({ version: 1, digest: OTHER_DIGEST })],
  ])(
    "replaces a persisted container that %s without ever writing a malformed one",
    async (_label, poisoned) => {
      const keys: Array<string | null> = [];
      const execute = vi.fn(async (init: RequestInit) => {
        keys.push(sentKey(init));
        throw { status: 503 };
      });
      // The first attempt retains a logical entry, so the explicit retry below
      // reaches storage through persistKey rather than the recovery lookup.
      await rejectionOf(runProtected(execute));
      await Promise.resolve();
      window.sessionStorage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, poisoned);
      const setItem = vi.spyOn(Storage.prototype, "setItem");

      await rejectionOf(runProtected(execute));
      await Promise.resolve();

      for (const container of containerWrites(setItem.mock.calls)) {
        expectRecordContainer(container);
      }
      const records = storedRecords();
      expect(records).toHaveLength(1);
      expect(records[0].key).toBe(keys[0]);
    },
  );

  it("drops a persisted record whose digest is not a string", async () => {
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      // An array whose text form is a valid digest must not pass as one.
      JSON.stringify([
        {
          version: 1,
          digest: [OTHER_DIGEST],
          key: OTHER_KEY,
          expiresAt: NOW + 30_000,
        },
      ]),
    );

    await rejectionOf(runProtected(vi.fn().mockRejectedValue({ status: 503 })));
    await Promise.resolve();

    const records = storedRecords();
    expect(records).toHaveLength(1);
    expectRecordContainer(records);
    expect(records[0].key).not.toBe(OTHER_KEY);
  });

  it("does not recover a persisted key that is not a string", async () => {
    const keys: Array<string | null> = [];
    const execute = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      throw { status: 503 };
    });
    await rejectionOf(runProtected(execute));
    await Promise.resolve();
    const [retained] = storedRecords();

    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([{ ...retained, key: [retained.key] }]),
    );
    clearIdempotencyRequestState();
    const succeed = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      return { ok: true };
    });

    await expect(runProtected(succeed)).resolves.toEqual({ ok: true });

    expect(keys[1]).toMatch(UUID_PATTERN);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("does not rewrite a persisted container that is already canonical", async () => {
    const canonical = JSON.stringify([
      { version: 1, digest: OTHER_DIGEST, key: OTHER_KEY, expiresAt: NOW + 30_000 },
    ]);
    window.sessionStorage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, canonical);
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    await rejectionOf(runProtected(vi.fn().mockRejectedValue({ status: 503 })));
    await Promise.resolve();

    // Only the two durable writes this request owes: one before the send and
    // one when the ambiguous failure extends the retained key.
    expect(containerWrites(setItem.mock.calls)).toHaveLength(2);
    const records = storedRecords();
    expect(records).toHaveLength(2);
    expect(records[1]).toEqual({
      version: 1,
      digest: OTHER_DIGEST,
      key: OTHER_KEY,
      expiresAt: NOW + 30_000,
    });
  });

  it("prunes an expired record as soon as the container is read", async () => {
    const live = {
      version: 1,
      digest: OTHER_DIGEST,
      key: OTHER_KEY,
      expiresAt: NOW + 30_000,
    };
    const expired = {
      version: 1,
      digest: "c".repeat(64),
      key: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      expiresAt: NOW - 1,
    };
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([expired, live]),
    );
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    await rejectionOf(runProtected(vi.fn().mockRejectedValue({ status: 503 })));
    await Promise.resolve();

    const writes = containerWrites(setItem.mock.calls);
    expect(writes[0]).toEqual([live]);
    expect(storedRecords().map((record) => record.digest)).not.toContain(
      expired.digest,
    );
  });

  it.each([
    ["an actor without a stable identity", { actorScope: null }],
    ["the worker-create route", { url: "/api/team/workers" }],
  ])(
    "never rewrites the persisted container for a memory-only mutation: %s",
    async (_label, overrides) => {
      const raw = JSON.stringify([
        {
          version: 1,
          digest: OTHER_DIGEST,
          key: OTHER_KEY,
          expiresAt: NOW + 30_000,
          unknown_field: "left exactly as found",
        },
      ]);
      window.sessionStorage.setItem(IDEMPOTENCY_SESSION_STORAGE_KEY, raw);
      const execute = vi.fn().mockResolvedValue({ ok: true });

      await expect(runProtected(execute, overrides)).resolves.toEqual({ ok: true });
      await Promise.resolve();

      expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBe(raw);
    },
  );

  it("removes only its own record when the mutation finally succeeds", async () => {
    const other = {
      version: 1,
      digest: OTHER_DIGEST,
      key: OTHER_KEY,
      expiresAt: NOW + 30_000,
    };
    window.sessionStorage.setItem(
      IDEMPOTENCY_SESSION_STORAGE_KEY,
      JSON.stringify([other]),
    );
    const execute = vi
      .fn()
      .mockRejectedValueOnce({ status: 503 })
      .mockResolvedValueOnce({ ok: true });

    await rejectionOf(runProtected(execute));
    await Promise.resolve();
    expect(storedRecords()).toHaveLength(2);

    await expect(runProtected(execute)).resolves.toEqual({ ok: true });
    await Promise.resolve();

    expect(storedRecords()).toEqual([other]);
  });

  it("classifies a protected route by its resolved path, not its URL form", () => {
    expect(
      isIdempotencyProtectedMutation("https://goatfarm.example/api/finance/new", "POST"),
    ).toBe(true);
    expect(
      isIdempotencyProtectedMutation(
        "https://goatfarm.example/api/animals/42/weight?trace=1",
        "POST",
      ),
    ).toBe(true);
    expect(
      isIdempotencyProtectedMutation("https://goatfarm.example/api/finance", "POST"),
    ).toBe(false);
  });

  it.each(["http://", "//", "http://["])(
    "does not treat unparseable URL %s as a protected mutation",
    (url) => {
      expect(isIdempotencyProtectedMutation(url, "POST")).toBe(false);
    },
  );

  it("treats an absent body and an empty body as one logical submission", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn(
      () =>
        new Promise<{ ok: true }>((resolve) => {
          releases.push(() => resolve({ ok: true }));
        }),
    );

    const first = runProtected(execute, {
      actorScope: null,
      init: { method: "POST" },
    });
    const second = runProtected(execute, {
      actorScope: null,
      init: { method: "POST", body: "" },
    });

    expect(execute).toHaveBeenCalledTimes(1);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it("refuses an unreplayable body with the documented error in a realm without URLSearchParams", async () => {
    const body = new URLSearchParams({ amount: "10" });
    vi.stubGlobal("URLSearchParams", undefined);
    const execute = vi.fn();

    expect(
      await rejectionOf(
        runProtected(execute, {
          actorScope: null,
          init: { method: "POST", body },
        }),
      ),
    ).toMatchObject({
      message: "Protected mutations require a replayable string request body.",
    });
    expect(execute).not.toHaveBeenCalled();
  });

  it("keeps adjacent headers separated against concatenation collisions", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn(
      () =>
        new Promise<{ ok: true }>((resolve) => {
          releases.push(() => resolve({ ok: true }));
        }),
    );

    const first = runProtected(execute, {
      actorScope: null,
      init: { method: "POST", body: "{}", headers: { "x-a": "1", "x-b": "2" } },
    });
    const second = runProtected(execute, {
      actorScope: null,
      init: { method: "POST", body: "{}", headers: { "x-a": "1x-b:2" } },
    });

    expect(execute).toHaveBeenCalledTimes(2);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it("treats an absent farm scope and an empty farm scope as one submission", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn(
      () =>
        new Promise<{ ok: true }>((resolve) => {
          releases.push(() => resolve({ ok: true }));
        }),
    );

    const first = runProtected(execute, { actorScope: null, farmScope: null });
    const second = runProtected(execute, { actorScope: null, farmScope: "" });

    expect(execute).toHaveBeenCalledTimes(1);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it("treats an empty caller-provided key as no caller key at all", async () => {
    const releases: Array<() => void> = [];
    const execute = vi.fn(
      () =>
        new Promise<{ ok: true }>((resolve) => {
          releases.push(() => resolve({ ok: true }));
        }),
    );

    const first = runProtected(execute, {
      actorScope: null,
      init: { method: "POST", body: "{}" },
    });
    const second = runProtected(execute, {
      actorScope: null,
      init: {
        method: "POST",
        body: "{}",
        headers: { "Idempotency-Key": "" },
      },
    });

    expect(execute).toHaveBeenCalledTimes(1);
    releases.forEach((release) => release());
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true },
      { ok: true },
    ]);
  });

  it("recovers a persisted key when the farm scope is empty rather than absent", async () => {
    const keys: Array<string | null> = [];
    const execute = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      throw { status: 503 };
    });
    await rejectionOf(runProtected(execute, { farmScope: null }));
    await Promise.resolve();
    clearIdempotencyRequestState();
    const succeed = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      return { ok: true };
    });

    await expect(runProtected(succeed, { farmScope: "" })).resolves.toEqual({
      ok: true,
    });

    expect(keys[1]).toBe(keys[0]);
  });

  it("keeps actor and farm scope separated in the persisted digest", async () => {
    const keys: Array<string | null> = [];
    const execute = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      throw { status: 503 };
    });
    await rejectionOf(
      runProtected(execute, { actorScope: "actor-1", farmScope: "7" }),
    );
    await Promise.resolve();
    expect(storedRecords()).toHaveLength(1);
    clearIdempotencyRequestState();
    const succeed = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      return { ok: true };
    });

    await expect(
      runProtected(succeed, { actorScope: "actor-17", farmScope: "" }),
    ).resolves.toEqual({ ok: true });

    expect(keys[1]).not.toBe(keys[0]);
  });

  it("stays memory-only when reaching sessionStorage itself is blocked", async () => {
    // Browsers that block storage in an embedded context throw on the
    // property access, not only on getItem/setItem.
    const descriptor = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get() {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    const keys: Array<string | null> = [];
    const execute = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      if (keys.length === 1) throw { status: 503 };
      return { ok: true };
    });

    try {
      await rejectionOf(runProtected(execute));
      await Promise.resolve();
      await expect(runProtected(execute)).resolves.toEqual({ ok: true });
    } finally {
      if (descriptor) Object.defineProperty(window, "sessionStorage", descriptor);
      else Reflect.deleteProperty(window, "sessionStorage");
    }

    expect(keys[0]).toMatch(UUID_PATTERN);
    // Realm memory still coalesces the explicit retry onto the same key.
    expect(keys[1]).toBe(keys[0]);
    expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
  });

  it("stays memory-only in a realm that has no window", async () => {
    vi.stubGlobal("window", undefined);
    const keys: Array<string | null> = [];
    const execute = vi.fn(async (init: RequestInit) => {
      keys.push(sentKey(init));
      throw { status: 503 };
    });

    expect(await rejectionOf(runProtected(execute))).toEqual({ status: 503 });
    await Promise.resolve();
    vi.unstubAllGlobals();

    expect(keys[0]).toMatch(UUID_PATTERN);
    expect(window.sessionStorage.getItem(IDEMPOTENCY_SESSION_STORAGE_KEY)).toBeNull();
  });
});
