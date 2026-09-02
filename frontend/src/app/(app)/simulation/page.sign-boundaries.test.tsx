/**
 * Simulation page: the sign boundaries every tinted figure sits on (a
 * break-even month, a Monte Carlo draw resting exactly on zero, a sensitivity
 * swing of nothing at all), the empty collections whose cards and banners must
 * stay hidden, the permission gate ahead of the page, and the disabled states
 * that stop a half-loaded or invalid editor from writing anything.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const MANAGE_PERMS = ["simulation.view", "simulation.manage"];
const CALIBRATION_PERMS = [
  ...MANAGE_PERMS,
  "animals.view",
  "breeding.view",
  "kidding.view",
  "feeding.view",
  "finance.view",
];

/** Sparse but valid full assumptions object (only present keys render). */
const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  finance: { interest_rate_annual: 0.12 },
};

const METRICS = {
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
    net_cash_flow: 0,
    cumulative_cash_flow: 0,
    cash_balance: 0,
    fodder_surplus_kg: 0,
    fodder_stock_kg_dm: 0,
    fodder_waste_kg_dm: 0,
    ...overrides,
  };
}

const RESULT = {
  months: [],
  annual_pl: [],
  metrics: METRICS,
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

/** Two years of percentile bands, as the Monte Carlo contract returns them. */
function band(base: number, step: number) {
  const at = (offset: number) =>
    Array.from({ length: 24 }, (_, index) => base + offset * step + index * step);
  return { p5: at(0), p25: at(1), p50: at(2), p75: at(3), p95: at(4) };
}

/** Monte Carlo payload whose every sign-carrying figure sits on zero; each
 * test moves only the figures whose tint it is actually about. */
function monteCarlo(overrides: Record<string, unknown> = {}) {
  return {
    runs: 400,
    seed: 7,
    herd_percentiles: band(40, 1),
    cash_percentiles: band(-10000, 1000),
    liquidity_percentiles: band(-10000, 1000),
    npv_mean: 0,
    npv_std: 50000,
    npv_p5: -10000,
    npv_p50: 175000,
    npv_p95: 260000,
    prob_npv_negative: 0,
    prob_liquidity_shortfall: 0,
    prob_dscr_below_one: 0,
    minimum_cash_p5: 0,
    minimum_cash_p50: 0,
    ending_cash_p5: 20000,
    ending_cash_p50: 90000,
    mean_disease_outbreaks: 0,
    mean_drought_events: 0,
    mean_market_crashes: 0,
    npv_histogram_counts: Array.from({ length: 20 }, (_, index) =>
      index === 10 ? 400 : 0,
    ),
    npv_histogram_edges: Array.from(
      { length: 21 },
      (_, index) => -100000 + index * 25000,
    ),
    ...overrides,
  };
}

function scenarioRow(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    farm_id: 1,
    name: `Plan ${id}`,
    notes: "",
    assumptions: DEFAULTS,
    revision: 1,
    valid: true,
    validation_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

function scenarioRows(count: number) {
  return Array.from({ length: count }, (_, index) => scenarioRow(index + 1));
}

/** Breeds + defaults + a scenario list; the run POST answers with `runResult`. */
function registerApiHandlers(
  options: {
    scenarios?: ReturnType<typeof scenarioRows>;
    runResult?: unknown;
    breeds?: unknown;
    permissions?: string[];
    onDefaults?: (params: URLSearchParams) => void;
    defaults?: (params: URLSearchParams) => unknown;
  } = {},
) {
  server.use(
    permissionsHandler(options.permissions ?? MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json(
        options.breeds ?? { breeds: ["osmanabadi"], systems: ["stall_fed"] },
      ),
    ),
    http.get("/api/simulation/defaults", ({ request }) => {
      const params = new URL(request.url).searchParams;
      options.onDefaults?.(params);
      return HttpResponse.json(options.defaults?.(params) ?? DEFAULTS);
    }),
    http.get("/api/simulation/scenarios", ({ request }) => {
      const scenarios = options.scenarios ?? [];
      const params = new URL(request.url).searchParams;
      const limit = Number(params.get("limit") ?? 20);
      const offset = Number(params.get("offset") ?? 0);
      return HttpResponse.json({
        items: scenarios.slice(offset, offset + limit),
        total: scenarios.length,
        limit,
        offset,
      });
    }),
    http.post("/api/simulation/run", () =>
      HttpResponse.json(options.runResult ?? RESULT),
    ),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(options?: Parameters<typeof registerApiHandlers>[0]) {
  registerApiHandlers(options);
  const user = userEvent.setup();
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  return user;
}

/** Run the editor assumptions and wait for the results section. */
async function runAdHoc(options?: Parameters<typeof registerApiHandlers>[0]) {
  const user = await renderLoaded(options);
  await user.click(screen.getByRole("button", { name: "Run simulation" }));
  expect(
    await screen.findByText("Source: Current editor assumptions"),
  ).toBeInTheDocument();
  return user;
}

function cardOf(title: string): HTMLElement {
  return screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
}

function metricCard(label: string): HTMLElement {
  return screen.getByText(label).closest("[data-slot='card']") as HTMLElement;
}

/** The StatCard icon chip, whose background carries the metric's tint. */
function metricTint(label: string): HTMLElement {
  return metricCard(label).querySelector("span") as HTMLElement;
}

function cells(row: HTMLElement): HTMLElement[] {
  return within(row).getAllByRole("cell");
}

function rowFor(name: string): HTMLElement {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

function sectionOf(label: string): HTMLDetailsElement {
  return screen.getByText(label).closest("details") as HTMLDetailsElement;
}

/** The live "Select 2–5 valid scenarios (n selected)." caption. */
function selectionCaption(): string {
  return screen.getByText(/valid scenarios \(\d+ selected\)/).textContent ?? "";
}

function deleteHandler(scenarios: ReturnType<typeof scenarioRows>) {
  return http.delete("/api/simulation/scenarios/:scenarioId", ({ params }) => {
    const index = scenarios.findIndex(
      (scenario) => scenario.id === Number(params.scenarioId),
    );
    if (index >= 0) scenarios.splice(index, 1);
    return new HttpResponse(null, { status: 204 });
  });
}

describe("SimulationPage monthly projection sign boundaries", () => {
  const BOUNDARY_RESULT = {
    ...RESULT,
    months: [
      // A month that exactly breaks even, and reports an event list that
      // happens to be empty.
      monthRow({ month: 1, events: [] }),
      monthRow({
        month: 2,
        calendar_month: 2,
        net_cash_flow: 9000,
        cash_balance: 9000,
        cumulative_cash_flow: 9000,
      }),
      monthRow({
        month: 3,
        calendar_month: 3,
        net_cash_flow: -4000,
        cash_balance: -1500,
        cumulative_cash_flow: -2000,
        events: ["Drought: green fodder yield down 40%"],
      }),
    ],
  };

  // Zero is not a loss. Painting a break-even month destructive tells the
  // farmer they are burning cash in a month that costs them nothing.
  it("leaves a break-even month plain and flags only the months truly below zero", async () => {
    await runAdHoc({ runResult: BOUNDARY_RESULT });

    const projection = cardOf("Monthly projection");
    expect(
      within(projection).getByText("Herd and cash-flow detail over 3 months."),
    ).toBeInTheDocument();
    const [, breakEven, surplus, deficit] = within(projection).getAllByRole("row");

    // Net cash flow, cash balance and cumulative cash flow, in that order.
    for (const index of [16, 17, 18]) {
      expect(cells(breakEven)[index]).toHaveTextContent("₹0");
      expect(cells(breakEven)[index]).not.toHaveClass("text-destructive");
      expect(cells(surplus)[index]).not.toHaveClass("text-destructive");
      expect(cells(deficit)[index]).toHaveClass("text-destructive");
    }
    expect(cells(surplus)[16]).toHaveTextContent("₹9,000");
    expect(cells(surplus)[17]).toHaveTextContent("₹9,000");
    expect(cells(surplus)[18]).toHaveTextContent("₹9,000");
    expect(cells(deficit)[16]).toHaveTextContent("-₹4,000");
    expect(cells(deficit)[17]).toHaveTextContent("-₹1,500");
    expect(cells(deficit)[18]).toHaveTextContent("-₹2,000");
  });

  // An empty event list is not an event: a month that reports `[]` has to read
  // exactly like a month that reports nothing at all.
  it("reads an empty event list as a quiet month", async () => {
    await runAdHoc({ runResult: BOUNDARY_RESULT });

    const [, breakEven, surplus, deficit] = within(
      cardOf("Monthly projection"),
    ).getAllByRole("row");

    expect(cells(breakEven)[20]).toHaveTextContent("—");
    expect(breakEven).not.toHaveClass("bg-warning-tint/50");
    expect(cells(surplus)[20]).toHaveTextContent("—");
    expect(surplus).not.toHaveClass("bg-warning-tint/50");
    expect(cells(deficit)[20]).toHaveTextContent(
      "Drought: green fodder yield down 40%",
    );
    expect(deficit).toHaveClass("bg-warning-tint/50");
  });
});

describe("SimulationPage Monte Carlo sign boundaries", () => {
  async function runMonteCarlo(overrides: Record<string, unknown> = {}) {
    const user = await renderLoaded({
      runResult: { ...RESULT, monte_carlo: monteCarlo(overrides) },
    });
    await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("NPV mean")).toBeInTheDocument();
  }

  // Every tint here is a sign test, and a distribution that lands exactly on
  // zero is on the safe side of all of them: a break-even mean is not a loss,
  // and a zero probability is not a risk.
  it("keeps a distribution resting on zero green on every card", async () => {
    await runMonteCarlo();

    expect(screen.getByText("Monte Carlo (400 runs, seed 7)")).toBeInTheDocument();
    for (const label of [
      "NPV mean",
      "P(NPV < 0)",
      "P(cash shortfall)",
      "P(DSCR < 1)",
      "Minimum cash P5",
      "Minimum cash P50",
    ])
      expect(metricTint(label)).toHaveClass("bg-success-tint");
    // The spread cards carry no sign of their own, so they stay neutral even
    // where the figure is negative (P5 is -₹10,000 here).
    expect(metricTint("NPV std")).toHaveClass("bg-muted");
    expect(metricTint("P5")).toHaveClass("bg-muted");
    expect(within(metricCard("P5")).getByText("-₹10,000")).toBeInTheDocument();

    expect(within(metricCard("NPV mean")).getAllByText("₹0")[0]).toBeInTheDocument();
    expect(within(metricCard("Minimum cash P5")).getAllByText("₹0")[0]).toBeInTheDocument();
    expect(within(metricCard("Minimum cash P50")).getAllByText("₹0")[0]).toBeInTheDocument();
    for (const label of ["P(NPV < 0)", "P(cash shortfall)", "P(DSCR < 1)"])
      expect(within(metricCard(label)).getByText("0.0%")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Mean path events: 0.00 disease, 0.00 drought, and 0.00 market crash.",
      ),
    ).toBeInTheDocument();
  });

  // The other side of every boundary: a mean that misses break-even by a
  // single rupee is a loss, and a risk of well under one percent is still a
  // risk the farmer is being asked to carry.
  it("tints every card that has slipped past zero, down to a sub-percent tail", async () => {
    await runMonteCarlo({
      npv_mean: -1,
      prob_npv_negative: 0.004,
      prob_liquidity_shortfall: 0.125,
      prob_dscr_below_one: 1,
      minimum_cash_p5: -25,
      minimum_cash_p50: -2500,
    });

    for (const label of [
      "NPV mean",
      "P(NPV < 0)",
      "P(cash shortfall)",
      "P(DSCR < 1)",
      "Minimum cash P5",
      "Minimum cash P50",
    ])
      expect(metricTint(label)).toHaveClass("bg-destructive/10");
    // The spread cards still take no colour from any of it.
    expect(metricTint("NPV std")).toHaveClass("bg-muted");

    expect(within(metricCard("NPV mean")).getByText("-₹1")).toBeInTheDocument();
    // The probability is a fraction of one, shown as a percentage.
    expect(within(metricCard("P(NPV < 0)")).getByText("0.4%")).toBeInTheDocument();
    expect(
      within(metricCard("P(cash shortfall)")).getByText("12.5%"),
    ).toBeInTheDocument();
    expect(within(metricCard("P(DSCR < 1)")).getByText("100.0%")).toBeInTheDocument();
    expect(within(metricCard("Minimum cash P5")).getByText("-₹25")).toBeInTheDocument();
    expect(
      within(metricCard("Minimum cash P50")).getByText("-₹2,500"),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage sensitivity sign boundaries", () => {
  async function runSensitivity(sensitivity: unknown) {
    const user = await renderLoaded({ runResult: { ...RESULT, sensitivity } });
    await user.click(screen.getByRole("checkbox", { name: "Sensitivity" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByText("Source: Current editor assumptions"),
    ).toBeInTheDocument();
  }

  // A swing of exactly nothing costs nothing: only a strictly negative ΔNPV
  // is a loss, on whichever side of the swing it falls.
  it("flags only strictly negative swings and leaves a nil swing plain", async () => {
    await runSensitivity([
      { parameter: "mortality_rate", delta_npv_low: 40000, delta_npv_high: -40000 },
      { parameter: "labour_cost", delta_npv_low: 0, delta_npv_high: 0 },
      { parameter: "meat_price_per_kg", delta_npv_low: -120000, delta_npv_high: 150000 },
    ]);

    const table = cardOf("Sensitivity (ΔNPV)");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Meat Price Per Kg",
      "Mortality Rate",
      "Labour Cost",
    ]);

    expect(cells(rows[0])[1]).toHaveTextContent("-₹1,20,000");
    expect(cells(rows[0])[1]).toHaveClass("text-destructive");
    expect(cells(rows[0])[2]).toHaveTextContent("₹1,50,000");
    expect(cells(rows[0])[2]).not.toHaveClass("text-destructive");

    expect(cells(rows[1])[1]).toHaveTextContent("₹40,000");
    expect(cells(rows[1])[1]).not.toHaveClass("text-destructive");
    expect(cells(rows[1])[2]).toHaveTextContent("-₹40,000");
    expect(cells(rows[1])[2]).toHaveClass("text-destructive");

    for (const index of [1, 2]) {
      expect(cells(rows[2])[index]).toHaveTextContent("₹0");
      expect(cells(rows[2])[index]).not.toHaveClass("text-destructive");
    }
  });

  // A sensitivity run that ranked nothing has nothing to say. An empty card
  // reads as "no parameter matters", which is the opposite of the truth.
  it("hides the sensitivity card when the run ranked no parameters", async () => {
    await runSensitivity([]);

    expect(screen.queryByText("Sensitivity (ΔNPV)")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "ΔNPV low" }),
    ).not.toBeInTheDocument();
    // The rest of the result is still there — only the empty card is gone.
    expect(screen.getAllByText("₹2,34,567")[0]).toBeInTheDocument();
  });
});

describe("SimulationPage permission gate", () => {
  // Permissions that have not arrived yet are not permissions that were
  // refused: announcing "no access" while the lookup is still in flight makes
  // every first paint look like a lockout.
  it("waits on the permissions lookup instead of claiming access is denied", async () => {
    let releasePerms!: () => void;
    registerApiHandlers();
    server.use(
      http.get(
        "/api/auth/permissions",
        () =>
          new Promise<Response>((resolve) => {
            releasePerms = () =>
              resolve(
                HttpResponse.json({ is_owner: false, permissions: MANAGE_PERMS }),
              );
          }),
      ),
    );
    renderWithProviders(<SimulationPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Simulation" })).toBeInTheDocument();
    expect(screen.queryByText("Horizon Months")).not.toBeInTheDocument();

    // The skeleton can paint before the (farm-gated) permissions request
    // leaves; release only once it is actually in flight.
    await waitFor(() => expect(releasePerms).toBeTypeOf("function"));
    releasePerms();
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Simulation" }),
    ).toBeInTheDocument();
  });

  // A failed lookup is not a denial either: it is a transient fault the
  // farmer can retry, and saying so is the difference between "come back" and
  // "you were removed from this farm".
  it("names a failed permissions lookup as a fault to retry", async () => {
    registerApiHandlers();
    server.use(http.get("/api/auth/permissions", () => HttpResponse.error()));
    renderWithProviders(<SimulationPage />);

    expect(
      await screen.findByText(
        "Could not load your permissions — refresh the page to try again.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });
});

describe("SimulationPage comparison gate bounds", () => {
  // A comparison of one is not a comparison. The gate opens on the second
  // usable selection and shuts again the moment it drops back to one.
  it("stays shut below two usable selections", async () => {
    const scenarios = scenarioRows(3);
    const user = await renderLoaded({ scenarios });
    expect(await screen.findByText("Plan 3")).toBeInTheDocument();

    const compare = screen.getByRole("button", { name: "Compare selected" });
    expect(selectionCaption()).toContain("(0 selected)");
    expect(compare).toBeDisabled();

    await user.click(screen.getByLabelText("Compare Plan 1"));
    expect(selectionCaption()).toContain("(1 selected)");
    expect(compare).toBeDisabled();

    await user.click(screen.getByLabelText("Compare Plan 2"));
    expect(selectionCaption()).toBe(
      "Saved assumption sets to load, run, update or compare. Select 2–5 valid scenarios (2 selected).",
    );
    expect(compare).toBeEnabled();

    await user.click(screen.getByLabelText("Compare Plan 1"));
    expect(selectionCaption()).toContain("(1 selected)");
    expect(compare).toBeDisabled();
  });

  // The cap is enforced on what the selection currently holds, not on how it
  // was assembled. A row that stopped validating frees a slot; when a later
  // refetch validates it again the selection is over the cap, and the gate has
  // to shut rather than send six ids the comparison endpoint would refuse.
  it("shuts again when a re-validated row pushes the selection past five", async () => {
    const scenarios = scenarioRows(8);
    const user = await renderLoaded({ scenarios });
    server.use(deleteHandler(scenarios));
    expect(await screen.findByText("Plan 8")).toBeInTheDocument();

    for (const id of [1, 2, 3, 4, 5])
      await user.click(screen.getByLabelText(`Compare Plan ${id}`));
    expect(selectionCaption()).toContain("(5 selected)");
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeEnabled();
    expect(screen.getByLabelText("Compare Plan 6")).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    // Plan 1's stored assumptions stop validating; the delete's refetch drops
    // it out of the usable selection and frees a slot.
    scenarios[0] = scenarioRow(1, {
      valid: false,
      validation_error: "horizon_months must be positive",
    });
    await user.click(within(rowFor("Plan 8")).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));
    await waitFor(() =>
      expect(screen.queryByText("Plan 8")).not.toBeInTheDocument(),
    );
    expect(selectionCaption()).toContain("(4 selected)");
    expect(screen.getByText("Invalid saved assumptions")).toBeInTheDocument();
    expect(screen.getByLabelText("Compare Plan 1")).not.toBeChecked();

    await user.click(screen.getByLabelText("Compare Plan 6"));
    expect(selectionCaption()).toContain("(5 selected)");
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeEnabled();

    // Plan 1 validates again: six usable rows are now selected, one more than
    // a comparison may carry.
    scenarios[0] = scenarioRow(1);
    await user.click(within(rowFor("Plan 7")).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));
    await waitFor(() =>
      expect(screen.queryByText("Plan 7")).not.toBeInTheDocument(),
    );
    expect(selectionCaption()).toContain("(6 selected)");
    expect(screen.getByLabelText("Compare Plan 1")).toBeChecked();
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeDisabled();
  });
});

describe("SimulationPage write gates", () => {
  // One unusable number invalidates the whole payload, so every action that
  // would send it — the run, the new scenario and the update — has to be shut,
  // not just the one the operator happens to click first.
  it("shuts run, save and update while a numeric field is unusable", async () => {
    const user = await renderLoaded({
      scenarios: [scenarioRow(3, { name: "Base plan" })],
    });
    await user.click(await screen.findByRole("button", { name: "Load" }));
    expect(screen.getByRole("button", { name: "Update Base plan" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();

    await user.clear(screen.getByLabelText("Does"));

    expect(
      screen.getByText("Fix 1 highlighted numeric field before running or saving."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Update Base plan" })).toBeDisabled();
  });

  // Runs, comparisons and scenario writes all read or move the same saved
  // revision, so the single flight has to be visible: nothing else may be
  // started while a run is on the wire.
  it("holds every other action shut while a run is on the wire", async () => {
    let releaseRun!: () => void;
    const scenarios = scenarioRows(2);
    registerApiHandlers({ scenarios });
    server.use(
      http.post(
        "/api/simulation/run",
        () =>
          new Promise<Response>((resolve) => {
            releaseRun = () => resolve(HttpResponse.json(RESULT));
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(within(rowFor("Plan 1")).getByRole("button", { name: "Load" }));
    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(releaseRun).toBeTypeOf("function"));

    const running = screen.getAllByRole("button", { name: "Running…" });
    expect(running.length).toBeGreaterThan(0);
    for (const button of running) expect(button).toBeDisabled();
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Update Plan 1" })).toBeDisabled();

    releaseRun();
    expect(
      await screen.findByText("Source: Current editor assumptions"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeEnabled();
  });
});

describe("SimulationPage defaults reload", () => {
  // The dropdown is an intent, not a request: "Load defaults" has to submit
  // whatever the dropdowns currently say. Refetching the parameters already on
  // file silently returned the old system's economics under the new label.
  it("re-requests the defaults under a freshly picked system", async () => {
    const requested: URLSearchParams[] = [];
    const user = await renderLoaded({
      breeds: { breeds: ["osmanabadi"], systems: ["stall_fed", "semi_intensive"] },
      onDefaults: (params) => requested.push(params),
      defaults: (params) =>
        params.get("system") === "semi_intensive"
          ? { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 34 } }
          : DEFAULTS,
    });

    const system = screen.getByLabelText("System");
    expect(system).toHaveTextContent("Stall Fed");
    const beforePick = requested.length;
    await user.click(system);
    await user.click(await screen.findByRole("option", { name: "Semi Intensive" }));
    await waitFor(() => expect(system).toHaveTextContent("Semi Intensive"));
    // Picking a system changes nothing on its own.
    expect(requested).toHaveLength(beforePick);
    expect(screen.getByLabelText("Does")).toHaveValue(50);

    await user.click(screen.getByRole("button", { name: "Load defaults" }));

    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(34));
    const last = requested[requested.length - 1];
    expect(last.get("system")).toBe("semi_intensive");
    expect(last.get("breed")).toBe("osmanabadi");
  });
});

describe("SimulationPage defaults loading state", () => {
  // Nothing that reads or replaces the assumptions may be offered before
  // there are assumptions — and once a reload is in flight the herd import
  // must wait too, or it merges head counts into economics that are about to
  // be thrown away.
  it("keeps the editor-dependent actions shut until the defaults arrive", async () => {
    const release: Array<() => void> = [];
    registerApiHandlers({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get(
        "/api/simulation/defaults",
        () =>
          new Promise<Response>((resolve) => {
            release.push(() => resolve(HttpResponse.json(DEFAULTS)));
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);

    expect(await screen.findByText("Loading defaults…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use current herd" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Calibrate from farm" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeDisabled();

    release[0]();
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
    expect(screen.queryByText("Loading defaults…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use current herd" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Calibrate from farm" })).toBeEnabled();
    // A healthy pair of lookups leaves no failure notice behind.
    expect(screen.queryByText("Could not load the defaults.")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Could not load available breeds and systems."),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await waitFor(() => expect(release).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Use current herd" })).toBeDisabled();
    // The editor is populated, so the second load is not a first paint.
    expect(screen.queryByText("Loading defaults…")).not.toBeInTheDocument();

    release[1]();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Use current herd" })).toBeEnabled(),
    );
  });

  // The herd import replaces the whole herd section, so a second click while
  // the first snapshot is still on the wire would apply the same head counts
  // twice over an editor that has already moved. Calibration and a defaults
  // reload read nothing from the snapshot, so neither of them is held back.
  it("shuts the herd import against a second click while its snapshot is on the wire", async () => {
    let releaseSnapshot!: () => void;
    const user = await renderLoaded({ permissions: CALIBRATION_PERMS });
    toastMocks.success.mockClear();
    server.use(
      http.get(
        "/api/simulation/herd-snapshot",
        () =>
          new Promise<Response>((resolve) => {
            releaseSnapshot = () =>
              resolve(
                HttpResponse.json({
                  does: 48,
                  bucks: 3,
                  f_kids: 4,
                  f_weaners: 5,
                  f_growers: 6,
                  m_kids: 3,
                  m_weaners: 2,
                  m_growers: 1,
                  total_head: 72,
                }),
              );
          }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(releaseSnapshot).toBeTypeOf("function"));

    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Use current herd" })).not.toBeInTheDocument();
    // Neither of these reads the snapshot, so neither waits on it.
    expect(screen.getByRole("button", { name: "Load defaults" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Calibrate from farm" })).toBeEnabled();

    releaseSnapshot();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));
    expect(screen.getByLabelText("Bucks")).toHaveValue(3);
    expect(screen.getByRole("button", { name: "Use current herd" })).toBeEnabled();
    expect(toastMocks.success).toHaveBeenCalledWith("Loaded current herd (72 head).");
  });

  // A failed lookup is a finished lookup. Leaving "Loading defaults…" up next
  // to the failure notice tells the farmer to keep waiting for a payload that
  // is never coming.
  it("stops claiming the defaults are loading once the lookup has failed", async () => {
    server.use(
      permissionsHandler(MANAGE_PERMS),
      http.get("/api/simulation/defaults/breeds", () => HttpResponse.error()),
      http.get("/api/simulation/defaults", () => HttpResponse.error()),
      http.get("/api/simulation/scenarios", () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
    );
    renderWithProviders(<SimulationPage />);

    expect(
      await screen.findByText("Could not load the defaults."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Could not load available breeds and systems."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Loading defaults…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeDisabled();
  });
});

describe("SimulationPage calibration evidence edges", () => {
  // A calibration that cleared no threshold still has to say what it did, and
  // an empty warning list is not a warning: an empty amber banner reads as an
  // unnamed problem with the farm's records.
  it("reports an empty calibration without an empty warning banner", async () => {
    const user = await renderLoaded({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get("/api/simulation/calibration", () =>
        HttpResponse.json({
          assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 41 } },
          evidence: [],
          warnings: [],
          coverage_score: 0,
          reference_date: "2026-08-24",
          lookback_months: 24,
        }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    expect(await screen.findByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "No farm observations met the evidence thresholds; breed-system defaults remain in use.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Records through 2026-08-24, using a 24-month lookback."),
    ).toBeInTheDocument();
    expect(screen.getByText("0.0% coverage")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Assumption" }),
    ).not.toBeInTheDocument();

    // The calibrated payload still rebuilds the editor on its headline
    // sections, exactly as a defaults or scenario load does.
    expect(screen.getByLabelText("Does")).toHaveValue(41);
    expect(sectionOf("Meta")).toHaveAttribute("open");
    expect(sectionOf("Herd")).toHaveAttribute("open");
    expect(sectionOf("Finance")).not.toHaveAttribute("open");
  });
});
