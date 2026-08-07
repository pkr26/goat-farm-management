/** /kidding/new?breeding_id=… shim: bounces to /kidding preserving the query
 *  string (the kidding page auto-opens the record dialog from breeding_id). */

import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import KiddingNewRedirect from "./page";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/kidding/new",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("/kidding/new redirect shim", () => {
  afterEach(() => {
    replaceMock.mockClear();
    window.history.replaceState({}, "", "/");
  });

  it("redirects to /kidding preserving the query string", async () => {
    window.history.replaceState({}, "", "/kidding/new?breeding_id=12");

    render(<KiddingNewRedirect />);

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/kidding?breeding_id=12"),
    );
  });

  it("redirects to plain /kidding without a query string", async () => {
    window.history.replaceState({}, "", "/kidding/new");

    render(<KiddingNewRedirect />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/kidding"));
  });
});
