/**
 * Regression tests for the race-condition fixes in the central fetch wrapper.
 *
 * Two distinct hazards are pinned here:
 *   1. Unbounded mutations. Requests carried no signal and no timer, so a
 *      wedged proxy left them pending forever — which any "block while
 *      pending" dialog guard then turned into a genuinely unclosable modal.
 *   2. Transport failures escaping the session-epoch asserts. Those asserts sit
 *      after each await, so a rejection skipped them entirely and a superseded
 *      caller could not tell "my request failed" from "a newer session replaced
 *      mine" — and AuthProvider tears the session down on the former.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAccessToken, setCurrentFarmId } from "./api-client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("request timeout (CC-1)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("token-1", 1);
    setCurrentFarmId("1");
  });

  afterEach(() => {
    setAccessToken(null);
    setCurrentFarmId(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("attaches an abort signal to a request that supplies none", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1");

    const signal = fetchMock.mock.calls[0][1]?.signal as AbortSignal | undefined;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal!.aborted).toBe(false);
  });

  it("never overrides a caller's own signal", async () => {
    // The idempotency registry uses the caller signal as cancellation-ownership
    // identity, and TanStack Query aborts its queries through it. Replacing it
    // would break both.
    const controller = new AbortController();
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1", { signal: controller.signal });

    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it("bounds an unsignalled request with a finite timeout", async () => {
    // The timeout is a real Node timer that fake timers do not intercept, and
    // waiting it out would take a minute — so pin the contract at its source.
    // Without a bound the request stays pending forever behind a wedged proxy,
    // and every dialog that disables its own dismissal while a write is in
    // flight stays shut with only a reload to escape.
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1");

    expect(timeoutSpy).toHaveBeenCalledTimes(1);
    const [ms] = timeoutSpy.mock.calls[0];
    expect(Number.isFinite(ms)).toBe(true);
    expect(ms).toBeGreaterThan(0);
    // Generous on purpose: a herd-wide health event or dispense legitimately
    // takes seconds, and aborting a write that may already have committed is
    // worse than waiting.
    expect(ms).toBeGreaterThanOrEqual(30_000);
  });

  it("does not arm a timeout when the caller owns the signal", async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    const controller = new AbortController();
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await apiFetch("/api/animals/1", { signal: controller.signal });

    expect(timeoutSpy).not.toHaveBeenCalled();
  });

  it("propagates an abort as a rejection rather than hanging", async () => {
    const aborted = new Error("The operation was aborted.");
    aborted.name = "AbortError";
    fetchMock.mockRejectedValue(aborted);

    // idempotent-request classifies AbortError as non-retryable while RETAINING
    // the logical key, so an explicit retry replays the same Idempotency-Key
    // instead of committing a second time.
    await expect(apiFetch("/api/animals/1")).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});

describe("transport failures and the session epoch (F5)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("token-1", 1);
    setCurrentFarmId("1");
  });

  afterEach(() => {
    setAccessToken(null);
    setCurrentFarmId(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports a transport failure as AuthSessionChangedError once a newer session took over", async () => {
    // The whole point: AuthProvider.establishSession only spares a superseded
    // session when it sees this error name. Before the fix a dropped
    // connection surfaced as a bare TypeError, and the losing bootstrap call
    // ran clearSession() — destroying the session the user had just signed
    // into.
    fetchMock.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          // A newer sign-in installs its token while this request is on the
          // wire, then the connection drops.
          setTimeout(() => {
            setAccessToken("token-2", 2);
            reject(new TypeError("Failed to fetch"));
          }, 0);
        }),
    );

    await expect(apiFetch("/api/auth/farms")).rejects.toMatchObject({
      name: "AuthSessionChangedError",
    });
  });

  it("still surfaces the real transport error when the session did not change", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(apiFetch("/api/auth/farms")).rejects.toThrow("Failed to fetch");
  });

  it("reports a broken response body as AuthSessionChangedError once a newer session took over", async () => {
    // Bodies are streams: the actor can change after headers arrive but before
    // parsing finishes, and a stream failure skips the post-await assert too.
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      headers: new Headers(),
      json: () =>
        new Promise((_resolve, reject) => {
          setTimeout(() => {
            setAccessToken("token-2", 2);
            reject(new TypeError("network error while reading body"));
          }, 0);
        }),
    } as unknown as Response);

    await expect(apiFetch("/api/auth/farms")).rejects.toMatchObject({
      name: "AuthSessionChangedError",
    });
  });
});
