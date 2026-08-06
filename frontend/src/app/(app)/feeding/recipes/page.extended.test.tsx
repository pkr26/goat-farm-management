/**
 * Feed recipes page: recipe cards with ingredient lines, the bucket→recipe
 * allocation reference table, nav tabs, empty/error states and RBAC gating.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
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
      lines: [
        { ingredient: "Super Napier green fodder", kg_per_100kg: 36, category: "GREEN" },
        { ingredient: "Crushed maize", kg_per_100kg: 14, category: "ENERGY" },
      ],
    },
    {
      id: 2,
      code: "FATTENING_50_50",
      name: "Fattening 50/50",
      description: null,
      lines: [{ ingredient: "Maize DDGS", kg_per_100kg: 10, category: "PROTEIN" }],
    },
  ],
  allocation: [
    { bucket: "BREEDING", allocation: "LACTATING_60_40" },
    { bucket: "QUARANTINE", allocation: "dry roughage only (days 1–3) → MAINTENANCE" },
  ],
};

describe("RecipesPage", () => {
  beforeEach(() => {
    server.use(http.get("/api/feeding/recipes", () => HttpResponse.json(PAYLOAD)));
  });

  async function renderLoaded() {
    renderWithProviders(<RecipesPage />);
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  }

  it("renders a card per recipe with code badge and description", async () => {
    await renderLoaded();

    expect(screen.getByRole("heading", { name: "TMR recipes" })).toBeInTheDocument();
    // Code badge on the card (also echoed in the allocation table below).
    expect(screen.getAllByText("LACTATING_60_40").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("For lactating does and growing kids")).toBeInTheDocument();
    expect(screen.getByText("FATTENING_50_50")).toBeInTheDocument();
  });

  it("renders ingredient lines with kg/100kg and category", async () => {
    await renderLoaded();

    const row = screen.getByText("Super Napier green fodder").closest("tr") as HTMLElement;
    expect(within(row).getByText("36")).toBeInTheDocument();
    expect(within(row).getByText("GREEN")).toBeInTheDocument();

    const maize = screen.getByText("Crushed maize").closest("tr") as HTMLElement;
    expect(within(maize).getByText("14")).toBeInTheDocument();
    expect(within(maize).getByText("ENERGY")).toBeInTheDocument();
  });

  it("omits the description paragraph when the recipe has none", async () => {
    await renderLoaded();

    const card = screen.getByText("Fattening 50/50").closest("div");
    expect(card).not.toBeNull();
    // Only the other recipe's description exists.
    expect(screen.getAllByText(/For lactating does/)).toHaveLength(1);
  });

  it("renders the bucket → recipe allocation reference table", async () => {
    await renderLoaded();

    expect(
      screen.getByRole("heading", { name: "Bucket → recipe allocation (reference)" }),
    ).toBeInTheDocument();
    const row = screen
      .getByText("dry roughage only (days 1–3) → MAINTENANCE")
      .closest("tr") as HTMLElement;
    expect(within(row).getByText("QUARANTINE")).toBeInTheDocument();
  });

  it("links the feeding nav tabs", async () => {
    await renderLoaded();

    expect(screen.getByRole("link", { name: "Today's plan" })).toHaveAttribute("href", "/feeding");
    expect(screen.getByRole("link", { name: "Recipes" })).toHaveAttribute(
      "href",
      "/feeding/recipes",
    );
    expect(screen.getByRole("link", { name: "Inventory" })).toHaveAttribute(
      "href",
      "/feeding/inventory",
    );
  });

  it("renders no recipe cards when the list is empty", async () => {
    server.use(
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({ recipes: [], allocation: [{ bucket: "BREEDING", allocation: "X" }] }),
      ),
    );
    renderWithProviders(<RecipesPage />);

    expect(
      await screen.findByRole("heading", { name: "Bucket → recipe allocation (reference)" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("LACTATING_60_40")).not.toBeInTheDocument();
  });

  it("shows the error detail when the recipes GET fails", async () => {
    server.use(
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({ detail: "recipes unavailable" }, { status: 500 }),
      ),
    );
    renderWithProviders(<RecipesPage />);
    expect(await screen.findByText("recipes unavailable")).toBeInTheDocument();
  });

  it("denies access without feeding.view and never calls the endpoint", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/feeding/recipes", () => {
        calls += 1;
        return HttpResponse.json(PAYLOAD);
      }),
    );
    renderWithProviders(<RecipesPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });
});
