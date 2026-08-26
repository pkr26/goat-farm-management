/**
 * Feed recipes page — branch detail not covered by page.extended.test.tsx:
 * which nav tab is styled as the current one, the two pending states (the
 * permission probe and the recipes fetch) staying distinct from the error
 * state, the generic transport fallback, and how a recipe with no description
 * or no ingredient lines renders.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import RecipesPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding/recipes",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const PAYLOAD = {
  recipes: [
    {
      id: 1,
      code: "LACTATING_60_40",
      name: "Lactating 60/40",
      description: "For lactating does and growing kids",
      lines: [{ ingredient: "Crushed maize", kg_per_100kg: 14, category: "ENERGY" }],
    },
  ],
  allocation: [{ bucket: "BREEDING", allocation: "LACTATING_60_40" }],
};

function cardOf(title: string): HTMLElement {
  return screen.getByText(title).closest('[data-slot="card"]') as HTMLElement;
}

describe("RecipesPage branches", () => {
  beforeEach(() => {
    server.use(http.get("/api/feeding/recipes", () => HttpResponse.json(PAYLOAD)));
  });

  async function renderLoaded() {
    renderWithProviders(<RecipesPage />);
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  }

  // The tab strip is the only thing telling the operator which feeding page
  // they are on: exactly one tab carries the underline + solid label.
  it("styles only the current feeding tab as active", async () => {
    await renderLoaded();

    const current = screen.getByRole("link", { name: "Recipes" });
    expect(current).toHaveClass("border-primary", "font-medium", "text-foreground");
    expect(current).not.toHaveClass("border-transparent");
    expect(current).not.toHaveClass("text-muted-foreground");

    for (const name of ["Today's plan", "Inventory"]) {
      const other = screen.getByRole("link", { name });
      expect(other).toHaveClass("border-transparent", "text-muted-foreground");
      expect(other).not.toHaveClass("border-primary");
      expect(other).not.toHaveClass("font-medium");
    }
  });

  it("shows the loading state while the permission probe is in flight", async () => {
    server.use(http.get("/api/auth/permissions", () => new Promise<Response>(() => {})));
    renderWithProviders(<RecipesPage />);

    expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
    // An unresolved probe is not a denial — never accuse the operator of
    // lacking access before the answer arrives.
    expect(screen.queryByText(/don't have access to this page/)).not.toBeInTheDocument();
  });

  it("shows the loading state while the recipes request is in flight", async () => {
    const requested = vi.fn();
    server.use(
      http.get("/api/feeding/recipes", () => {
        requested();
        return new Promise<Response>(() => {});
      }),
    );
    renderWithProviders(<RecipesPage />);

    // Wait past the permission probe: only then is the page's "Loading…"
    // reporting the recipes fetch rather than the probe.
    await waitFor(() => expect(requested).toHaveBeenCalled());
    // A request still on the wire is not a failure.
    await waitFor(() =>
      expect(screen.queryByText("Could not load recipes.")).not.toBeInTheDocument(),
    );
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
  });

  it("falls back to a generic message when the recipes request fails in transport", async () => {
    // A dropped connection never reaches the ApiError mapping, so there is no
    // server detail to quote.
    server.use(http.get("/api/feeding/recipes", () => HttpResponse.error()));
    renderWithProviders(<RecipesPage />);

    expect(await screen.findByText("Could not load recipes.")).toBeInTheDocument();
  });

  it("renders the recipe description as its own muted paragraph", async () => {
    await renderLoaded();

    const description = screen.getByText("For lactating does and growing kids");
    expect(description.tagName).toBe("P");
    expect(description).toHaveClass("text-sm", "text-muted-foreground");
  });

  it("renders only the header row for a recipe the API sent without lines", async () => {
    server.use(
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({
          recipes: [
            {
              id: 3,
              code: "MAINTENANCE",
              name: "Maintenance",
              description: null,
              // `lines` is optional on FeedRecipeOut — an absent list is an
              // empty ingredient table, not a phantom row.
            },
          ],
          allocation: [],
        }),
      ),
    );
    renderWithProviders(<RecipesPage />);
    expect(await screen.findByText("Maintenance")).toBeInTheDocument();

    const card = cardOf("Maintenance");
    expect(within(card).getAllByRole("row")).toHaveLength(1);
    expect(within(card).getByRole("columnheader", { name: "Ingredient" })).toBeInTheDocument();
    expect(within(card).queryByRole("cell")).not.toBeInTheDocument();
  });
});
