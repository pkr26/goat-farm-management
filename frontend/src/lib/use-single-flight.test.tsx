import { act, renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useSingleFlight } from "./use-single-flight";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("useSingleFlight", () => {
  it("starts idle", () => {
    const { result } = renderHook(() => useSingleFlight());

    expect(result.current.pending).toBe(false);
  });

  it("runs one action, exposes pending, and preserves its result", async () => {
    const gate = deferred<string>();
    const action = vi.fn(() => gate.promise);
    const { result } = renderHook(() => useSingleFlight());

    let first!: Promise<string | undefined>;
    act(() => {
      first = result.current.run(action);
    });

    expect(result.current.pending).toBe(true);
    expect(action).toHaveBeenCalledTimes(1);

    await act(async () => gate.resolve("saved"));

    await expect(first).resolves.toBe("saved");
    expect(result.current.pending).toBe(false);
  });

  it("closes the same-render gap and returns undefined to a duplicate caller", async () => {
    const gate = deferred<number>();
    const action = vi.fn(() => gate.promise);
    const { result } = renderHook(() => useSingleFlight());

    let first!: Promise<number | undefined>;
    let duplicate!: Promise<number | undefined>;
    act(() => {
      first = result.current.run(action);
      duplicate = result.current.run(action);
    });

    await expect(duplicate).resolves.toBeUndefined();
    expect(action).toHaveBeenCalledTimes(1);

    await act(async () => gate.resolve(7));
    await expect(first).resolves.toBe(7);
  });

  it("releases the flight after rejection so a later action can run", async () => {
    const error = new Error("save failed");
    const { result } = renderHook(() => useSingleFlight());
    let rejected!: Promise<unknown>;

    act(() => {
      rejected = result.current.run(() => Promise.reject(error));
    });
    await act(async () => {
      await expect(rejected).rejects.toBe(error);
    });

    expect(result.current.pending).toBe(false);
    await act(async () => {
      await expect(result.current.run(async () => "retried")).resolves.toBe("retried");
    });
    expect(result.current.pending).toBe(false);
  });

  it("settles safely after unmount without trying to publish pending state", async () => {
    const gate = deferred<string>();
    const { result, unmount } = renderHook(() => useSingleFlight());
    let request!: Promise<string | undefined>;

    act(() => {
      request = result.current.run(() => gate.promise);
    });
    expect(result.current.pending).toBe(true);
    unmount();

    gate.resolve("saved");
    await expect(request).resolves.toBe("saved");
  });

  it("ignores a stale run callback invoked after its component unmounted", async () => {
    const action = vi.fn(async () => "must-not-run");
    const { result, unmount } = renderHook(() => useSingleFlight());
    const staleRun = result.current.run;

    unmount();

    await expect(staleRun(action)).resolves.toBeUndefined();
    expect(action).not.toHaveBeenCalled();
  });

  it("remains mounted after Strict Mode's effect cleanup rehearsal", async () => {
    const { result } = renderHook(() => useSingleFlight(), {
      wrapper: StrictMode,
    });

    await act(async () => {
      await expect(result.current.run(async () => "saved")).resolves.toBe("saved");
    });
    expect(result.current.pending).toBe(false);
  });
});
