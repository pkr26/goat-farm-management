/**
 * Feed recipes page — mutation-hardening suite: the empty recipe catalog,
 * species-aware allocation bucket labels, recipe card content, the recipe
 * load-error retry and the permissions-error retry.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import RecipesPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding/recipes",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function recipesHandler(payload: unknown) {
  return http.get("/api/feeding/recipes", () => HttpResponse.json(payload));
}

async function renderLoaded() {
  renderWithProviders(<RecipesPage />);
  // The feeding tab strip only renders once the page has data.
  await screen.findByRole("navigation");
}

describe("RecipesPage — catalog rendering", () => {
  it("shows the empty state when no recipes are provisioned", async () => {
    server.use(recipesHandler({ recipes: [], allocation: [] }));
    await renderLoaded();

    expect(screen.getByText("No recipes configured.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The recipe catalog is provisioned by the farm's feed setup; contact an administrator.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Ingredient")).not.toBeInTheDocument();
  });

  it("renders recipe cards with codes, descriptions and ingredient lines", async () => {
    server.use(
      recipesHandler({
        recipes: [
          {
            id: 1,
            code: "LACTATING_60_40",
            name: "Lactating 60/40",
            description: "For milking does",
            lines: [
              { ingredient: "Crushed maize", kg_per_100kg: 40, category: "ENERGY" },
              { ingredient: "Lucerne hay", kg_per_100kg: 60, category: "GREEN" },
            ],
          },
        ],
        allocation: [],
      }),
    );
    await renderLoaded();

    // A non-empty catalog must not also render the empty state beside it.
    expect(screen.queryByText("No recipes configured.")).not.toBeInTheDocument();
    expect(screen.getByText("Lactating 60/40")).toBeInTheDocument();
    expect(screen.getByText("LACTATING_60_40")).toBeInTheDocument();
    expect(screen.getByText("For milking does")).toBeInTheDocument();
    expect(screen.getByText("Crushed maize")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("Lucerne hay")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
  });

  it("resolves allocation bucket codes to species labels", async () => {
    server.use(
      recipesHandler({
        recipes: [],
        allocation: [
          { bucket: "PREGNANCY_EARLY", allocation: "LACTATING_60_40" },
          { bucket: "MALE_KIDS", allocation: "FATTENING_50_50" },
        ],
      }),
    );
    await renderLoaded();

    const table = screen
      .getByText("Bucket → recipe allocation (reference)")
      .closest('[data-slot="card"]') as HTMLElement;
    expect(within(table).getByText("Pregnancy A")).toBeInTheDocument();
    expect(within(table).getByText("Male kids")).toBeInTheDocument();
    expect(within(table).queryByText("PREGNANCY_EARLY")).not.toBeInTheDocument();
    expect(within(table).getByText("LACTATING_60_40")).toBeInTheDocument();
  });
});

describe("RecipesPage — error and retry paths", () => {
  it("retries the recipe load from the error state", async () => {
    let calls = 0;
    server.use(
      http.get("/api/feeding/recipes", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "unavailable" }, { status: 503 })
          : HttpResponse.json({ recipes: [], allocation: [] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<RecipesPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("unavailable");
    await user.click(screen.getByRole("button", { name: "Retry recipes" }));

    await waitFor(() => expect(calls).toBe(2));
    expect(await screen.findByText("No recipes configured.")).toBeInTheDocument();
  });

  it("recovers the page when the permissions probe is retried", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return permsCalls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["feeding.view"] });
      }),
      recipesHandler({ recipes: [], allocation: [] }),
    );
    const user = userEvent.setup();
    renderWithProviders(<RecipesPage />);

    await user.click(await screen.findByRole("button", { name: /retry/i }));
    expect(await screen.findByText("No recipes configured.")).toBeInTheDocument();
  });
});
