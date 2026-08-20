/** /kidding/new?breeding_id=… shim: redirects to /kidding preserving the query
 *  string (the kidding page auto-opens the record dialog from breeding_id). */

import { render, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import KiddingNewRedirect from "./page";

const { replaceMock, searchParams } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  searchParams: { current: new URLSearchParams() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/kidding/new",
  useSearchParams: () => searchParams.current,
  useParams: () => ({}),
}));

describe("/kidding/new redirect shim", () => {
  afterEach(() => {
    replaceMock.mockClear();
    searchParams.current = new URLSearchParams();
  });

  it("redirects once to /kidding preserving the query string under Strict Mode", async () => {
    searchParams.current = new URLSearchParams("breeding_id=12");

    render(
      <StrictMode>
        <KiddingNewRedirect />
      </StrictMode>,
    );

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/kidding?breeding_id=12"),
    );
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("redirects to plain /kidding without a query string", async () => {
    render(<KiddingNewRedirect />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/kidding"));
  });

  // Next reuses this component across a query-only navigation instead of
  // remounting it. Keyed only on `router`, the effect would fire once and the
  // second task link would be dropped — sending the operator to the FIRST
  // doe's kidding dialog while they believe they picked the second.
  it("re-redirects when only the query string changes", async () => {
    searchParams.current = new URLSearchParams("breeding_id=12");
    const { rerender } = render(<KiddingNewRedirect />);
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/kidding?breeding_id=12"),
    );

    searchParams.current = new URLSearchParams("breeding_id=99");
    rerender(<KiddingNewRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/kidding?breeding_id=99"),
    );
  });
});
