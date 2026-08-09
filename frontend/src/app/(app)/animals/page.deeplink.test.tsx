// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// /animals/new redirects to /animals?new=1 to auto-open the Add-animal
// dialog. AnimalsPageContent strips the flag from the URL in an effect that
// runs on its FIRST commit — while the page is still rendering the
// permissions "Loading…" placeholder, i.e. before CreateAnimalDialog has ever
// been mounted. `startOpen` used to be read from the live search params at
// the render where the dialog finally mounts, by which time `new=1` was gone,
// so the deep link silently did nothing on a cold load. The flag is now
// latched on the first render (as /breeding and /kidding already do).
//
// The navigation mock below deliberately reflects the committed replacement,
// which is what page.test.tsx's static mock cannot do.

import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    prefetch: vi.fn(),
    // Mirror the real router: the replacement is visible to later renders.
    replace: (url: string) => {
      navState.search = new URL(url, "http://localhost").search;
    },
  }),
  usePathname: () => "/animals",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

beforeEach(() => {
  navState.search = "";
  server.use(http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })));
});

describe("AnimalsPage ?new=1 deep link", () => {
  it("opens the create dialog even though the flag is stripped before the dialog mounts", async () => {
    navState.search = "?new=1";
    renderWithProviders(<AnimalsPage />);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    // The flag is still cleaned out of the URL so a reload doesn't reopen it.
    expect(navState.search).toBe("");
  });

  it("does not open the dialog without the flag", async () => {
    renderWithProviders(<AnimalsPage />);
    await screen.findByText("No animals match these filters.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
