/**
 * animals/new shim — fresh-domain mutation campaign kills (2026-09): the
 * redirect must preserve the caller's query and fall back to the bare list
 * path when there is nothing to preserve.
 */

import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import NewAnimalPage from "./page";

const { replaceMock, navState } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  navState: { search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/animals/new",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

describe("NewAnimalPage — campaign kills", () => {
  beforeEach(() => {
    replaceMock.mockClear();
    navState.search = "";
  });

  it("redirects to the list with new=1 even for a bare visit", async () => {
    renderWithProviders(<NewAnimalPage />);
    expect(await screen.findByRole("status")).toBeInTheDocument();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/animals?new=1"));
  });

  it("preserves the caller's query and merges new=1", async () => {
    navState.search = "?returnTo=%2Fanimals%3Fq%3DG-1";
    renderWithProviders(<NewAnimalPage />);
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith(
        "/animals?returnTo=%2Fanimals%3Fq%3DG-1&new=1",
      ),
    );
  });
});
