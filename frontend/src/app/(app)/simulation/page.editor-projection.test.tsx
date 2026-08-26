/**
 * Simulation page (part 2): the generic editor's choice fields, unit and
 * bound heuristics, validation of assumptions and events that arrive from the
 * API rather than from the keyboard, the scenario comparison table, the
 * editor-loader toasts (herd snapshot / calibration / failed runs) and the
 * invalidation scope of a scenario write.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

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

const SNAPSHOT = {
  does: 48,
  bucks: 3,
  f_kids: 4,
  f_weaners: 5,
  f_growers: 6,
  m_kids: 3,
  m_weaners: 2,
  m_growers: 1,
  total_head: 72,
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
  min_dscr: 1.55,
  payback_month: 30,
  break_even_meat_price_per_kg: 320,
  peak_capacity_head: 58,
  terminal_value: 150000,
  tax_total: 0,
  accounting_profit_total: 25000,
  minimum_cash_balance: 43000,
  minimum_cash_month: 14,
  additional_working_capital_required: 0,
  operating_margin: 0.25,
};

const RESULT = {
  months: [monthRow(), monthRow({ month: 2, calendar_month: 2, births: 4 })],
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

interface RunBody {
  assumptions: {
    meta?: { horizon_months?: number };
    growth?: {
      birth_weight_kg?: number;
      adult_weight_doe_kg?: number;
      weight_by_age_months?: number[];
    };
    risk?: { scenario_weights?: number[] };
    optimization?: { maximum_project_cost?: number | null };
    sales?: { festival_sale_months?: number[] };
    events?: unknown[];
  };
  monte_carlo: boolean;
  sensitivity: boolean;
  optimization: boolean;
}

function herdEvent(overrides: Record<string, unknown> = {}) {
  return {
    month: 6,
    kind: "purchase",
    animal_class: "doe",
    count: 5,
    price_per_head: null,
    ...overrides,
  };
}

function scenarioRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    farm_id: 1,
    name: "Plan",
    notes: "",
    assumptions: DEFAULTS,
    valid: true,
    validation_error: null,
    revision: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

/** Breeds + defaults + scenario list; the run POST captures its body. */
function registerApiHandlers(
  options: {
    scenarios?: unknown[];
    onRun?: (body: RunBody) => void;
    runResult?: unknown;
    defaults?: unknown;
    permissions?: string[];
    calibrationResult?: unknown;
  } = {},
) {
  server.use(
    permissionsHandler(options.permissions ?? MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () =>
      HttpResponse.json(options.defaults ?? DEFAULTS),
    ),
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
    http.get("/api/simulation/herd-snapshot", () => HttpResponse.json(SNAPSHOT)),
    http.get("/api/simulation/calibration", () =>
      HttpResponse.json(options.calibrationResult ?? {}),
    ),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return HttpResponse.json(options.runResult ?? RESULT);
    }),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(
  options?: Parameters<typeof registerApiHandlers>[0],
  queryClient = createTestQueryClient(),
) {
  registerApiHandlers(options);
  const view = renderWithProviders(<SimulationPage />, queryClient);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  return view;
}

const runButton = () => screen.getByRole("button", { name: "Run simulation" });

beforeEach(() => {
  toastMocks.error.mockClear();
  toastMocks.success.mockClear();
});

describe("SimulationPage assumption field projection", () => {
  it("renders every backend choice field as its human label", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        costs: { capacity_basis: "projected_peak" },
        optimization: { objective: "balanced" },
      },
    });

    // The closed trigger must read the human label, never the stored value.
    expect(screen.getByLabelText("Foundation Flock State")).toHaveTextContent("Open");
    expect(screen.getByLabelText("Capacity Basis")).toHaveTextContent("Projected peak");
    expect(screen.getByLabelText("Objective")).toHaveTextContent("Balanced");
    expect(screen.getByLabelText("Objective")).not.toHaveTextContent("balanced");
  });

  it("labels the remaining stored choices of every choice field", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        herd: { does: 50, foundation_flock_state: "mixed" },
        costs: { capacity_basis: "opening_herd" },
        optimization: { objective: "npv" },
      },
    });

    expect(screen.getByLabelText("Foundation Flock State")).toHaveTextContent("Mixed");
    expect(screen.getByLabelText("Capacity Basis")).toHaveTextContent("Opening herd");
    expect(screen.getByLabelText("Objective")).toHaveTextContent("Highest NPV");
  });

  it("labels the planned-capacity and liquidity choices", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        costs: { capacity_basis: "planned", planned_capacity_head: 80 },
        optimization: { objective: "liquidity" },
      },
    });

    expect(screen.getByLabelText("Capacity Basis")).toHaveTextContent("Planned capacity");
    expect(screen.getByLabelText("Objective")).toHaveTextContent("Strongest liquidity");
  });

  it("captions the yearly, litre and acre unit heuristics", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        costs: { vet_cost_per_year: 6000, shed_useful_life_years: 10 },
        sales: { lactation_milk_litres: 120 },
        feed: { cultivated_fodder_acres: 2 },
      },
    });

    // A per-year rate is money; a life expressed in years is a duration.
    expect(screen.getByLabelText("Vet Cost Per Year")).toHaveAttribute(
      "data-unit",
      "₹/yr",
    );
    expect(screen.getByLabelText("Shed Useful Life Years")).toHaveAttribute(
      "data-unit",
      "years",
    );
    expect(screen.getByLabelText("Lactation Milk Litres")).toHaveAttribute(
      "data-unit",
      "litres",
    );
    expect(screen.getByLabelText("Cultivated Fodder Acres")).toHaveAttribute(
      "data-unit",
      "acres",
    );
    expect(screen.getByText("Unit: ₹/yr")).toBeInTheDocument();
    expect(screen.getByText("Unit: acres")).toBeInTheDocument();
  });

  it("scopes the herd and feed money floors to price-shaped fields", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        herd: { does: 50, foundation_doe_price: 8000, average_doe_age_months: 36 },
        feed: { concentrate_price_per_kg: 30, grazing_hours_per_day: 6 },
      },
    });

    for (const label of ["Foundation Doe Price", "Concentrate Price Per Kg"]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("min", "0");
      expect(screen.getByLabelText(label)).toHaveAttribute("max", "1000000000");
    }
    // Neither heuristic claims a field it cannot recognise: those keep the
    // backend's own range instead of inheriting a money floor and ceiling.
    for (const label of ["Average Doe Age Months", "Grazing Hours Per Day"]) {
      expect(screen.getByLabelText(label)).not.toHaveAttribute("min");
      expect(screen.getByLabelText(label)).not.toHaveAttribute("max");
    }
  });

  it("renders an unrecognised array assumption with the generic fallback rule", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, risk: { scenario_weights: [0.2, 0.3, 0.5] } },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Risk"));
    const weights = screen.getByLabelText("Scenario Weights (values, comma-separated)");
    expect(weights).toHaveValue("0.2, 0.3, 0.5");

    // No length, bound or ordering rule applies to a key the editor does not
    // recognise — the backend stays the authority on it.
    await user.type(weights, ", 0.6");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.click(runButton());
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.risk?.scenario_weights).toEqual([
      0.2, 0.3, 0.5, 0.6,
    ]);
  });
});

describe("SimulationPage stored assumption validation", () => {
  it("reports stored herd events the inline inputs never validated", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: [
          herdEvent({ month: 999 }),
          herdEvent({ month: 2, count: 0 }),
          herdEvent({ month: 3, count: 1, price_per_head: -50 }),
        ],
      },
    });

    expect(
      screen.getByText("Event 1: month must be a whole number between 1 and 60."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Event 2: count must be greater than 0 and at most 100,000."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Event 3: price per head must be zero or more (or left blank).",
      ),
    ).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("rejects a stored start month carrying anything before the year", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        meta: { horizon_months: 60, start_year_month: "FY2026-01" },
      },
    });

    expect(
      screen.getByText(
        "Start year month must be a real month from 1900-01 to 2200-12.",
      ),
    ).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("rejects a stored start month carrying anything after the month", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        meta: { horizon_months: 60, start_year_month: "2026-013" },
      },
    });

    expect(
      screen.getByText(
        "Start year month must be a real month from 1900-01 to 2200-12.",
      ),
    ).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("reports stored festival months that duplicate or exceed the horizon", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [3, 3, 999] } },
    });

    expect(
      screen.getByText("Festival sale months must not contain duplicates."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Festival sale months must be whole numbers between 1 and 60.",
      ),
    ).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.clear(festivals);
    await user.type(festivals, "3, 12");

    expect(
      screen.queryByText("Festival sale months must not contain duplicates."),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Festival sale months must be whole numbers/),
    ).not.toBeInTheDocument();
    expect(runButton()).toBeEnabled();
  });

  // Rendering 501 event rows is the only way to reach this gate, so this
  // test carries its own (generous) budget.
  it("caps a stored event list at the backend maximum", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: Array.from({ length: 501 }, (_, index) =>
          herdEvent({ month: (index % 60) + 1 }),
        ),
      },
    });

    expect(
      screen.getByText("A simulation can contain at most 500 herd events."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add event" })).toBeDisabled();
    expect(runButton()).toBeDisabled();
  }, 45_000);

  it("labels every event kind and animal class in the closed row controls", async () => {
    const classes = [
      "doe",
      "buck",
      "female_kid",
      "male_kid",
      "female_weaner",
      "male_weaner",
      "female_grower",
      "male_grower",
    ];
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: classes.map((animalClass, index) =>
          herdEvent({
            month: index + 1,
            animal_class: animalClass,
            kind: index % 2 === 0 ? "purchase" : "sale",
          }),
        ),
      },
    });

    const selectedLabels = (label: string) =>
      screen
        .getAllByLabelText(label)
        .map(
          (trigger) =>
            trigger.querySelector("[data-slot='select-value']")?.textContent,
        );
    expect(selectedLabels("Class")).toEqual([
      "Doe",
      "Buck",
      "Female kid",
      "Male kid",
      "Female weaner",
      "Male weaner",
      "Female grower",
      "Male grower",
    ]);
    expect(selectedLabels("Kind")).toEqual([
      "Purchase",
      "Sale",
      "Purchase",
      "Sale",
      "Purchase",
      "Sale",
      "Purchase",
      "Sale",
    ]);
  });
});

describe("SimulationPage editor identity and recovery", () => {
  it("shows no scheduled events until the defaults arrive", async () => {
    registerApiHandlers();
    server.use(
      http.get("/api/simulation/defaults", () => new Promise<Response>(() => {})),
    );
    renderWithProviders(<SimulationPage />);

    expect(await screen.findByText("Loading defaults…")).toBeInTheDocument();
    expect(screen.getByText("No scheduled events")).toBeInTheDocument();
    expect(screen.queryByText(/^Event 1:/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps each defaults-loaded event row's validation on its own identity", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: [herdEvent({ month: 6 }), herdEvent({ month: 7, count: 9 })],
      },
    });

    await user.clear(screen.getAllByLabelText("Count")[1]);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);

    expect(screen.getAllByLabelText("Count")).toHaveLength(1);
    expect(screen.getByLabelText("Count")).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("keeps each scenario-loaded event row's validation on its own identity", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      scenarios: [
        scenarioRow({
          id: 7,
          name: "Restock plan",
          assumptions: {
            ...DEFAULTS,
            events: [herdEvent({ month: 6 }), herdEvent({ month: 7, count: 9 })],
          },
        }),
      ],
    });

    const row = (await screen.findByText("Restock plan")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Load" }));
    expect(screen.getAllByLabelText("Count")).toHaveLength(2);

    await user.clear(screen.getAllByLabelText("Count")[1]);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);

    expect(screen.getAllByLabelText("Count")).toHaveLength(1);
    expect(screen.getByLabelText("Count")).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("re-checks an untouched month list when the horizon drops", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [1, 3] } },
    });

    const horizon = screen.getByLabelText("Horizon Months");
    await user.clear(horizon);
    await user.type(horizon, "12");

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    // Both stored months still fit the shorter horizon, so the untouched
    // field must stay valid instead of being re-read as a single number.
    expect(festivals).toHaveValue("1, 3");
    expect(festivals).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByText(/Every entry must be at most/)).not.toBeInTheDocument();
    expect(runButton()).toBeEnabled();
  });

  it("accepts an empty festival month list and sends no months", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [12, 24] } },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.clear(festivals);

    expect(festivals).toHaveValue("");
    expect(festivals).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.click(runButton());
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual([]);
  });

  it("round-trips an optional ceiling between blank and a number", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        optimization: { maximum_project_cost: null, maximum_funding_gap: null },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Optimization", { selector: "summary" }));
    const ceiling = screen.getByLabelText("Maximum Project Cost");
    // "No ceiling" renders as a genuinely blank box — not as text the number
    // input would silently drop — so the hint below it tells the truth.
    expect(ceiling).toHaveValue(null);
    expect(ceiling).toHaveAttribute("value", "");
    expect(screen.getAllByText("Unit: ₹ — blank means no limit")).toHaveLength(2);

    await user.type(ceiling, "750000");
    await user.click(runButton());
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.optimization?.maximum_project_cost).toBe(750000);

    await user.clear(ceiling);
    expect(ceiling).toHaveValue(null);
    expect(screen.queryByText("A value is required.")).not.toBeInTheDocument();
    await user.click(runButton());
    await waitFor(() =>
      expect(captured.body?.assumptions.optimization?.maximum_project_cost).toBeNull(),
    );
  });

  it("links each invalid editor field to its own error message", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [12] } },
    });

    const horizon = screen.getByLabelText("Horizon Months");
    await user.clear(horizon);
    await user.type(horizon, "5");
    expect(horizon).toHaveAttribute("aria-describedby", "sim-meta-horizon_months-error");
    expect(screen.getByText("Must be at least 12.")).toHaveAttribute(
      "id",
      "sim-meta-horizon_months-error",
    );

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.clear(festivals);
    await user.type(festivals, "x");
    expect(festivals).toHaveAttribute(
      "aria-describedby",
      "sim-sales-festival_sale_months-error",
    );
    expect(screen.getByText("Enter only comma-separated numbers.")).toHaveAttribute(
      "id",
      "sim-sales-festival_sale_months-error",
    );
  });

  it("mirrors a growth-curve edit back into the birth weight", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
          weight_by_age_months: [
            2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 24.5, 26.5,
          ],
        },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Growth"));
    const curve = screen.getByLabelText(/Weight By Age Months/);
    await user.clear(curve);
    await user.type(
      curve,
      "3,4.5,6.5,8.5,10.5,12.5,14.5,16.5,18.5,20.5,22.5,24.5,26.5",
    );

    // Age zero of the curve IS the birth weight; the scalar field follows it.
    expect(screen.getByLabelText("Birth Weight Kg")).toHaveValue(3);
    await user.click(runButton());
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.growth?.birth_weight_kg).toBe(3);
    expect(captured.body?.assumptions.growth?.weight_by_age_months?.[0]).toBe(3);
    expect(captured.body?.assumptions.growth?.adult_weight_doe_kg).toBe(32);
  });

  it("does not invent a growth curve when the defaults omit one", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
        },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Growth"));
    const birth = screen.getByLabelText("Birth Weight Kg");
    await user.clear(birth);
    await user.type(birth, "3");

    // The mirror writes no curve points the breed defaults never supplied.
    expect(screen.getByLabelText(/Weight By Age Months/)).toHaveValue("");
    await user.click(runButton());
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.growth?.birth_weight_kg).toBe(3);
    expect(captured.body?.assumptions.growth?.weight_by_age_months).toEqual([]);
  });
});

describe("SimulationPage scenario comparison", () => {
  it("renders every comparison metric row for each selected scenario", async () => {
    const scenarios = [
      scenarioRow({ id: 1, name: "Plan one" }),
      scenarioRow({ id: 2, name: "Plan two" }),
    ];
    const weakResult = {
      ...RESULT,
      metrics: {
        ...METRICS,
        npv: -12000,
        irr: null,
        mirr: null,
        bcr: null,
        avg_dscr: null,
        min_dscr: null,
        payback_month: null,
        operating_margin: null,
        minimum_cash_balance: -5000,
        additional_working_capital_required: 5000,
      },
    };
    let comparedIds: string | null = null;
    const user = userEvent.setup();
    await renderLoaded({ scenarios });
    server.use(
      http.get("/api/simulation/scenarios/compare", ({ request }) => {
        comparedIds = new URL(request.url).searchParams.get("ids");
        return HttpResponse.json({ scenarios, results: [RESULT, weakResult] });
      }),
    );

    await user.click(await screen.findByLabelText("Compare Plan one"));
    await user.click(screen.getByLabelText("Compare Plan two"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));

    const table = ((await screen.findByText("Comparison")).closest(
      "div",
    ) as HTMLElement);
    expect(comparedIds).toBe("1,2");
    expect(
      within(table)
        .getAllByRole("row")
        .slice(1)
        .map((row) => (row as HTMLTableRowElement).cells[0].textContent),
    ).toEqual([
      "NPV",
      "IRR",
      "MIRR",
      "BCR",
      "Avg DSCR",
      "Minimum DSCR",
      "Operating margin",
      "Minimum cash",
      "Additional working capital",
      "Payback month",
    ]);

    const rowFor = (label: string) =>
      within(table).getByText(label).closest("tr") as HTMLElement;
    expect(within(rowFor("NPV")).getByText("₹2,34,567")).toBeInTheDocument();
    expect(within(rowFor("NPV")).getByText("-₹12,000")).toBeInTheDocument();
    expect(within(rowFor("IRR")).getByText("18.0%")).toBeInTheDocument();
    expect(within(rowFor("MIRR")).getByText("16.0%")).toBeInTheDocument();
    expect(within(rowFor("BCR")).getByText("1.42")).toBeInTheDocument();
    expect(within(rowFor("Avg DSCR")).getByText("1.80")).toBeInTheDocument();
    expect(within(rowFor("Minimum DSCR")).getByText("1.55")).toBeInTheDocument();
    expect(within(rowFor("Operating margin")).getByText("25.0%")).toBeInTheDocument();
    expect(within(rowFor("Minimum cash")).getByText("₹43,000")).toBeInTheDocument();
    expect(within(rowFor("Minimum cash")).getByText("-₹5,000")).toBeInTheDocument();
    expect(
      within(rowFor("Additional working capital")).getByText("₹5,000"),
    ).toBeInTheDocument();
    expect(within(rowFor("Payback month")).getByText("30")).toBeInTheDocument();
    // Every unavailable metric of the weaker scenario reads as a dash.
    for (const label of [
      "IRR",
      "MIRR",
      "BCR",
      "Avg DSCR",
      "Minimum DSCR",
      "Operating margin",
      "Payback month",
    ]) {
      expect(within(rowFor(label)).getByText("—")).toBeInTheDocument();
    }
  });
});

describe("SimulationPage loader and run feedback", () => {
  it("reports a transport failure while loading the herd snapshot", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    server.use(
      http.get("/api/simulation/herd-snapshot", () => HttpResponse.error()),
    );

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "Could not load the herd snapshot.",
      ),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(50);
  });

  it("reports the head count an imported herd snapshot carried", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));
    expect(toastMocks.success).toHaveBeenCalledWith("Loaded current herd (72 head).");
  });

  it("reports a transport failure while calibrating from farm records", async () => {
    const user = userEvent.setup();
    await renderLoaded({ permissions: CALIBRATION_PERMS });
    server.use(http.get("/api/simulation/calibration", () => HttpResponse.error()));

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "Could not calibrate from farm records.",
      ),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(50);
  });

  it("discards a calibration a scenario load overtook", async () => {
    let releaseCalibration!: () => void;
    let markCalibrationStarted!: () => void;
    const calibrationGate = new Promise<void>((resolve) => {
      releaseCalibration = resolve;
    });
    const calibrationStarted = new Promise<void>((resolve) => {
      markCalibrationStarted = resolve;
    });
    registerApiHandlers({
      permissions: CALIBRATION_PERMS,
      scenarios: [
        scenarioRow({
          id: 7,
          name: "Expansion",
          assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 99 } },
        }),
      ],
    });
    server.use(
      http.get("/api/simulation/calibration", async () => {
        markCalibrationStarted();
        await calibrationGate;
        return HttpResponse.json({
          assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 73 } },
          evidence: [],
          warnings: [],
          coverage_score: 0,
          reference_date: "2026-08-10",
          lookback_months: 24,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByLabelText("Does")).toHaveValue(50);

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await calibrationStarted;
    const row = (await screen.findByText("Expansion")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Load" }));
    expect(screen.getByLabelText("Does")).toHaveValue(99);
    releaseCalibration();

    // The scenario the operator loaded meanwhile owns the editor, and the
    // discarded calibration says so instead of failing silently.
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "The editor or calibration settings changed while calibration was running. Calibrate again to apply fresh farm evidence.",
      ),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(99);
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();
    expect(screen.queryByText("Farm calibration evidence")).not.toBeInTheDocument();
  });

  it("rebinds the calibrated breed and replaces the event list", async () => {
    const snapshotBreeds: (string | null)[] = [];
    registerApiHandlers({
      permissions: CALIBRATION_PERMS,
      defaults: { ...DEFAULTS, events: [herdEvent()] },
      calibrationResult: {
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
            period_start: "2024-08-10",
            period_end: "2026-08-10",
          },
          {
            path: "mortality.adult",
            previous_value: 0.05,
            calibrated_value: 0.04,
            sample_size: 30,
            confidence: "medium",
            method: "Observed adult deaths",
            source: "Health records",
            period_start: "2024-08-10",
            period_end: "2026-08-10",
          },
        ],
        warnings: [],
        coverage_score: 0.72,
        reference_date: "2026-08-10",
        lookback_months: 24,
      },
    });
    server.use(
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["osmanabadi", "sirohi"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/herd-snapshot", ({ request }) => {
        snapshotBreeds.push(new URL(request.url).searchParams.get("breed"));
        return HttpResponse.json(SNAPSHOT);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Month")).toHaveLength(1);

    await user.click(screen.getByLabelText("Breed"));
    await user.click(await screen.findByRole("option", { name: "sirohi" }));
    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(73));
    // Calibrated assumptions carry no events, so the editor keeps none.
    expect(screen.getByText("No scheduled events")).toBeInTheDocument();
    expect(toastMocks.success).toHaveBeenCalledWith(
      "Calibrated 2 assumptions from farm records.",
    );

    // The editor now holds sirohi assumptions, so a herd import must bucket
    // head counts by that breed's thresholds.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Use current herd" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(snapshotBreeds).toEqual(["sirohi"]));
  });

  it("keeps each calibrated event row's validation on its own identity", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      permissions: CALIBRATION_PERMS,
      calibrationResult: {
        assumptions: {
          ...DEFAULTS,
          events: [herdEvent({ month: 6 }), herdEvent({ month: 7, count: 9 })],
        },
        evidence: [],
        warnings: [],
        coverage_score: 0,
        reference_date: "2026-08-10",
        lookback_months: 24,
      },
    });

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(screen.getAllByLabelText("Count")).toHaveLength(2));

    await user.clear(screen.getAllByLabelText("Count")[1]);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);

    expect(screen.getAllByLabelText("Count")).toHaveLength(1);
    expect(screen.getByLabelText("Count")).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("reports the fallback message when an ad-hoc run cannot reach the API", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    server.use(http.post("/api/simulation/run", () => HttpResponse.error()));

    await user.click(runButton());

    expect(await screen.findByText("Simulation failed")).toBeInTheDocument();
    expect(toastMocks.error).toHaveBeenCalledWith("Simulation failed");
  });

  it("reports the fallback message when a saved scenario run cannot reach the API", async () => {
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenarioRow({ id: 7, name: "Plan B" })] });
    server.use(
      http.post("/api/simulation/scenarios/7/run", () => HttpResponse.error()),
    );

    const row = (await screen.findByText("Plan B")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Run" }));

    expect(await screen.findByText("Scenario run failed")).toBeInTheDocument();
    expect(toastMocks.error).toHaveBeenCalledWith("Scenario run failed");
  });
});

describe("SimulationPage scenario list refresh", () => {
  it("refreshes only the scenario queries after deleting a scenario", async () => {
    let defaultsCalls = 0;
    let scenarioCalls = 0;
    const scenarios = [scenarioRow({ id: 7, name: "Old plan" })];
    registerApiHandlers();
    server.use(
      http.get("/api/simulation/defaults", () => {
        defaultsCalls += 1;
        return HttpResponse.json(DEFAULTS);
      }),
      http.get("/api/simulation/scenarios", () => {
        scenarioCalls += 1;
        return HttpResponse.json({
          items: scenarios,
          total: scenarios.length,
          limit: 20,
          offset: 0,
        });
      }),
      http.delete("/api/simulation/scenarios/7", () => {
        scenarios.length = 0;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Old plan")).toBeInTheDocument();
    expect(defaultsCalls).toBe(1);

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(confirm).toHaveBeenCalledWith('Delete scenario "Old plan"?');

    expect(await screen.findByText("No saved scenarios yet.")).toBeInTheDocument();
    expect(scenarioCalls).toBeGreaterThan(1);
    // The write touched saved scenarios only: breed defaults are untouched
    // editor state and must not be refetched underneath the operator.
    expect(defaultsCalls).toBe(1);
    confirm.mockRestore();
  });

  it("re-homes a scenario page that shrank past the current offset", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded({ scenarios }, queryClient);

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 21")).toBeInTheDocument();

    scenarios.splice(5);
    await act(async () => {
      await queryClient.invalidateQueries();
    });

    expect(await screen.findByText("Plan 1")).toBeInTheDocument();
    expect(screen.queryByText(/page no longer exists/)).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1–5 of 5 saved scenarios")).toBeInTheDocument();
  });
});

describe("SimulationPage result formatting", () => {
  it("prints the cash-trough month as a month and string figures verbatim", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      runResult: {
        ...RESULT,
        metric_explanations: [
          {
            key: "minimum_cash_balance",
            title: "Minimum cash",
            explanation: "The lowest cash balance the projection reaches.",
            figures: { minimum_cash_balance: 43000, minimum_cash_month: 14 },
          },
          {
            key: "project_cost",
            title: "Project cost",
            explanation: "Total project cost at month 0.",
            figures: {
              project_cost: 500000,
              capacity_basis: "projected_peak",
              capacity_places: 58,
            },
          },
        ],
      },
    });

    await user.click(runButton());
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Explain Minimum cash (month 14)" }),
    );
    const cashDialog = await screen.findByRole("dialog");
    expect(within(cashDialog).getByText("₹43,000")).toBeInTheDocument();
    // A cash-trough month is a month index, not rupees.
    expect(within(cashDialog).getByText("14")).toBeInTheDocument();
    expect(within(cashDialog).queryByText("₹14")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Explain Project cost" }));
    const costDialog = await screen.findByRole("dialog");
    expect(within(costDialog).getByText("₹5,00,000")).toBeInTheDocument();
    // A string figure is the backend's own wording and passes through as-is.
    expect(within(costDialog).getByText("projected_peak")).toBeInTheDocument();
    expect(within(costDialog).getByText("58")).toBeInTheDocument();
  });

  it("renders a dash for a missing cohort count (backend contract change)", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      runResult: {
        ...RESULT,
        months: [monthRow({ total_herd: null, births: 4.2 })],
      },
    });

    await user.click(runButton());

    const projection = ((await screen.findByText("Monthly projection")).closest(
      "[data-slot='card']",
    ) as HTMLElement);
    const row = within(projection).getByText("4.2").closest("tr") as HTMLTableRowElement;
    // Cell order: month, total herd, births, …
    expect(row.cells[1]).toHaveTextContent("—");
    expect(row.cells[2]).toHaveTextContent("4.2");
  });

  it("renders calibration evidence arrays and missing baselines", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      permissions: CALIBRATION_PERMS,
      calibrationResult: {
        assumptions: DEFAULTS,
        evidence: [
          {
            path: "growth.weight_by_age_months",
            previous_value: null,
            calibrated_value: [2.5, 4.25],
            sample_size: 40,
            confidence: "medium",
            method: "Weighed kids at birth and month 1",
            source: "Weight records",
            period_start: "2024-08-10",
            period_end: "2026-08-10",
          },
        ],
        warnings: [],
        coverage_score: 0.5,
        reference_date: "2026-08-10",
        lookback_months: 24,
      },
    });

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    const row = ((await screen.findByText("Weight records")).closest(
      "tr",
    ) as HTMLElement);
    expect(within(row).getByText("2.500, 4.250")).toBeInTheDocument();
    // No baseline value at all reads as a dash, not as an empty cell.
    expect(within(row).getByText("—")).toBeInTheDocument();
  });
});
