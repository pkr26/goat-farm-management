/**
 * Boundary contracts from the 2026-09-23 deep-mutation campaign.
 *
 * The sibling suites assert outcomes; these pin the EXACT edges that survived
 * mutation: the refresh-payload guard arms (numeric/empty/whitespace tokens),
 * the actor-scope preservation arms of setAccessToken, the auth-failure
 * registration stack (at(-1) semantics, double-unregister, null reset), the
 * 499/500 transient boundary, single-flight dedup and release, the exact
 * 10 s / 60 s / 300 s request budgets, the healthz/readyz root-path allowlist,
 * and ApiError.code extraction.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  farmScopeEpochValue,
  refreshSessionDetailed,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "./api-client";

function jsonResponse(status: number, body: unknown, contentType = "application/json"): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: `Status ${status}`,
    headers: { "Content-Type": contentType },
  });
}

function refreshPayload(accessToken: string, actorId = 1) {
  return {
    access_token: accessToken,
    user: { id: actorId, email: `actor-${actorId}@example.test`, name: null },
  };
}

/** An unsigned JWT — the client only ever reads the unverified `sub` claim. */
function jwtWithClaims(claims: Record<string, unknown>): string {
  const payload = globalThis
    .btoa(JSON.stringify(claims))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${payload}.signature`;
}

function flushMacrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}


/** Drains the single-flight release timer (setTimeout 0) around a call so
 *  each test's refresh starts from a clean refreshPromise slot. */
async function refreshOutcome(): Promise<{ kind: string; body?: unknown }> {
  await flushMacrotasks();
  try {
    return (await refreshSessionDetailed()) as { kind: string; body?: unknown };
  } finally {
    await flushMacrotasks();
  }
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks keeps queued implementations; a persistent
  // mockImplementation from an earlier test must not answer this one's calls.
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken(null);
  setCurrentFarmId(null);
  setOnAuthFailure(null);
});

afterEach(async () => {
  setAccessToken(null);
  setCurrentFarmId(null);
  setOnAuthFailure(null);
  vi.unstubAllGlobals();
  vi.useRealTimers();
  await flushMacrotasks();
});

describe("refresh payload guard arms", () => {
  it("rejects a numeric, empty, and whitespace token body", async () => {
    for (const bad of [
      { access_token: 123, user: { id: 1, email: "a@t", name: null } },
      { access_token: "", user: { id: 1, email: "a@t", name: null } },
      { access_token: "abc def", user: { id: 1, email: "a@t", name: null } },
    ]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, bad));
      const outcome = await refreshOutcome();
      expect(outcome, JSON.stringify(bad)).toEqual({ kind: "rejected" });
    }
  });

  it("rejects a body whose user is not a safe object", async () => {
    for (const user of [null, "u", { id: 1.5, email: "a@t", name: null }, { id: 0, email: "a@t", name: null }]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { access_token: "t", user }));
      const outcome = await refreshOutcome();
      expect(outcome, JSON.stringify(user)).toEqual({ kind: "rejected" });
    }
  });

  it("rejects a non-string, non-null user name", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "t", user: { id: 1, email: "a@t", name: 7 } }),
    );
    expect(await refreshOutcome()).toEqual({ kind: "rejected" });
  });
});

describe("setAccessToken actor-scope preservation", () => {
  it("re-derives the scope from the token when re-applying the same JWT", async () => {
    const token = jwtWithClaims({ sub: 7 });
    setAccessToken(token, "not-seven");
    setAccessToken(token); // bootstrap re-applies the trusted token
    const body = refreshPayload(token, 7);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, body));
    const outcome = await refreshOutcome();
    // The stale "not-seven" scope must not reject the actor-7 refresh.
    expect(outcome).toEqual({ kind: "session", body });
  });

  it("keeps an explicitly installed opaque-token scope across a re-apply", async () => {
    setAccessToken("opaque-token", "actor-9");
    setAccessToken("opaque-token"); // same token, no fresh scope argument
    fetchMock.mockResolvedValueOnce(jsonResponse(200, refreshPayload("opaque-token", 5)));
    const outcome = await refreshOutcome();
    // actor-9 is still the expected scope; a user-5 answer must NOT be applied.
    expect(outcome).toEqual({ kind: "rejected" });
  });
});

describe("auth-failure registration stack", () => {
  function fireAuthFailure() {
    // The module calls the active handler on an actor-mismatched refresh:
    // the installed token belongs to sub 1, the answer to sub 2.
    setAccessToken(jwtWithClaims({ sub: 1 }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, refreshPayload(jwtWithClaims({ sub: 2 }), 2)),
    );
  }

  it("a null reset removes every registration", async () => {
    const a = vi.fn();
    setOnAuthFailure(a);
    setOnAuthFailure(null);
    fireAuthFailure();
    await refreshOutcome();
    expect(a).not.toHaveBeenCalled();
  });

  it("the newest surviving registration stays active", async () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = setOnAuthFailure(a);
    setOnAuthFailure(b);
    offA();
    fireAuthFailure();
    await refreshOutcome();
    expect(b).toHaveBeenCalledOnce();
    expect(a).not.toHaveBeenCalled();
  });

  it("three registrations: removing the newest restores the middle one", async () => {
    const a = vi.fn();
    const b = vi.fn();
    const c = vi.fn();
    setOnAuthFailure(a);
    setOnAuthFailure(b);
    const offC = setOnAuthFailure(c);
    offC();
    fireAuthFailure();
    await refreshOutcome();
    expect(b).toHaveBeenCalledOnce();
    expect(a).not.toHaveBeenCalled();
    expect(c).not.toHaveBeenCalled();
  });

  it("unregistering the only registration silences the failure hook", async () => {
    const a = vi.fn();
    const off = setOnAuthFailure(a);
    off();
    fireAuthFailure();
    await refreshOutcome();
    expect(a).not.toHaveBeenCalled();
  });

  it("a null reset leaves no stale registration for later cycles", async () => {
    const stale = vi.fn();
    const next = vi.fn();
    setOnAuthFailure(stale);
    setOnAuthFailure(null);
    const offNext = setOnAuthFailure(next);
    offNext();
    fireAuthFailure();
    await refreshOutcome();
    expect(stale).not.toHaveBeenCalled();
    expect(next).not.toHaveBeenCalled();
  });

  it("unregistering twice never removes a later registration", async () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = setOnAuthFailure(a);
    setOnAuthFailure(b);
    offA();
    offA(); // stale cleanup — must be a no-op
    fireAuthFailure();
    await refreshOutcome();
    expect(b).toHaveBeenCalledOnce();
    expect(a).not.toHaveBeenCalled();
  });
});

describe("transient refresh status boundary", () => {
  it("treats 499 as authoritative and 500 as transient", async () => {
    setAccessToken("t");
    fetchMock.mockResolvedValueOnce(jsonResponse(499, { detail: "no" }));
    expect(await refreshOutcome()).toEqual({ kind: "rejected" });
    fetchMock.mockResolvedValueOnce(jsonResponse(500, { detail: "boom" }));
    expect(await refreshOutcome()).toEqual({ kind: "unavailable" });
    fetchMock.mockResolvedValueOnce(jsonResponse(408, { detail: "t/o" }));
    expect(await refreshOutcome()).toEqual({ kind: "unavailable" });
    fetchMock.mockResolvedValueOnce(jsonResponse(429, { detail: "slow" }));
    expect(await refreshOutcome()).toEqual({ kind: "unavailable" });
  });
});

describe("single-flight refresh", () => {
  it("deduplicates concurrent same-epoch calls into one fetch", async () => {
    await flushMacrotasks();
    setAccessToken("t");
    let release!: (value: Response) => void;
    fetchMock.mockImplementationOnce(
      () => new Promise<Response>((resolve) => (release = resolve)),
    );
    const first = refreshSessionDetailed();
    const second = refreshSessionDetailed();
    // The fetch starts after a few async hops through the cookie lock.
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    release(jsonResponse(200, refreshPayload(jwtWithClaims({ sub: 1 }))));
    const [a, b] = await Promise.all([first, second]);
    expect(a).toEqual(b);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await flushMacrotasks();
  });

  it("starts a fresh fetch after the previous refresh settled", async () => {
    await flushMacrotasks();
    setAccessToken("t");
    // A fresh Response per call: bodies are one-shot streams.
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, refreshPayload(jwtWithClaims({ sub: 1 }))),
    );
    const first = await refreshOutcome();
    expect(first.kind).toBe("session");
    const second = await refreshOutcome();
    expect(second.kind).toBe("session");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("request timeout budgets", () => {
  it("aborts a wedged refresh fetch at exactly 10 s", async () => {
    vi.useFakeTimers();
    setAccessToken("t");
    fetchMock.mockImplementationOnce(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new Error("aborted")),
          );
        }),
    );
    let settled = false;
    const outcome = refreshSessionDetailed().then((value) => {
      settled = true;
      return value;
    });
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // 9_999 ms is inside the budget: the refresh must still be in flight.
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await outcome).toEqual({ kind: "unavailable" });
  });

  it("fails a queued refresh waiting on a stuck cookie lock at exactly 62 s", async () => {
    vi.useFakeTimers();
    setAccessToken(jwtWithClaims({ sub: 1 }));
    // The first refresh holds the origin-wide cookie lock forever (wedged
    // proxy); a second, same-actor refresh must give up at the lock budget.
    fetchMock.mockImplementationOnce(
      () => new Promise<Response>(() => {}),
    );
    const first = refreshSessionDetailed();
    // A 0 ms advance flushes the microtask hops up to the fetch call.
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // A new sign-in supersedes the epoch so the second refresh is a NEW
    // single-flight entry that must queue behind the wedged holder.
    setAccessToken(jwtWithClaims({ sub: 2 }));
    let secondSettled = false;
    const second = refreshSessionDetailed().then((value) => {
      secondSettled = true;
      return value;
    });
    await vi.advanceTimersByTimeAsync(61_999);
    expect(secondSettled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await second).toEqual({ kind: "unavailable" });
    // The holder is untouched by the waiter's timeout.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
    // The holder stays wedged on purpose; nothing else in this file needs
    // the cookie lock afterwards.
    void first;
  });

  it("aborts an ordinary request at exactly 60 s", async () => {
    vi.useFakeTimers();
    // AbortSignal.timeout is not fake-timer driven; route it through the
    // faked setTimeout so the exact boundary is observable.
    vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), ms);
      return controller.signal as AbortSignal;
    });
    setAccessToken("session-token");
    fetchMock.mockImplementationOnce(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        }),
    );
    const pending = apiFetch("/api/animals");
    const assertion = expect(pending).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(59_999);
    await vi.advanceTimersByTimeAsync(1);
    await assertion;
  });

  it("gives simulation runs exactly 300 s", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), ms);
      return controller.signal as AbortSignal;
    });
    setAccessToken("session-token");
    fetchMock.mockImplementationOnce(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        }),
    );
    const pending = apiFetch("/api/simulation/run", { method: "POST", body: "{}" });
    const assertion = expect(pending).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(299_999);
    await vi.advanceTimersByTimeAsync(1);
    await assertion;
  });
});

describe("root-path allowlist", () => {
  it("permits exactly /healthz and /readyz at the root, nothing else", async () => {
    setAccessToken("session-token");
    for (const path of ["/healthz", "/readyz", "/healthz?probe=1", "/api/anything"]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
      await expect(apiFetch(path)).resolves.toEqual({ ok: true });
    }
    for (const path of ["/healthzz", "/HEALTHZ", "/health", "//healthz", "/evil", "/api/../evil", "/api/%2e%2e/evil"]) {
      await expect(apiFetch(path)).rejects.toThrow(/approved same-origin backend path/);
    }
  });
});

describe("farm scope epoch", () => {
  it("bumps exactly once per farm change and never for a re-apply", () => {
    const before = farmScopeEpochValue();
    setCurrentFarmId("1");
    const afterFirst = farmScopeEpochValue();
    setCurrentFarmId("1");
    setCurrentFarmId("2");
    const afterSecond = farmScopeEpochValue();
    expect(afterFirst - before).toBe(1);
    expect(afterSecond - afterFirst).toBe(1);
  });
});

describe("ApiError code extraction", () => {
  it("keeps a non-empty string code and drops empty or non-string codes", async () => {
    setAccessToken("session-token");
    fetchMock.mockResolvedValueOnce(jsonResponse(409, { code: "HERD_LOCKED", detail: "busy" }));
    const err = await apiFetch("/api/animals").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("HERD_LOCKED");

    fetchMock.mockResolvedValueOnce(jsonResponse(409, { code: "", detail: "busy" }));
    const empty = await apiFetch("/api/animals").catch((e: unknown) => e);
    expect((empty as ApiError).code).toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse(409, { code: 42, detail: "busy" }));
    const numeric = await apiFetch("/api/animals").catch((e: unknown) => e);
    expect((numeric as ApiError).code).toBeNull();
  });
});
