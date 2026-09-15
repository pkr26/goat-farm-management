/**
 * Simulation page — meat-mode fields (2026-09 final contract): the editor
 * exposes the surplus-milk side-line, the weaning/growth-regime selects, the
 * NLM subsidy toggle and the per-class water planning rates, while the
 * results surface the water demand figures.
 */

import { screen, waitFor, within } from "@testing-library/react";
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

const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  reproduction: { lactation_months: 2, weaning_days: 60 },
  growth: { growth_regime: "stall_fed" },
  sales: { milk_sale_litres_per_doe_day: 0, milk_price_per_litre: 30 },
  feed: { water_litres_lactating_doe_per_day: 12 },
  finance: { nlm_subsidy: false },
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
    meat_price_per_kg: 350,
    culls_head: 0,
    cull_revenue: 0,
    milk_revenue: 0,
    manure_revenue: 0,
    purchases_head: 0,
    purchase_cost: 0,
    feed_green_kg: 0,
    feed_homegrown_green_kg: 0,
    feed_purchased_green_kg: 0,
    feed_dry_kg: 0,
    feed_concentrate_kg: 0,
    feed_cost: 4000,
    vet_cost: 500,
    labour_cost: 2000,
    insurance_cost: 100,
    misc_cost: 400,
    selling_cost: 0,
    depreciation: 0,
    tax: 0,
    terminal_value: 0,
    debt_service: 0,
    net_cash_flow: -7000,
    cumulative_cash_flow: -7000,
    cash_balance: 43000,
    fodder_surplus_kg: 0,
    fodder_stock_kg_dm: 0,
    fodder_waste_kg_dm: 0,
    water_litres: 12500,
    events: [],
    ...overrides,
  };
}

const RESULT = {
  months: [monthRow()],
  annual_pl: [],
  metrics: {
    project_cost: 500000,
    loan_amount: 250000,
    subsidy_amount: 100000,
    equity: 150000,
    npv: 234567,
    irr: 0.18,
    mirr: 0.16,
    bcr: 1.42,
    dscr_per_year: [1.8],
    avg_dscr: 1.8,
    min_dscr: 1.8,
    payback_month: 30,
    break_even_meat_price_per_kg: 320,
    peak_capacity_head: 58,
    terminal_value: 150000,
    tax_total: 0,
    accounting_profit_total: 25000,
    minimum_cash_balance: 43000,
    minimum_cash_month: 1,
    additional_working_capital_required: 0,
    operating_margin: 0.25,
  },
  amortization: [],
  feed_summary: {
    annual_green_kg: [],
    annual_homegrown_green_kg: [],
    annual_purchased_green_kg: [],
    annual_dry_kg: [],
    annual_concentrate_kg: [],
    annual_feed_cost: [],
    annual_fodder_waste_kg_dm: [],
    land_requirement_acres: 0,
    fodder_deficit_months: 0,
    peak_fodder_stock_kg_dm: 0,
  },
  project_cost_breakdown: {
    shed_cost: 200000,
    equipment_cost: 50000,
    stock_cost: 200000,
    working_capital: 50000,
    capacity_places: 58,
    capacity_basis: "projected_peak",
    projected_peak_head: 52.4,
  },
  terminal_value_breakdown: {
    livestock: 100000,
    shed: 30000,
    equipment: 5000,
    working_capital: 15000,
    total: 150000,
  },
  model_version: "3.0.0",
  assumptions_fingerprint: "0123456789abcdef0123456789abcdef",
  metric_explanations: [],
  narrative_report: [],
  monte_carlo: null,
  sensitivity: null,
  optimization: null,
  annual_water_litres: [150000],
  peak_water_litres_per_day: 470,
};

function registerApiHandlers(options: { onRun?: (body: unknown) => void } = {}) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
    http.get("/api/simulation/calibration", () => HttpResponse.json({})),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.(await request.json());
      return HttpResponse.json(RESULT);
    }),
  );
}

async function renderLoaded(options: Parameters<typeof registerApiHandlers>[0] = {}) {
  registerApiHandlers(options);
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
}

describe("SimulationPage — meat-mode fields", () => {
  it("renders the surplus-milk, weaning, regime, subsidy and water inputs", async () => {
    await renderLoaded();

    // The weaning interval keeps its (meat-mode) lactation field.
    expect(screen.getByLabelText("Lactation Months")).toHaveAttribute("data-unit", "months");
    // The surplus-milk side-line is bounded 0–10 litres/doe/day.
    const milkSale = screen.getByLabelText("Milk Sale Litres Per Doe Day");
    expect(milkSale).toHaveAttribute("data-unit", "litres/doe/day");
    expect(milkSale).toHaveAttribute("max", "10");
    expect(screen.getByLabelText("Milk Price Per Litre")).toHaveAttribute(
      "data-unit",
      "₹/litre",
    );
    // The water planning rate is captioned in litres/day.
    expect(screen.getByLabelText("Water Litres Lactating Doe Per Day")).toHaveAttribute(
      "data-unit",
      "litres/day",
    );
    // Weaning policy and growth regime are closed-set selects.
    expect(screen.getByLabelText("Weaning Days")).toHaveTextContent(
      "60 days (operational standard)",
    );
    expect(screen.getByLabelText("Growth Regime")).toHaveTextContent("Stall-fed");
    // The NLM toggle is a checkbox.
    expect(screen.getByRole("checkbox", { name: "Nlm Subsidy" })).toBeInTheDocument();
  });

  it("sends the weaning select as a number, not a string", async () => {
    let runBody:
      | { assumptions?: { reproduction?: { weaning_days?: unknown } } }
      | undefined;
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        runBody = body as typeof runBody;
      },
    });

    await user.click(screen.getByLabelText("Weaning Days"));
    await user.click(
      await screen.findByRole("option", { name: "90 days (research standard)" }),
    );
    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    await waitFor(() => expect(runBody?.assumptions?.reproduction?.weaning_days).toBe(90));
  });

  it("shows the water demand in the metric cards, the annual line and the monthly table", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Source: Current editor assumptions")).toBeInTheDocument();

    expect(screen.getByText("Peak water demand")).toBeInTheDocument();
    expect(screen.getByText("470 L/day")).toBeInTheDocument();
    expect(screen.getByText(/Annual water demand: Y1 1,50,000 L/)).toBeInTheDocument();
    const projection = screen
      .getByText("Herd and cash-flow detail over 1 months.")
      .closest("[data-slot='card']") as HTMLElement;
    expect(
      within(projection).getByRole("columnheader", { name: "Water (L)" }),
    ).toBeInTheDocument();
    expect(within(projection).getByText("12,500")).toBeInTheDocument();
  });
});
