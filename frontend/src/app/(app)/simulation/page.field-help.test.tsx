/**
 * Simulation assumptions editor: the "?" explanation affordance and the
 * species-aware vocabulary.
 *
 * - every field label carries a "?" that opens a dialog explaining the term
 *   plus the unit, allowed values and current value the editor derives;
 * - a buffalo dairy reads its own nouns (milking buffalo / bull / calf) in
 *   labels and help text, while a goat farm keeps the goat labels;
 * - section headings explain what each assumptions group covers;
 * - the results tables surface cull disposals and the milk/meat/cull revenue
 *   split — on a dairy these are material lines that used to be invisible.
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

const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: {
    does: 50,
    bucks: 2,
    foundation_doe_age_min_months: 18,
    foundation_doe_age_max_months: 42,
    foundation_flock_state: "open",
  },
  reproduction: { lactation_months: 3 },
  risk: {
    monte_carlo_runs: 500,
    meat_price: { enabled: true, low: 0.8, high: 1.2 },
    kid_mortality: { enabled: true, low: 0.6, high: 1.6 },
  },
};

const RUN_RESULT = {
  months: [
    {
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
      sales_head: 2,
      sales_revenue: 3200,
      meat_price_per_kg: 160,
      culls_head: 1.5,
      cull_revenue: 75000,
      milk_revenue: 120000,
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
      net_cash_flow: 0,
      cumulative_cash_flow: -50000,
      cash_balance: 10000,
      fodder_surplus_kg: 0,
      fodder_stock_kg_dm: 0,
      fodder_waste_kg_dm: 0,
      events: [],
    },
  ],
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
      selling_cost: 0,
      stock_purchases: 0,
      total_opex: 90000,
      ebitda: 30000,
      depreciation: 5000,
      ebit: 25000,
      interest: 0,
      profit_before_tax: 25000,
      tax: 0,
      profit_after_tax: 25000,
      principal: 0,
      debt_service: 0,
      terminal_value: 0,
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
    minimum_cash_balance: 10000,
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
};

function registerApiHandlers(options?: { dairy?: boolean; run?: boolean }) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({
        breeds: options?.dairy ? ["murrah_dairy"] : ["osmanabadi"],
        systems: ["stall_fed"],
      }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
  );
  if (options?.dairy) {
    // useFarmType() reads the authenticated farm list; a buffalo farm makes
    // the editor speak the dairy vocabulary.
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          {
            id: 7,
            name: "Navipet Dairy",
            location: "Navipet",
            timezone: "Asia/Kolkata",
            role: null,
            farm_type: "BUFFALO_DAIRY",
          },
        ]),
      ),
    );
  }
  if (options?.run) {
    server.use(
      http.post("/api/simulation/run", () => HttpResponse.json(RUN_RESULT)),
    );
  }
}

async function renderLoaded(options?: { dairy?: boolean; run?: boolean }) {
  registerApiHandlers(options);
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  return userEvent.setup();
}

/** The facts list inside an open field-help dialog, as term → value pairs. */
function dialogFacts(): Record<string, string> {
  const dialog = screen.getByRole("dialog");
  const facts: Record<string, string> = {};
  within(dialog)
    .getAllByRole("definition")
    .forEach((definition) => {
      const term = within(definition.parentElement as HTMLElement)
        .getAllByRole("term")
        .find((node) => node.parentElement === definition.parentElement);
      if (term) facts[term.textContent ?? ""] = definition.textContent ?? "";
    });
  return facts;
}

describe("SimulationPage field explanations", () => {
  it("opens a plain-language explanation with unit, allowed values and current value", async () => {
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: /Explain Horizon Months\?/ }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Horizon Months")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/How many months the projection runs/),
    ).toBeInTheDocument();
    expect(dialogFacts()).toEqual({
      Unit: "months",
      "Allowed values": "at least 12, at most 240, whole numbers only",
      "Current value": "60",
    });

    // The dialog closes again and the editor is untouched.
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Horizon Months")).toHaveValue(60);
  });

  it("explains the foundation-age window the user asked about", async () => {
    const user = await renderLoaded();

    await user.click(
      screen.getByRole("button", { name: /Explain Foundation Doe Age Min Months\?/ }),
    );

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getAllByText(/foundation doe/i).length).toBeGreaterThan(0);
    // spread uniformly across this window
    expect(within(dialog).getByText(/spread uniformly/i)).toBeInTheDocument();
    expect(dialogFacts()["Current value"]).toBe("18");
  });

  it("explains each assumptions section from its heading", async () => {
    const user = await renderLoaded();

    // The "?" sits inside the <summary>; clicking it must open the dialog
    // WITHOUT toggling the section closed.
    await user.click(screen.getByRole("button", { name: /Explain Herd\?/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Herd assumptions")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/animals on the ground on day 1/i),
    ).toBeInTheDocument();

    await user.keyboard("{Escape}");
    // Section stayed open: its fields are still reachable.
    expect(screen.getByLabelText("Foundation Doe Age Min Months")).toBeVisible();
  });

  it("explains Monte Carlo risk variables and their low/high multipliers", async () => {
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: /Explain Meat Price\?/ }));
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Meat price")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/random multiplier between the low and high/i),
    ).toBeInTheDocument();

    await user.keyboard("{Escape}");
    // The subfield button explains the multiplier bounds specifically.
    const meatPriceGroup = screen
      .getByText("Meat Price")
      .closest("div.rounded-lg") as HTMLElement;
    await user.click(
      within(meatPriceGroup).getByRole("button", { name: /Explain Low\?/ }),
    );
    dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Meat price — low")).toBeInTheDocument();
    expect(within(dialog).getByText(/Must bracket 1\.0/)).toBeInTheDocument();
    expect(dialogFacts()).toEqual({
      Unit: "multiplier",
      "Allowed values": "greater than 0, at most 100",
      "Current value": "0.8",
    });
  });
});

describe("SimulationPage species-aware vocabulary", () => {
  it("keeps goat nouns on a goat farm", async () => {
    await renderLoaded();
    expect(screen.getByLabelText("Does")).toHaveValue(50);
    expect(screen.getByLabelText("Bucks")).toHaveValue(2);
    expect(screen.getByText("Kid Mortality")).toBeInTheDocument();
  });

  it("speaks dairy nouns on a buffalo farm — labels and help text", async () => {
    const user = await renderLoaded({ dairy: true });

    // The same fields, dairy words: no "doe"/"buck"/"kid" anywhere in labels.
    // The dairy vocabulary lands once the authenticated farm list resolves
    // and the species-sync effect reloads the editor on the dairy preset.
    expect(await screen.findByLabelText("Milking Buffalo", {}, { timeout: 4000 })).toHaveValue(50);
    expect(await screen.findByLabelText("Bulls", {}, { timeout: 4000 })).toHaveValue(2);
    expect(
      await screen.findByLabelText(
        "Foundation Milking Buffalo Age Min Months",
        {},
        { timeout: 4000 },
      ),
    ).toHaveValue(18);
    expect(screen.getByText("Calf Mortality")).toBeInTheDocument();
    expect(screen.queryByText("Kid Mortality")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Does")).not.toBeInTheDocument();

    // The help dialog explains the term with the farm's own species.
    await user.click(
      await screen.findByRole(
        "button",
        { name: /Explain Milking Buffalo\?/ },
        { timeout: 4000 },
      ),
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(/Adult breeding milking buffalo/i),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage disposal and revenue columns", () => {
  it("shows cull disposals and milk revenue in the monthly projection", async () => {
    const user = await renderLoaded({ run: true });
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByText("Source: Current editor assumptions"),
    ).toBeInTheDocument();

    const projection = screen
      .getByText("Herd and cash-flow detail over 1 months.")
      .closest("[data-slot='card']") as HTMLElement;
    for (const header of ["Cull head", "Cull revenue", "Milk revenue"]) {
      expect(
        within(projection).getByRole("columnheader", { name: header }),
      ).toBeInTheDocument();
    }
    const [, monthRow] = within(projection).getAllByRole("row");
    const monthCells = within(monthRow).getAllByRole("cell");
    expect(monthCells[7]).toHaveTextContent("1.5");
    expect(monthCells[8]).toHaveTextContent("₹75,000");
    expect(monthCells[9]).toHaveTextContent("₹1,20,000");
  });

  it("splits annual revenue into meat, cull, milk and manure", async () => {
    const user = await renderLoaded({ run: true });
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByText("Source: Current editor assumptions"),
    ).toBeInTheDocument();

    const annual = screen
      .getByText(/Revenue splits into meat/)
      .closest("[data-slot='card']") as HTMLElement;
    const headers = within(annual)
      .getAllByRole("columnheader")
      .map((header) => header.textContent);
    expect(headers).toContain("Meat ₹");
    expect(headers).toContain("Cull ₹");
    expect(headers).toContain("Milk ₹");
    expect(headers).toContain("Manure ₹");

    const [, yearRow] = within(annual).getAllByRole("row");
    const cells = within(yearRow).getAllByRole("cell");
    expect(cells[2]).toHaveTextContent("₹1,00,000"); // meat
    expect(cells[3]).toHaveTextContent("₹5,000"); // cull
    expect(cells[4]).toHaveTextContent("₹10,000"); // milk
    expect(cells[5]).toHaveTextContent("₹5,000"); // manure
  });
});
