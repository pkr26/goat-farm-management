/**
 * Simulation page: result-table styling and copy contracts (negative cash
 * flagged, non-negative figures left plain), the Monte Carlo tints, histogram
 * and uncertainty bands, the sensitivity ranking, the optimization candidate
 * table, the in-flight button labels, the fallback copy when a lookup fails,
 * and the assumptions editor's section/field identity contracts.
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
    ...overrides,
  };
}

function annualRow(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

const RESULT = {
  months: [monthRow(), monthRow({ month: 2, calendar_month: 2, births: 4 })],
  annual_pl: [annualRow()],
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
};

function band(base: number, step: number) {
  return {
    p5: Array.from({ length: 24 }, (_, index) => base + index * step),
    p25: Array.from({ length: 24 }, (_, index) => base + step + index * step),
    p50: Array.from({ length: 24 }, (_, index) => base + 2 * step + index * step),
    p75: Array.from({ length: 24 }, (_, index) => base + 3 * step + index * step),
    p95: Array.from({ length: 24 }, (_, index) => base + 4 * step + index * step),
  };
}

/** 20-bin NPV histogram, as the backend contract fixes it. */
const HISTOGRAM_COUNTS = Array.from({ length: 20 }, (_, index) =>
  index === 1 ? 1 : index === 19 ? 40 : 0,
);
const HISTOGRAM_EDGES = Array.from(
  { length: 21 },
  (_, index) => -100000 + index * 25000,
);

function monteCarlo(overrides: Record<string, unknown> = {}) {
  return {
    runs: 500,
    seed: 42,
    herd_percentiles: band(40, 1),
    cash_percentiles: band(-10000, 1000),
    liquidity_percentiles: band(-10000, 1000),
    npv_mean: 180000,
    npv_std: 50000,
    npv_p5: -10000,
    npv_p50: 175000,
    npv_p95: 260000,
    prob_npv_negative: 0,
    prob_liquidity_shortfall: 0,
    prob_dscr_below_one: 0,
    minimum_cash_p5: 5000,
    minimum_cash_p50: 12000,
    ending_cash_p5: 20000,
    ending_cash_p50: 90000,
    mean_disease_outbreaks: 0.4,
    mean_drought_events: 0.25,
    mean_market_crashes: 0.1,
    npv_histogram_counts: HISTOGRAM_COUNTS,
    npv_histogram_edges: HISTOGRAM_EDGES,
    ...overrides,
  };
}

function candidate(overrides: Record<string, unknown> = {}) {
  return {
    rank: 0,
    starting_does: 50,
    starting_bucks: 2,
    max_breeding_does: 100,
    sale_age_months: 18,
    female_retention_fraction: 0.5,
    loan_fraction: 0.5,
    project_cost: 500000,
    capacity_places: 58,
    projected_peak_head: 70,
    npv: 234567,
    irr: 0.18,
    min_dscr: 1.8,
    minimum_cash_balance: 25000,
    funding_gap: 0,
    feasible: true,
    constraint_violations: [],
    ...overrides,
  };
}

function scenario(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    farm_id: 1,
    name: "Plan A",
    notes: "",
    assumptions: DEFAULTS,
    revision: 3,
    valid: true,
    validation_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

/** Breeds + defaults + scenario list; the run POST returns `runResult`. */
function registerApiHandlers(
  options: {
    scenarios?: unknown[];
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
async function renderLoaded(
  options?: Parameters<typeof registerApiHandlers>[0],
) {
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

/** Optimization candidate rows; the card title is an icon+text span. */
function candidateRows(): HTMLElement[] {
  const table = screen
    .getByRole("columnheader", { name: "Decision set" })
    .closest("table") as HTMLElement;
  return within(table).getAllByRole("row").slice(1);
}

describe("SimulationPage cash-flow tables", () => {
  const CASH_RESULT = {
    ...RESULT,
    annual_pl: [
      annualRow({ year: 1, net_cash_flow: -30000 }),
      annualRow({ year: 2, total_revenue: 160000, net_cash_flow: 40000 }),
    ],
    months: [
      monthRow({
        month: 1,
        net_cash_flow: -7000,
        cash_balance: -5000,
        cumulative_cash_flow: -7000,
        events: ["Purchased 10 doe(s) at ₹8,000/head (₹80,000)"],
      }),
      monthRow({
        month: 2,
        calendar_month: 2,
        sales_revenue: 60000,
        net_cash_flow: 12000,
        cash_balance: 43000,
        cumulative_cash_flow: 5000,
      }),
    ],
  };

  it("marks negative annual and monthly cash figures and leaves the rest plain", async () => {
    await runAdHoc({ runResult: CASH_RESULT });

    const [, lossYear, profitYear] = within(cardOf("Annual P&L")).getAllByRole("row");
    const lossCells = cells(lossYear);
    expect(lossCells[11]).toHaveTextContent("-₹30,000");
    expect(lossCells[11]).toHaveClass("text-right", "tabular-nums", "text-destructive");
    const profitCells = cells(profitYear);
    expect(profitCells[11]).toHaveTextContent("₹40,000");
    // A non-negative figure carries no styling beyond the neutral money cells.
    expect(profitCells[11].className).toBe(profitCells[1].className);

    const projection = cardOf("Monthly projection");
    const [, deficitMonth, surplusMonth] = within(projection).getAllByRole("row");
    const deficitCells = cells(deficitMonth);
    expect(deficitCells[13]).toHaveTextContent("-₹7,000");
    expect(deficitCells[14]).toHaveTextContent("-₹5,000");
    expect(deficitCells[15]).toHaveTextContent("-₹7,000");
    for (const index of [13, 14, 15])
      expect(deficitCells[index]).toHaveClass(
        "text-right",
        "tabular-nums",
        "text-destructive",
      );
    const surplusCells = cells(surplusMonth);
    expect(surplusCells[13]).toHaveTextContent("₹12,000");
    expect(surplusCells[14]).toHaveTextContent("₹43,000");
    expect(surplusCells[15]).toHaveTextContent("₹5,000");
    for (const index of [13, 14, 15])
      expect(surplusCells[index].className).toBe(surplusCells[6].className);
  });

  it("counts the projected months and separates event months from quiet ones", async () => {
    await runAdHoc({ runResult: CASH_RESULT });

    const projection = cardOf("Monthly projection");
    expect(
      within(projection).getByText("Herd and cash-flow detail over 2 months."),
    ).toBeInTheDocument();

    const [, deficitMonth, surplusMonth] = within(projection).getAllByRole("row");
    expect(deficitMonth).toHaveClass("bg-amber-50");
    expect(cells(deficitMonth)[17]).toHaveTextContent(
      "Purchased 10 doe(s) at ₹8,000/head (₹80,000)",
    );
    expect(surplusMonth).not.toHaveClass("bg-amber-50");
    expect(cells(surplusMonth)[17]).toHaveTextContent("—");
  });
});

describe("SimulationPage Monte Carlo presentation", () => {
  it("tints a healthy distribution green and charts its NPV spread", async () => {
    const user = await renderLoaded({
      runResult: { ...RESULT, monte_carlo: monteCarlo() },
    });
    await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("NPV mean")).toBeInTheDocument();

    for (const label of [
      "NPV mean",
      "P(NPV < 0)",
      "P(cash shortfall)",
      "P(DSCR < 1)",
      "Minimum cash P5",
      "Minimum cash P50",
    ])
      expect(metricTint(label)).toHaveClass("bg-emerald-100");
    expect(within(metricCard("P(NPV < 0)")).getByText("0.0%")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Mean path events: 0.40 disease, 0.25 drought, and 0.10 market crash.",
      ),
    ).toBeInTheDocument();

    // Bar heights are proportional to the busiest bin, with a visible floor
    // for any bin that holds runs at all.
    const bars = Array.from(screen.getByLabelText("NPV histogram").children);
    expect(bars).toHaveLength(20);
    expect(bars[0]).toHaveStyle({ height: "0%" });
    expect(bars[1]).toHaveStyle({ height: "4%" });
    expect(bars[19]).toHaveStyle({ height: "100%" });
    expect(bars[19]).toHaveAttribute(
      "title",
      "₹3,75,000 – ₹4,00,000: 40 runs",
    );

    // Annual checkpoints: first month, each anniversary, and the final month.
    const bands = screen.getByText("Annual uncertainty checkpoints")
      .parentElement as HTMLElement;
    const rows = within(bands).getAllByRole("row").slice(1);
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual(["1", "12", "24"]);
    expect(cells(rows[1]).map((cell) => cell.textContent)).toEqual([
      "12",
      "51.0",
      "53.0",
      "55.0",
      "₹1,000",
      "₹3,000",
      "₹5,000",
    ]);
  });

  it("tints a loss-making distribution red and prints its risk probabilities", async () => {
    const user = await renderLoaded({
      runResult: {
        ...RESULT,
        monte_carlo: monteCarlo({
          npv_mean: -50000,
          prob_npv_negative: 0.08,
          prob_liquidity_shortfall: 0.22,
          prob_dscr_below_one: 0.12,
          minimum_cash_p5: -30000,
          minimum_cash_p50: -1000,
        }),
      },
    });
    await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("NPV mean")).toBeInTheDocument();

    for (const label of [
      "NPV mean",
      "P(NPV < 0)",
      "P(cash shortfall)",
      "P(DSCR < 1)",
      "Minimum cash P5",
      "Minimum cash P50",
    ])
      expect(metricTint(label)).toHaveClass("bg-red-100");
    expect(within(metricCard("NPV mean")).getByText("-₹50,000")).toBeInTheDocument();
    expect(within(metricCard("P(NPV < 0)")).getByText("8.0%")).toBeInTheDocument();
    expect(
      within(metricCard("P(cash shortfall)")).getByText("22.0%"),
    ).toBeInTheDocument();
    expect(within(metricCard("P(DSCR < 1)")).getByText("12.0%")).toBeInTheDocument();
  });
});

describe("SimulationPage sensitivity table", () => {
  it("ranks parameters by swing and flags every NPV loss", async () => {
    await runAdHoc({
      runResult: {
        ...RESULT,
        sensitivity: [
          {
            parameter: "conception_rate",
            delta_npv_low: -40000,
            delta_npv_high: 35000,
            label_low: "-10%",
            label_high: "+10%",
          },
          {
            parameter: "meat_price_per_kg",
            delta_npv_low: -120000,
            delta_npv_high: 150000,
            label_low: "-10%",
            label_high: "+10%",
          },
          {
            parameter: "feed_price_per_kg",
            delta_npv_low: -90000,
            delta_npv_high: -20000,
            label_low: "-10%",
            label_high: "+10%",
          },
        ],
      },
    });

    const table = cardOf("Sensitivity (ΔNPV)");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Meat Price Per Kg",
      "Feed Price Per Kg",
      "Conception Rate",
    ]);
    // Losses are destructive on whichever side of the swing they fall.
    expect(cells(rows[0])[1]).toHaveTextContent("-₹1,20,000");
    expect(cells(rows[0])[1]).toHaveClass("text-destructive");
    expect(cells(rows[0])[2]).toHaveTextContent("₹1,50,000");
    expect(cells(rows[0])[2]).not.toHaveClass("text-destructive");
    expect(cells(rows[1])[2]).toHaveTextContent("-₹20,000");
    expect(cells(rows[1])[2]).toHaveClass("text-destructive");
  });
});

describe("SimulationPage optimization results", () => {
  const BASELINE = candidate({ rank: 0 });
  const RECOMMENDED = candidate({
    rank: 1,
    starting_does: 60,
    starting_bucks: 3,
    npv: 310000,
  });

  function optimizationResult(overrides: Record<string, unknown> = {}) {
    return {
      ...RESULT,
      optimization: {
        objective: "balanced",
        evaluated_candidates: 24,
        feasible_candidates: 3,
        baseline: BASELINE,
        recommended: RECOMMENDED,
        alternatives: [],
        ...overrides,
      },
    };
  }

  async function runOptimization(overrides: Record<string, unknown> = {}) {
    const user = await renderLoaded({ runResult: optimizationResult(overrides) });
    await user.click(screen.getByRole("checkbox", { name: "Optimization" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByRole("columnheader", { name: "Decision set" }),
    ).toBeInTheDocument();
    return user;
  }

  it("lists the baseline, the recommendation and every distinct alternative", async () => {
    await runOptimization({
      alternatives: [
        // Same digits as the baseline once the field separators are dropped
        // (100/18 vs 1001/8) — a different decision set that must still list.
        candidate({ rank: 1, max_breeding_does: 1001, sale_age_months: 8 }),
        // A genuine repeat of the recommendation, which must not list twice.
        candidate({ rank: 2, starting_does: 60, starting_bucks: 3, npv: 310000 }),
      ],
    });

    expect(
      screen.getByText(
        "3 of 24 evaluated candidates satisfy the Balanced objective constraints.",
      ),
    ).toBeInTheDocument();
    const rows = candidateRows();
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Baseline",
      "Recommended",
      "Alternative 1",
    ]);
    expect(cells(rows[1])[8]).toHaveTextContent("₹3,10,000");
    expect(cells(rows[2])[2]).toHaveTextContent("1001");
    expect(cells(rows[2])[3]).toHaveTextContent("8 mo");
  });

  it("merges the recommendation into the baseline row when they are one decision set", async () => {
    await runOptimization({ recommended: candidate({ rank: 1 }) });

    const rows = candidateRows();
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Baseline / recommended",
    ]);
  });

  it("lists no alternatives when the optimizer returned none", async () => {
    await runOptimization({ alternatives: undefined });

    const rows = candidateRows();
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Baseline",
      "Recommended",
    ]);
  });

  it("separates a failing decision set from a viable one across every column", async () => {
    await runOptimization({
      baseline: candidate({
        rank: 0,
        feasible: false,
        minimum_cash_balance: -30000,
        funding_gap: 30000,
        constraint_violations: [
          "Minimum DSCR is below 1.20",
          "Projected peak exceeds funded capacity",
        ],
      }),
      recommended: candidate({
        rank: 1,
        starting_does: 60,
        starting_bucks: 3,
        constraint_violations: undefined,
      }),
    });

    const rows = candidateRows();
    const failing = cells(rows[0]);
    const viable = cells(rows[1]);

    expect(failing[1]).toHaveTextContent("Infeasible");
    expect(failing[1]).toHaveClass("text-destructive");
    expect(viable[1]).toHaveTextContent("Feasible");
    expect(viable[1]).toHaveClass("text-emerald-700");

    expect(failing[2].textContent).toBe("50 / 2 / 100");
    expect(failing[7].textContent).toBe("58.0 / 70.0");
    expect(viable[2].textContent).toBe("60 / 3 / 100");

    expect(failing[11]).toHaveTextContent("-₹30,000");
    expect(failing[11]).toHaveClass("text-right", "tabular-nums", "text-destructive");
    expect(failing[12]).toHaveTextContent("₹30,000");
    expect(failing[12]).toHaveClass("text-right", "tabular-nums", "text-destructive");
    expect(failing[13].textContent).toBe(
      "Minimum DSCR is below 1.20; Projected peak exceeds funded capacity",
    );

    // A viable candidate is styled exactly like the neutral money columns.
    expect(viable[11].className).toBe(viable[6].className);
    expect(viable[12].className).toBe(viable[6].className);
    expect(viable[13]).toHaveTextContent("None");
  });
});

describe("SimulationPage in-flight labels", () => {
  it("labels the ad-hoc run button while the run is on the wire", async () => {
    let releaseRun!: () => void;
    registerApiHandlers();
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

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(releaseRun).toBeTypeOf("function"));
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();

    releaseRun();
    expect(
      await screen.findByRole("button", { name: "Run simulation" }),
    ).toBeEnabled();
  });

  it("labels only the scenario row that is running", async () => {
    let releaseRun!: () => void;
    registerApiHandlers({
      scenarios: [scenario({ id: 1, name: "Plan A" }), scenario({ id: 2, name: "Plan B" })],
    });
    server.use(
      http.post(
        "/api/simulation/scenarios/1/run",
        () =>
          new Promise<Response>((resolve) => {
            releaseRun = () => resolve(HttpResponse.json(RESULT));
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    const planA = screen.getByText("Plan A").closest("tr") as HTMLElement;
    const planB = screen.getByText("Plan B").closest("tr") as HTMLElement;
    await user.click(within(planA).getByRole("button", { name: "Run" }));
    await waitFor(() => expect(releaseRun).toBeTypeOf("function"));
    expect(within(planA).getByRole("button", { name: "Running…" })).toBeDisabled();
    expect(within(planB).getByRole("button", { name: "Run" })).toBeDisabled();

    releaseRun();
    expect(
      await screen.findByText("Source: Saved scenario “Plan A”"),
    ).toBeInTheDocument();
  });

  it("labels the compare button while the comparison is on the wire", async () => {
    const scenarios = [scenario({ id: 1, name: "Plan A" }), scenario({ id: 2, name: "Plan B" })];
    let releaseCompare!: () => void;
    registerApiHandlers({ scenarios });
    server.use(
      http.get(
        "/api/simulation/scenarios/compare",
        () =>
          new Promise<Response>((resolve) => {
            releaseCompare = () =>
              resolve(HttpResponse.json({ scenarios, results: [RESULT, RESULT] }));
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Compare Plan A"));
    await user.click(screen.getByLabelText("Compare Plan B"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    await waitFor(() => expect(releaseCompare).toBeTypeOf("function"));
    expect(screen.getByRole("button", { name: "Comparing…" })).toBeDisabled();

    releaseCompare();
    expect(await screen.findByText("Comparison")).toBeInTheDocument();
  });

  it("labels the update button while the scenario write is on the wire", async () => {
    const saved = scenario({ id: 4, name: "Versioned plan" });
    let releaseUpdate!: () => void;
    registerApiHandlers({ scenarios: [saved] });
    server.use(
      http.patch(
        "/api/simulation/scenarios/4",
        () =>
          new Promise<Response>((resolve) => {
            releaseUpdate = () =>
              resolve(HttpResponse.json({ ...saved, revision: 4 }));
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Versioned plan" }));
    await waitFor(() => expect(releaseUpdate).toBeTypeOf("function"));
    expect(screen.getByRole("button", { name: "Updating…" })).toBeDisabled();

    releaseUpdate();
    expect(
      await screen.findByRole("button", { name: "Update Versioned plan" }),
    ).toBeEnabled();
  });

  it("labels the defaults button until the payload lands", async () => {
    let releaseDefaults!: () => void;
    registerApiHandlers();
    server.use(
      http.get(
        "/api/simulation/defaults",
        () =>
          new Promise<Response>((resolve) => {
            releaseDefaults = () => resolve(HttpResponse.json(DEFAULTS));
          }),
      ),
    );
    renderWithProviders(<SimulationPage />);

    expect(await screen.findByRole("button", { name: "Loading…" })).toBeDisabled();
    expect(screen.getByText("Loading defaults…")).toBeInTheDocument();

    releaseDefaults();
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load defaults" })).toBeEnabled();
  });

  it("labels the herd snapshot button while the snapshot is on the wire", async () => {
    let releaseSnapshot!: () => void;
    const user = await renderLoaded();
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

    releaseSnapshot();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));
    expect(screen.getByRole("button", { name: "Use current herd" })).toBeEnabled();
  });
});

describe("SimulationPage farm calibration", () => {
  const CALIBRATION = {
    assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 73 } },
    evidence: [
      {
        path: "herd.does",
        previous_value: 50,
        calibrated_value: 73,
        sample_size: 73,
        confidence: "high",
        method: "Counted active adult female animals",
        source: "Animal registry",
        period_start: "2021-08-10",
        period_end: "2026-08-10",
      },
    ],
    warnings: [],
    coverage_score: 0.72,
    reference_date: "2026-08-10",
    lookback_months: 60,
  };

  it("offers every lookback window, labels the run and describes the evidence", async () => {
    const captured: { lookback?: string | null } = {};
    let releaseCalibration!: () => void;
    const user = await renderLoaded({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get("/api/simulation/calibration", ({ request }) => {
        captured.lookback = new URL(request.url).searchParams.get("lookback_months");
        return new Promise<Response>((resolve) => {
          releaseCalibration = () => resolve(HttpResponse.json(CALIBRATION));
        });
      }),
    );

    const lookback = screen.getByLabelText("Calibration history");
    expect(lookback).toHaveTextContent("24 months");
    await user.click(lookback);
    expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual([
      "12 months",
      "24 months",
      "36 months",
      "60 months",
    ]);
    await user.click(screen.getByRole("option", { name: "12 months" }));
    await waitFor(() => expect(lookback).toHaveTextContent("12 months"));
    for (const months of [36, 60]) {
      await user.click(lookback);
      await user.click(await screen.findByRole("option", { name: `${months} months` }));
      await waitFor(() => expect(lookback).toHaveTextContent(`${months} months`));
    }

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(releaseCalibration).toBeTypeOf("function"));
    expect(screen.getByRole("button", { name: "Calibrating…" })).toBeDisabled();

    releaseCalibration();
    expect(await screen.findByText("Animal registry")).toBeInTheDocument();
    expect(captured.lookback).toBe("60");
    expect(
      screen.getByText("Records through 2026-08-10, using a 60-month lookback."),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage lookup failures", () => {
  it("keeps the loaded breed and system selectable and names each failed lookup", async () => {
    server.use(
      permissionsHandler(MANAGE_PERMS),
      http.get("/api/simulation/defaults/breeds", () => HttpResponse.error()),
      http.get("/api/simulation/defaults", () => HttpResponse.error()),
      http.get("/api/simulation/scenarios", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);

    expect(
      await screen.findByText("Could not load available breeds and systems."),
    ).toBeInTheDocument();
    expect(screen.getByText("Could not load the defaults.")).toBeInTheDocument();
    expect(screen.getByText("Could not load saved scenarios.")).toBeInTheDocument();

    // With no breed catalogue the editor still offers what it is set to.
    const breed = screen.getByLabelText("Breed");
    expect(breed).toHaveTextContent("osmanabadi");
    await user.click(breed);
    expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual([
      "osmanabadi",
    ]);
    await user.keyboard("{Escape}");

    const system = screen.getByLabelText("System");
    expect(system).toHaveTextContent("Stall Fed");
    await user.click(system);
    expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual([
      "Stall Fed",
    ]);
  });

  it("names the comparison failure inline", async () => {
    const scenarios = [scenario({ id: 1, name: "Plan A" }), scenario({ id: 2, name: "Plan B" })];
    const user = await renderLoaded({ scenarios });
    server.use(
      http.get("/api/simulation/scenarios/compare", () => HttpResponse.error()),
    );

    await user.click(screen.getByLabelText("Compare Plan A"));
    await user.click(screen.getByLabelText("Compare Plan B"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));

    expect(
      await screen.findByText("Could not compare the selected scenarios."),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage assumptions editor", () => {
  it("requests the freshly picked breed when defaults are reloaded", async () => {
    const requested: URLSearchParams[] = [];
    const user = await renderLoaded({
      breeds: { breeds: ["osmanabadi", "sirohi"], systems: ["stall_fed"] },
      onDefaults: (params) => requested.push(params),
      defaults: (params) =>
        params.get("breed") === "sirohi"
          ? { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 80 } }
          : DEFAULTS,
    });

    await user.click(screen.getByLabelText("Breed"));
    await user.click(await screen.findByRole("option", { name: "sirohi" }));
    await user.click(screen.getByRole("button", { name: "Load defaults" }));

    expect(await screen.findByLabelText("Does")).toHaveValue(80);
    const last = requested[requested.length - 1];
    expect(last.get("breed")).toBe("sirohi");
    expect(last.get("system")).toBe("stall_fed");
  });

  it("opens the headline sections and reopens them for a freshly loaded scenario", async () => {
    const user = await renderLoaded({
      scenarios: [
        scenario({
          id: 6,
          name: "Expansion",
          assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 99 } },
        }),
      ],
    });

    const sectionOf = (label: string) =>
      screen.getByText(label).closest("details") as HTMLDetailsElement;
    expect(sectionOf("Meta")).toHaveAttribute("open");
    expect(sectionOf("Herd")).toHaveAttribute("open");
    expect(sectionOf("Finance")).not.toHaveAttribute("open");

    // Collapsing a section is a native <details> toggle; loading a scenario
    // rebuilds the editor, so the headline sections come back open.
    sectionOf("Herd").open = false;
    expect(sectionOf("Herd")).not.toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "Load" }));
    expect(await screen.findByLabelText("Does")).toHaveValue(99);
    expect(sectionOf("Herd")).toHaveAttribute("open");
  });

  it("counts each blocked numeric field separately, in singular and plural", async () => {
    const user = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));

    const months = screen.getAllByLabelText("Month");
    const prices = screen.getAllByLabelText("Price per head");
    await user.clear(months[0]);
    expect(screen.getByText(/Fix \d+ highlighted numeric field/)).toHaveTextContent(
      "Fix 1 highlighted numeric field before running or saving.",
    );

    await user.clear(months[1]);
    await user.type(prices[0], "-5");
    await user.type(prices[1], "-5");
    expect(screen.getByText(/Fix \d+ highlighted numeric field/)).toHaveTextContent(
      "Fix 4 highlighted numeric fields before running or saving.",
    );
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("gives every event input its own id and links its error message", async () => {
    const user = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));

    for (const [label, suffix] of [
      ["Month", "month"],
      ["Count", "count"],
      ["Price per head", "price"],
    ]) {
      const inputs = screen.getAllByLabelText(label);
      expect(inputs[0].id).toMatch(new RegExp(`^simulation-event-\\d+-${suffix}$`));
      expect(inputs[1].id).toMatch(new RegExp(`^simulation-event-\\d+-${suffix}$`));
      expect(inputs[0].id).not.toBe(inputs[1].id);
    }

    const month = screen.getAllByLabelText("Month")[1];
    await user.clear(month);
    const error = screen.getByText("A value is required.");
    expect(error).toHaveAttribute("id", `${month.id}-error`);
    expect(month).toHaveAttribute("aria-describedby", `${month.id}-error`);
  });

  it("sends the event kind and animal class the operator picked", async () => {
    const captured: { events?: unknown[] } = {};
    registerApiHandlers();
    server.use(
      http.post("/api/simulation/run", async ({ request }) => {
        const body = (await request.json()) as {
          assumptions: { events?: unknown[] };
        };
        captured.events = body.assumptions.events;
        return HttpResponse.json(RESULT);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const kind = screen.getByLabelText("Kind");
    await user.click(kind);
    await user.click(await screen.findByRole("option", { name: "Sale" }));
    await waitFor(() => expect(kind).toHaveTextContent("Sale"));
    const animalClass = screen.getByLabelText("Class");
    await user.click(animalClass);
    await user.click(await screen.findByRole("option", { name: "Buck" }));
    await waitFor(() => expect(animalClass).toHaveTextContent("Buck"));

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.events).toEqual([
      {
        month: 12,
        kind: "sale",
        animal_class: "buck",
        count: 10,
        price_per_head: null,
      },
    ]);
  });
});

describe("SimulationPage result provenance", () => {
  it("prints the model version beside the truncated assumptions fingerprint", async () => {
    await runAdHoc();

    expect(screen.getByText(/assumptions fingerprint/)).toHaveTextContent(
      "Model 3.0.0 · assumptions fingerprint 0123456789ab",
    );
  });

  it("names the saved scenario a stale result was measured against", async () => {
    registerApiHandlers({ scenarios: [scenario({ id: 1, name: "Plan A" })] });
    server.use(
      http.post("/api/simulation/scenarios/1/run", () => HttpResponse.json(RESULT)),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(
      await screen.findByText("Source: Saved scenario “Plan A”"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));

    expect(screen.getByText(/These results do not match/)).toHaveTextContent(
      "These results do not match the current saved scenario or run options. Run the simulation again before using them for a decision.",
    );
  });
});
