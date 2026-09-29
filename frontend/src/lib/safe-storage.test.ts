/**
 * safeStorage: the one guarded Web-Storage accessor (2026-09-28 audit dedup
 * — previously four-plus private copies of the same fail-closed guard).
 */

import { describe, expect, it, vi } from "vitest";

import { safeStorage } from "@/lib/safe-storage";

describe("safeStorage", () => {
  it("returns the requested realm's Storage", () => {
    expect(safeStorage("local")).toBe(window.localStorage);
    expect(safeStorage("session")).toBe(window.sessionStorage);
  });

  it("fails closed (null) when the realm is missing entirely", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "window");
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: undefined,
    });
    try {
      expect(safeStorage("local")).toBeNull();
      expect(safeStorage("session")).toBeNull();
    } finally {
      if (original) Object.defineProperty(globalThis, "window", original);
    }
  });

  it("fails closed (null) when site data is blocked for the origin", () => {
    // A per-origin deny makes the window.localStorage GETTER itself throw
    // SecurityError — the accessor must not let that escape.
    for (const kind of ["localStorage", "sessionStorage"] as const) {
      const getter = vi.spyOn(window, kind, "get").mockImplementation(() => {
        throw new DOMException("blocked", "SecurityError");
      });
      try {
        expect(safeStorage(kind === "localStorage" ? "local" : "session")).toBeNull();
      } finally {
        getter.mockRestore();
      }
    }
  });
});
