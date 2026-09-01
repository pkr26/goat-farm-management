/**
 * URL-backed state hook: numeric fallbacks, clamping, default stripping and
 * the pending-write composition that keeps two rapid updates from clobbering
 * each other while router.replace is still in flight.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useUrlState } from "./use-url-state";

const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  params: new URLSearchParams(""),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/health",
  useRouter: () => ({ replace: nav.replace }),
  useSearchParams: () => nav.params,
}));

function setParams(query: string) {
  nav.params = new URLSearchParams(query);
}

describe("useUrlState", () => {
  beforeEach(() => {
    nav.replace.mockClear();
    setParams("");
  });

  it("getNumber returns the fallback for a missing or empty param", () => {
    setParams("");
    const { result } = renderHook(() => useUrlState());
    expect(result.current.getNumber("offset", 7)).toBe(7);

    setParams("offset=");
    const { result: empty } = renderHook(() => useUrlState());
    expect(empty.current.getNumber("offset", 7)).toBe(7);
  });

  it("getNumber returns the fallback for a non-numeric param", () => {
    setParams("offset=abc");
    const { result } = renderHook(() => useUrlState());
    expect(result.current.getNumber("offset", 3)).toBe(3);
  });

  it("getNumber truncates and clamps into range", () => {
    setParams("offset=12.9");
    const { result } = renderHook(() => useUrlState());
    expect(result.current.getNumber("offset", 0, 0, 10)).toBe(10);
    expect(result.current.getNumber("offset", 0, 5)).toBe(12); // inside [5, ∞)
    setParams("offset=2");
    const { result: low } = renderHook(() => useUrlState());
    expect(low.current.getNumber("offset", 0, 5)).toBe(5);
  });

  it("set deletes on null and replaces without scroll", () => {
    const { result } = renderHook(() => useUrlState());
    act(() => result.current.set({ offset: 50 }));
    expect(nav.replace).toHaveBeenCalledWith("/health?offset=50", { scroll: false });

    act(() => result.current.set({ offset: null }));
    expect(nav.replace).toHaveBeenLastCalledWith("/health", { scroll: false });

    setParams("date_from=2026-01-01");
    const { result: withDate } = renderHook(() => useUrlState());
    act(() => withDate.current.set({ date_from: null }));
    expect(nav.replace).toHaveBeenLastCalledWith("/health", { scroll: false });
  });

  it("composes two writes fired before the navigation commits", () => {
    setParams("");
    const { result } = renderHook(() => useUrlState());
    // First write: a date filter plus its offset reset. The mocked router
    // never commits (searchParams stays empty), exactly like the real
    // router's async window — the second write must not drop the date.
    act(() => result.current.set({ date_from: "2026-01-01", offset: null }));
    act(() => result.current.set({ offset: 50 }));
    expect(nav.replace).toHaveBeenLastCalledWith(
      "/health?date_from=2026-01-01&offset=50",
      { scroll: false },
    );
  });

  it("returns null and does not navigate when nothing changes", () => {
    setParams("");
    const { result } = renderHook(() => useUrlState());
    let returned: string | null = "sentinel";
    act(() => {
      returned = result.current.set({ offset: null });
    });
    expect(returned).toBeNull();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("adopts externally changed params as the new composition base", () => {
    setParams("");
    const { result, rerender } = renderHook(() => useUrlState());
    act(() => result.current.set({ offset: 50 }));
    // Someone else navigates: the URL moves under the pending write.
    setParams("batch=3");
    rerender();
    act(() => result.current.set({ offset: 100 }));
    expect(nav.replace).toHaveBeenLastCalledWith("/health?batch=3&offset=100", {
      scroll: false,
    });
  });
});
