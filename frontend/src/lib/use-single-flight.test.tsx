import { act, renderHook } from "@testing-library/react";
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
});
