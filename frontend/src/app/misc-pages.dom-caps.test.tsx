/**
 * Misc-page DOM caps (2026-10-01 campaign follow-up): the planner's
 * plan-name box (120), the ops-simulation per-row tag inputs (50) and the
 * simulation scenario save dialog's name (120) and notes (2 000) are all
 * bounded in the DOM at their schema limits.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { server, TEST_FARMS } from "@/test/msw-server";

import OpsSimulationPage from "./(app)/ops-simulation/page";
import PlannerPage from "./(app)/planner/page";
import SimulationPage from "./(app)/simulation/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner",
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

describe("misc page DOM caps", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/defaults", () =>
        HttpResponse.json({
          meta: { horizon_months: 60, start_year_month: "2026-01" },
          herd: { does: 50, bucks: 2, foundation_flock_state: "open" },
          finance: { interest_rate_annual: 0.12 },
          feed: { green_kg_per_head_per_day: 3 },
        }),
      ),
      http.get("/api/planner/plans", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/simulation/scenarios", () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
    );
  });

  it("planner caps the plan name at 120", async () => {
    renderWithProviders(<PlannerPage />, createTestQueryClient());
    expect(await screen.findByText("Sale targets")).toBeInTheDocument();
    expect(screen.getByLabelText("Plan name")).toHaveAttribute("maxlength", "120");
  });

  it("ops-simulation caps each herd-row tag at 50", async () => {
    renderWithProviders(<OpsSimulationPage />);
    await screen.findByDisplayValue("D1");
    const tags = screen.getAllByLabelText(/^Tag for row /);
    expect(tags.length).toBeGreaterThan(0);
    for (const tag of tags) expect(tag).toHaveAttribute("maxlength", "50");
  });

  it("simulation caps the scenario name at 120 and notes at 2 000", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    await screen.findByText("Horizon Months");
    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/^Name/)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/^notes/i)).toHaveAttribute("maxlength", "2000");
  });
});
