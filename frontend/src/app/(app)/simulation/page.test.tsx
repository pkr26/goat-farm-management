/**
 * Simulation page: RBAC gating, defaults auto-load into the generic
 * assumptions editor, ad-hoc run results (metric cards + annual P&L),
 * scenario save with list refresh, and the run error state.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const MANAGE_PERMS = ["simulation.view", "simulation.manage"];

/** Sparse but valid full assumptions object (only present keys render). */
const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2, foundation_flock_state: "open" },
  finance: { interest_rate_annual: 0.12 },
};

function monthRow(overrides: Record<string, unknown> = {}) {
  return {
    month: 1,
    calendar_month: 1,
    f_kids: 0,
    f_weaners: 0,
    f_growers: 0,
    open_does: 50,
    pregnant_does: 0,
    lactating_does: 0,
    m_kids: 0,
    m_weaners: 0,
    m_growers: 0,
    bucks: 2,
    total_herd: 52,
    births: 0,
    deaths: 0,
    sales_head: 0,
    sales_revenue: 0,
    culls_head: 0,
    cull_revenue: 0,
    milk_revenue: 0,
    manure_revenue: 0,
    purchases_head: 0,
    purchase_cost: 0,
    feed_green_kg: 0,
    feed_dry_kg: 0,
    feed_concentrate_kg: 0,
    feed_cost: 4000,
    vet_cost: 500,
    labour_cost: 2000,
    insurance_cost: 100,
    misc_cost: 400,
    debt_service: 0,
    net_cash_flow: -7000,
    cumulative_cash_flow: -7000,
    fodder_surplus_kg: 0,
    ...overrides,
  };
}

const RESULT = {
  months: [monthRow(), monthRow({ month: 2, calendar_month: 2, births: 4 })],
  annual_pl: [
    {
      year: 1,
      meat_revenue: 100000,
      cull_revenue: 5000,
      milk_revenue: 10000,
      manure_revenue: 5000,
      total_revenue: 120000,
      feed_cost: 48000,
      vet_cost: 6000,
      labour_cost: 24000,
      insurance_cost: 2000,
      misc_cost: 10000,
      stock_purchases: 0,
      total_opex: 90000,
      ebitda: 30000,
      interest: 0,
      principal: 0,
      debt_service: 0,
      net_cash_flow: 30000,
    },
  ],
  metrics: {
    project_cost: 500000,
    loan_amount: 250000,
    subsidy_amount: 100000,
    equity: 150000,
    npv: 234567,
    irr: 0.18,
    bcr: 1.42,
    dscr_per_year: [1.8],
    avg_dscr: 1.8,
    min_dscr: 1.8,
    payback_month: 30,
    break_even_meat_price_per_kg: 320,
  },
  amortization: [],
  feed_summary: {
    annual_green_kg: [],
    annual_dry_kg: [],
    annual_concentrate_kg: [],
    annual_feed_cost: [],
    land_requirement_acres: 0,
    fodder_deficit_months: 0,
  },
  monte_carlo: null,
  sensitivity: null,
};

/** Breeds + defaults + (empty or mutable) scenario list used by every test. */
function registerApiHandlers(scenarios: unknown[] = []) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
    http.get("/api/simulation/scenarios", () => HttpResponse.json(scenarios)),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(scenarios: unknown[] = []) {
  registerApiHandlers(scenarios);
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
}

describe("SimulationPage", () => {
  it("denies access without simulation.view and never calls the API", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/simulation/defaults", () => {
        calls += 1;
        return HttpResponse.json(DEFAULTS);
      }),
    );
    renderWithProviders(<SimulationPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("auto-loads defaults and renders assumption sections and fields", async () => {
    await renderLoaded();

    // One collapsible section per top-level key, humanized field labels.
    expect(screen.getByText("Meta")).toBeInTheDocument();
    expect(screen.getByText("Herd")).toBeInTheDocument();
    expect(screen.getByText("Finance")).toBeInTheDocument();
    expect(screen.getByLabelText("Horizon Months")).toHaveValue(60);
    expect(screen.getByLabelText("Does")).toHaveValue(50);
    expect(screen.getByText("Foundation Flock State")).toBeInTheDocument();
  });

  it("runs the simulation and shows metric cards and annual P&L rows", async () => {
    server.use(
      http.post("/api/simulation/run", () => HttpResponse.json(RESULT)),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(screen.getByText("IRR")).toBeInTheDocument();
    expect(screen.getByText("18.0%")).toBeInTheDocument();
    expect(screen.getByText("Payback month")).toBeInTheDocument();
    // Annual P&L: year 1 row with revenue, opex and net cash flow.
    const yearRow = (await screen.findByText("₹1,20,000")).closest("tr") as HTMLElement;
    expect(within(yearRow).getByText("1")).toBeInTheDocument();
    expect(within(yearRow).getByText("₹90,000")).toBeInTheDocument();
    // EBITDA and net cash flow are both 30,000 in the fixture.
    expect(within(yearRow).getAllByText("₹30,000")).toHaveLength(2);
  });

  it("saves a scenario and shows it in the refreshed list", async () => {
    const scenarios: unknown[] = [];
    server.use(
      http.post("/api/simulation/scenarios", async ({ request }) => {
        const body = (await request.json()) as {
          name: string;
          notes?: string;
          assumptions: unknown;
        };
        const scenario = {
          id: 1,
          farm_id: 1,
          name: body.name,
          notes: body.notes ?? "",
          assumptions: body.assumptions,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-02T00:00:00Z",
        };
        scenarios.push(scenario);
        return HttpResponse.json(scenario, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded(scenarios);
    expect(screen.getByText("No saved scenarios yet.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Name/), "Base plan");
    await user.click(within(dialog).getByRole("button", { name: "Save scenario" }));

    expect(await screen.findByText("Base plan")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("No saved scenarios yet.")).not.toBeInTheDocument();
  });

  it("shows the server detail inline when the run fails", async () => {
    server.use(
      http.post("/api/simulation/run", () =>
        HttpResponse.json({ detail: "engine exploded" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(await screen.findByText("engine exploded")).toBeInTheDocument();
  });
});
