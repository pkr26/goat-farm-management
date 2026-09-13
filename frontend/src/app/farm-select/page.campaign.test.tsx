/**
 * Farm-select page — fresh-domain mutation campaign kills (2026-09): the
 * empty-list branch must not render an (empty) farm grid, and the standalone
 * Sign out button must actually sign the operator out.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FarmSelectPage from "./page";

const { pushMock, replaceMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  navState: { search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/farm-select",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

describe("FarmSelectPage — campaign kills", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    navState.search = "";
  });

  it("renders no farm grid when the membership list is empty", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));
    const { container } = renderWithProviders(<FarmSelectPage />);

    expect(
      await screen.findByText("No farms yet — create your first one below."),
    ).toBeInTheDocument();
    // The farm-card grid itself must be absent: an empty list that still
    // renders the card grid takes layout space and reads as a broken picker.
    // ([class~=…] matches whole words, so the form card-header's own grid
    // utilities do not collide with the picker's "grid gap-3" container.)
    expect(container.querySelector('main div[class~="grid"][class~="gap-3"]')).toBeNull();
  });

  it("signs the operator out from the standalone Sign out button", async () => {
    let logouts = 0;
    server.use(
      http.post("/api/auth/logout", () => {
        logouts += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Your farms" })).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(logouts).toBe(1));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });
});
