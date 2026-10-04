/**
 * Ops-simulation kilogram formatting: whole-kilogram shares print with NO decimals
 * ("2", "1") while fractional shares print exactly one ("1.2") — the formatter's
 * whole/fractional split pinned at both arms.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import OpsSimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/ops-simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

function feedLine(
  day: number,
  kgPerHead: number,
  heads: number,
  building = "BREEDING",
) {
  const daily = heads * kgPerHead;
  return {
    day,
    building,
    recipe: "MAINTENANCE_75_25",
    recipe_display: "Maintenance 75:25",
    heads,
    kg_per_head: kgPerHead,
    morning_kg: daily * 0.4,
    afternoon_kg: daily * 0.2,
    night_kg: daily * 0.4,
  };
}

describe("OpsSimulationPage formatKg digits", () => {
  it("prints whole shares without decimals and fractional shares with one", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/ops-sim/run", () =>
        HttpResponse.json({
          result: {
            model_version: "1.0.0",
            species: "GOAT",
            start_date: "2026-09-03",
            horizon_days: 1,
            seed: 11,
            head_start: 8,
            transition_counts: [],
            journeys: [],
            explanations: [],
            notes: [],
            totals: {
              feed_kg_by_recipe: {},
              tasks_by_category: {},
              tasks_by_role: {},
              vet_tasks_by_building: {},
              building_days: {},
              services: 0,
              conceptions: 0,
              failed_services: 0,
              kids_born_alive: 0,
              kids_born_dead: 0,
              deaths: 0,
              culls: 0,
              sales: 0,
              moves: 0,
            },
            days: [
              {
                day: 1,
                date: "2026-09-03",
                tasks: [],
                moves: [],
                births: [],
                exits: [],
                // 5 × 1 = 5 kg → shares 2 / 1 / 2 (whole);
                // 3 × 1 = 3 kg → shares 1.2 / 0.6 / 1.2 (fractional).
                feeding: [feedLine(1, 1, 5), feedLine(1, 1, 3, "GROWER")],
                occupancy: [{ building: "BREEDING", heads: 5 }],
              },
            ],
          },
          ledger: null,
        }),
      ),
    );
    renderWithProviders(<OpsSimulationPage />);
    await user.click(await screen.findByRole("button", { name: /^Run 90 days/ }));

    // Day 1 is selected by default once a result lands.
    const feedingTable = screen
      .getAllByRole("table")
      .find((t) => within(t).queryAllByText(/Maintenance 75:25/).length > 0);
    expect(feedingTable).toBeDefined();
    const cells = within(feedingTable!)
      .getAllByRole("row")
      .slice(1)
      .map((row) =>
        within(row)
          .getAllByRole("cell")
          .slice(-3)
          .map((cell) => cell.textContent),
      );
    expect(cells).toEqual([
      ["2", "1", "2"],
      ["1.2", "0.6", "1.2"],
    ]);
  });
});
