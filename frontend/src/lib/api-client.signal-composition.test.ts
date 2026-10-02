/**
 * Request-signal composition in rawFetch (2026-10-01 audit, 07-M3).
 *
 * AbortSignal.any / AbortSignal.timeout used to be hard dependencies —
 * unlike the Web Locks path, which degrades for pre-Web-Lock browsers — so
 * a pre-17.4 Safari threw "AbortSignal.any is not a function" synchronously
 * inside rawFetch for EVERY apiFetch. Worse, that TypeError is exactly what
 * the offline queue classifies as queueable, so each worker completion
 * showed "Saved — will send when online" for a request that never left the
 * device. These tests pin the native composition and the manual fallback
 * for browsers without the statics (the same fail-soft shape the Web-Locks
 * fallback uses).
 *
 * Global fetch is stubbed directly, same as the other api-client files; the
 * parked implementation rejects with the request signal's abort reason, so
 * the timeout's TimeoutError shape is observable end-to-end.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isOfflineQueueableFailure } from "@/lib/offline-queue";
import { apiFetch, setAccessToken, setCurrentFarmId, setOnAuthFailure } from "./api-client";

/** Parks the request until its signal aborts, then rejects with the signal's
 * reason — the observable behaviour fetch has for an aborted request. An
 * already-aborted signal rejects immediately. */
function parkUntilAbort(mock: ReturnType<typeof vi.fn>): void {
  mock.mockImplementation(
    (_input: unknown, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal as AbortSignal | undefined;
        const abortNow = () =>
          reject(
            signal?.reason ??
              new DOMException("The operation was aborted.", "AbortError"),
          );
        if (signal?.aborted) {
          abortNow();
          return;
        }
        signal?.addEventListener("abort", abortNow);
      }),
  );
}

/** Removes one AbortSignal static (the pre-17.4-Safari world) and returns a
 * restore function. */
function hideAbortSignalStatic(name: "any" | "timeout"): () => void {
  const descriptor = Object.getOwnPropertyDescriptor(AbortSignal, name);
  Object.defineProperty(AbortSignal, name, { configurable: true, value: undefined });
  return () => {
    if (descriptor) Object.defineProperty(AbortSignal, name, descriptor);
  };
}

/** Captures a request's eventual failure. Attached IMMEDIATELY after the
 * apiFetch call, because the rejection can fire inside vi's timer-advancing
 * macrotasks — a promise left unhandled across that boundary surfaces as an
 * unhandled rejection even though a later `.rejects` would consume it. */
function captureFailure(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => new Error("expected the request to fail"),
    (error: unknown) => error,
  );
}

/** Lets each microtask-scheduled refreshPromise reset (setTimeout 0) flush. */
function flushMacrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("rawFetch request-signal composition (2026-10-01 audit, 07-M3)", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    setAccessToken(null);
    setCurrentFarmId(null);
    setOnAuthFailure(null);
    fetchMock.mockReset();
    await flushMacrotasks();
  });

  it("composes the caller's cancellation with the bounded lifetime natively when available", async () => {
    parkUntilAbort(fetchMock);
    vi.useFakeTimers();
    try {
      const caller = new AbortController();
      const pending = apiFetch("/api/animals", { signal: caller.signal });
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      // Composed, never passed through: the timeout must not be skippable.
      expect(requestSignal).not.toBe(caller.signal);
      expect(requestSignal.aborted).toBe(false);

      caller.abort();
      expect(requestSignal.aborted).toBe(true);
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("degrades to manual composition when AbortSignal.any is missing instead of throwing", async () => {
    const restore = hideAbortSignalStatic("any");
    parkUntilAbort(fetchMock);
    vi.useFakeTimers();
    try {
      // A caller signal forces the composition path: without one the native
      // AbortSignal.timeout alone suffices (and native timers are not under
      // vi's control).
      const caller = new AbortController();
      const pending = apiFetch("/api/animals", { signal: caller.signal });
      const failure = captureFailure(pending);
      // Before the fix, AbortSignal.any(...) threw synchronously inside
      // rawFetch — this line never even ran.
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      expect(requestSignal).toBeInstanceOf(AbortSignal);
      expect(requestSignal.aborted).toBe(false);

      await vi.advanceTimersByTimeAsync(60_000);
      expect(requestSignal.aborted).toBe(true);
      const caught = await failure;
      // The fallback reproduces AbortSignal.timeout's TimeoutError shape…
      expect(caught).toMatchObject({ name: "TimeoutError" });
      // …so the offline queue's classifier answers the same way it does on
      // modern browsers: a timeout is a field-connectivity failure.
      expect(isOfflineQueueableFailure(caught)).toBe(true);
    } finally {
      vi.useRealTimers();
      restore();
    }
  });

  it("still forwards the caller's cancellation through the fallback composition", async () => {
    const restore = hideAbortSignalStatic("any");
    parkUntilAbort(fetchMock);
    vi.useFakeTimers();
    try {
      const caller = new AbortController();
      const pending = apiFetch("/api/animals", { signal: caller.signal });
      await vi.advanceTimersByTimeAsync(0);
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      expect(requestSignal.aborted).toBe(false);

      caller.abort();
      expect(requestSignal.aborted).toBe(true);
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    } finally {
      vi.useRealTimers();
      restore();
    }
  });

  it("forwards an already-aborted caller signal through the fallback", async () => {
    const restoreAny = hideAbortSignalStatic("any");
    const restoreTimeout = hideAbortSignalStatic("timeout");
    parkUntilAbort(fetchMock);
    vi.useFakeTimers();
    try {
      const caller = new AbortController();
      caller.abort();
      const pending = apiFetch("/api/animals", { signal: caller.signal });
      const failure = captureFailure(pending);
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      expect(requestSignal.aborted).toBe(true);
      expect(await failure).toMatchObject({ name: "AbortError" });
    } finally {
      vi.useRealTimers();
      restoreTimeout();
      restoreAny();
    }
  });

  it("bounds the request lifetime manually when both statics are missing", async () => {
    const restoreAny = hideAbortSignalStatic("any");
    const restoreTimeout = hideAbortSignalStatic("timeout");
    parkUntilAbort(fetchMock);
    vi.useFakeTimers();
    try {
      const pending = apiFetch("/api/animals");
      const failure = captureFailure(pending);
      await vi.advanceTimersByTimeAsync(0);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const requestSignal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;

      await vi.advanceTimersByTimeAsync(60_000);
      expect(requestSignal.aborted).toBe(true);
      expect(await failure).toMatchObject({ name: "TimeoutError" });
    } finally {
      vi.useRealTimers();
      restoreTimeout();
      restoreAny();
    }
  });
});
