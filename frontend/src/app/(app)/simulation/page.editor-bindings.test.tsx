/**
 * Simulation page: what every whole-editor loader owes the operator — the
 * scenario/calibration bindings it replaces, the validation gates it releases,
 * the uncommitted draft it discards — plus the epoch arithmetic that keeps an
 * overtaken herd import out of a freshly loaded scenario, the row identity a
 * recovered event draft commits to, and the list/comparison refreshes a run,
 * save, update or delete owes once the server has moved on.
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

function herd(does: number) {
  return { does, bucks: 2 };
}

function purchase(overrides: Record<string, unknown> = {}) {
  return {
    month: 6,
    kind: "purchase",
    animal_class: "doe",
    count: 10,
    price_per_head: null,
    ...overrides,
  };
}

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

/** Calibration payload whose assumptions carry a distinct doe count. */
function calibrationPayload(assumptions: Record<string, unknown>) {
  return {
    assumptions,
    evidence: [
      {
        path: "reproduction.conception_rate",
        previous_value: 0.85,
        calibrated_value: 0.78,
        sample_size: 40,
        confidence: "medium",
        method: "Observed conceptions per exposure",
        source: "Breeding records",
        period_start: "2024-08-10",
        period_end: "2026-08-10",
      },
    ],
    warnings: [],
    coverage_score: 0.72,
    reference_date: "2026-08-10",
    lookback_months: 24,
  };
}

interface RunBody {
  assumptions: Record<string, unknown> & {
    growth?: { weight_by_age_months?: number[] };
    sales?: { festival_sale_months?: number[] };
    events?: Record<string, unknown>[];
  };
  monte_carlo: boolean;
  sensitivity: boolean;
  optimization: boolean;
}

function scenarioRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    farm_id: 1,
    name: "Plan",
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

/** Breeds + defaults + scenario list, plus the optional loaders each test
 *  drives. `defaults` may be a factory so a reload can answer differently. */
function registerApiHandlers(
  options: {
    defaults?: unknown | (() => unknown);
    scenarios?: { id: number; name: string }[];
    permissions?: string[];
    snapshot?: () => Response | Promise<Response>;
    calibration?: () => Response | Promise<Response>;
    compare?: () => Response;
    run?: () => Response;
    onRun?: (body: RunBody) => void;
    onScenarios?: (offset: number) => Response | Promise<Response> | undefined;
  } = {},
) {
  server.use(
    permissionsHandler(options.permissions ?? MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => {
      const payload =
        typeof options.defaults === "function"
          ? (options.defaults as () => unknown)()
          : (options.defaults ?? DEFAULTS);
      return HttpResponse.json(payload as Record<string, unknown>);
    }),
    http.get("/api/simulation/scenarios", ({ request }) => {
      const scenarios = options.scenarios ?? [];
      const params = new URL(request.url).searchParams;
      const limit = Number(params.get("limit") ?? 20);
      const offset = Number(params.get("offset") ?? 0);
      const overridden = options.onScenarios?.(offset);
      if (overridden !== undefined) return overridden;
      return HttpResponse.json({
        items: scenarios.slice(offset, offset + limit),
        total: scenarios.length,
        limit,
        offset,
      });
    }),
    http.get("/api/simulation/herd-snapshot", () =>
      options.snapshot ? options.snapshot() : HttpResponse.json(SNAPSHOT),
    ),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return options.run ? options.run() : HttpResponse.json(RESULT);
    }),
  );
  if (options.calibration)
    server.use(http.get("/api/simulation/calibration", () => options.calibration!()));
  if (options.compare)
    server.use(
      http.get("/api/simulation/scenarios/compare", () => options.compare!()),
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

function runButton() {
  return screen.getByRole("button", { name: "Run simulation" });
}

function scenariosCard() {
  return screen
    .getByText("Scenarios", { selector: "[data-slot='card-title'], h2" })
    .closest("[data-slot='card']") as HTMLElement;
}

function scenarioActions(name: string) {
  return within(screen.getByText(name).closest("tr") as HTMLElement);
}

describe("SimulationPage unit captions", () => {
  // `_per_year` names a rate of the underlying metric; only the fields that
  // merely contain "year" are durations.
  it("captions a per-year cost in rupees a year, not as a duration", async () => {
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        costs: { misc_overhead_per_year: 24000, shed_useful_life_years: 20 },
      },
    });

    await user.click(screen.getByText("Costs"));

    const overhead = screen.getByLabelText("Misc Overhead Per Year");
    expect(overhead).toHaveValue(24000);
    expect(overhead).toHaveAttribute("data-unit", "₹/yr");
    expect(screen.getByText("Unit: ₹/yr")).toBeInTheDocument();
    expect(screen.getByLabelText("Shed Useful Life Years")).toHaveAttribute(
      "data-unit",
      "years",
    );
  });
});

describe("SimulationPage comma-separated lists", () => {
  // Typed lists arrive with human spacing: blank-looking text is an empty
  // list, a gap between two commas is a missing entry (never a zero), and a
  // padded entry is just that entry.
  it("reads a comma list through the spaces around it", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [3, 9] } },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Sales"));
    const months = screen.getByLabelText(/Festival Sale Months/);

    await user.clear(months);
    await user.type(months, "   ");
    expect(
      screen.queryByText("Enter only comma-separated numbers."),
    ).not.toBeInTheDocument();
    expect(runButton()).toBeEnabled();

    await user.clear(months);
    await user.type(months, "4, ,9");
    expect(screen.getByText("Enter only comma-separated numbers.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.clear(months);
    await user.type(months, " 4 , 9 ");
    expect(
      screen.queryByText("Enter only comma-separated numbers."),
    ).not.toBeInTheDocument();

    await user.click(runButton());
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual([4, 9]);
  });
});

describe("SimulationPage event row identity", () => {
  // A rejected month becomes legal again when the horizon grows, and the row
  // that owns it may have moved up in the meantime. The recovered value must
  // reach that row, not the index it had when the editor first rendered.
  it("commits a bounds-recovered event draft to the row that still owns it", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: [
          purchase({ month: 6 }),
          purchase({
            month: 30,
            kind: "sale",
            animal_class: "buck",
            count: 4,
            price_per_head: 9000,
          }),
        ],
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    const months = screen.getAllByLabelText("Month");
    await user.clear(months[1]);
    await user.type(months[1], "90");
    expect(screen.getByText("Must be at most 60.")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    expect(screen.getAllByLabelText("Month")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "10 yr" }));
    expect(screen.queryByText("Must be at most 60.")).not.toBeInTheDocument();

    // Blur releases the recovered draft, so the field now shows what the
    // event actually stores.
    await user.click(screen.getByLabelText("Month"));
    await user.tab();
    expect(screen.getByLabelText("Month")).toHaveValue(90);

    await user.click(runButton());
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.events).toEqual([
      {
        month: 90,
        kind: "sale",
        animal_class: "buck",
        count: 4,
        price_per_head: 9000,
      },
    ]);
  });

  // Every loader that replaces the event list has to hand each row an identity
  // of its own; removing a row then releases that row's validation gate — and
  // only that row's.
  it("releases the run gate a removed row from any loaded event list held", async () => {
    const user = await renderLoaded({
      permissions: CALIBRATION_PERMS,
      defaults: { ...DEFAULTS, events: [purchase({ month: 6 }), purchase({ month: 9 })] },
      scenarios: [
        scenarioRow({
          id: 5,
          name: "Two purchases",
          assumptions: {
            ...DEFAULTS,
            events: [purchase({ month: 4 }), purchase({ month: 8 })],
          },
        }),
      ],
      calibration: () =>
        HttpResponse.json(
          calibrationPayload({
            ...DEFAULTS,
            events: [purchase({ month: 5 }), purchase({ month: 10 })],
          }),
        ),
    });

    const breakTheFirstRowThenDropIt = async (survivingMonth: number) => {
      expect(screen.getAllByLabelText("Count")).toHaveLength(2);
      await user.clear(screen.getAllByLabelText("Count")[0]);
      expect(screen.getByText("A value is required.")).toBeInTheDocument();
      expect(runButton()).toBeDisabled();

      await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);

      expect(screen.queryByText("A value is required.")).not.toBeInTheDocument();
      expect(screen.queryByText(/highlighted numeric field/)).not.toBeInTheDocument();
      expect(runButton()).toBeEnabled();
      expect(screen.getByLabelText("Month")).toHaveValue(survivingMonth);
    };

    await breakTheFirstRowThenDropIt(9);

    await user.click(scenarioActions("Two purchases").getByRole("button", { name: "Load" }));
    await breakTheFirstRowThenDropIt(8);

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(screen.getAllByLabelText("Count")).toHaveLength(2));
    await breakTheFirstRowThenDropIt(10);
  });
});

describe("SimulationPage editor bindings", () => {
  it("unbinds the scenario and the calibration evidence each loader replaces", async () => {
    let defaultsCalls = 0;
    const user = await renderLoaded({
      permissions: CALIBRATION_PERMS,
      // A reload must answer with a payload of its own, exactly as a real
      // breed/system reload does.
      defaults: () => {
        defaultsCalls += 1;
        return { ...DEFAULTS, herd: herd(50 + defaultsCalls) };
      },
      scenarios: [
        scenarioRow({ id: 7, name: "Expansion", assumptions: { ...DEFAULTS, herd: herd(99) } }),
      ],
      calibration: () =>
        HttpResponse.json(calibrationPayload({ ...DEFAULTS, herd: herd(73) })),
    });
    const loadExpansion = () =>
      user.click(scenarioActions("Expansion").getByRole("button", { name: "Load" }));
    const loadDefaults = () =>
      user.click(screen.getByRole("button", { name: "Load defaults" }));
    const calibrate = () =>
      user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    await loadExpansion();
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();

    await calibrate();
    expect(await screen.findByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).toBeInTheDocument();
    // Calibrated assumptions are nobody's saved scenario any more.
    expect(screen.queryByText("Editing scenario: Expansion")).not.toBeInTheDocument();

    await loadExpansion();
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();
    // The evidence described the calibration this scenario just replaced.
    expect(screen.queryByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).not.toBeInTheDocument();

    await loadDefaults();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(52));
    expect(screen.queryByText("Editing scenario: Expansion")).not.toBeInTheDocument();

    await calibrate();
    expect(await screen.findByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).toBeInTheDocument();

    await loadDefaults();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(53));
    expect(screen.queryByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).not.toBeInTheDocument();
  });

  // An event row's validation entry outlives the row when a loader swaps the
  // whole list, and nothing left on screen can clear it — so the loader must.
  it("clears the run gate an invalid event row held when a loader replaces the list", async () => {
    let defaultsCalls = 0;
    const user = await renderLoaded({
      permissions: CALIBRATION_PERMS,
      defaults: () => {
        defaultsCalls += 1;
        return { ...DEFAULTS, events: [purchase({ month: defaultsCalls === 1 ? 6 : 7 })] };
      },
      scenarios: [
        scenarioRow({
          id: 3,
          name: "One purchase",
          assumptions: { ...DEFAULTS, events: [purchase({ month: 4 })] },
        }),
      ],
      calibration: () =>
        HttpResponse.json(
          calibrationPayload({ ...DEFAULTS, events: [purchase({ month: 5 })] }),
        ),
    });

    const breakTheOnlyRow = async () => {
      await user.clear(screen.getByLabelText("Count"));
      expect(
        screen.getByText("Fix 1 highlighted numeric field before running or saving."),
      ).toBeInTheDocument();
      expect(runButton()).toBeDisabled();
    };
    const expectFreshList = async (month: number) => {
      await waitFor(() => expect(screen.getByLabelText("Month")).toHaveValue(month));
      expect(screen.queryByText(/highlighted numeric field/)).not.toBeInTheDocument();
      expect(runButton()).toBeEnabled();
    };

    await breakTheOnlyRow();
    await user.click(scenarioActions("One purchase").getByRole("button", { name: "Load" }));
    await expectFreshList(4);

    await breakTheOnlyRow();
    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await expectFreshList(5);

    await breakTheOnlyRow();
    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await expectFreshList(7);
  });

  // A local draft belongs to the assumption set it was typed against; every
  // loader that replaces that set has to take the draft with it.
  it("discards an uncommitted draft on every whole-editor load", async () => {
    let defaultsCalls = 0;
    const user = await renderLoaded({
      permissions: CALIBRATION_PERMS,
      defaults: () => {
        defaultsCalls += 1;
        return { ...DEFAULTS, herd: herd(defaultsCalls === 1 ? 50 : 51) };
      },
      scenarios: [
        scenarioRow({ id: 7, name: "Expansion", assumptions: { ...DEFAULTS, herd: herd(99) } }),
      ],
      calibration: () =>
        HttpResponse.json(calibrationPayload({ ...DEFAULTS, herd: herd(73) })),
    });

    // A rejected draft is the one blur cannot clear, so it survives the click
    // on the loader button and only the reload can dislodge it.
    const emptyTheDoeCount = async () => {
      await user.clear(screen.getByLabelText("Does"));
      expect(screen.getByLabelText("Does")).toHaveValue(null);
      expect(screen.getByText("A value is required.")).toBeInTheDocument();
    };
    const expectReloadedDoeCount = async (does: number) => {
      await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(does));
      expect(screen.queryByText("A value is required.")).not.toBeInTheDocument();
    };

    await emptyTheDoeCount();
    await user.click(scenarioActions("Expansion").getByRole("button", { name: "Load" }));
    await expectReloadedDoeCount(99);

    await emptyTheDoeCount();
    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await expectReloadedDoeCount(48);

    await emptyTheDoeCount();
    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await expectReloadedDoeCount(73);

    await emptyTheDoeCount();
    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await expectReloadedDoeCount(51);

    await emptyTheDoeCount();
    await user.click(scenarioActions("Expansion").getByRole("button", { name: "Load" }));
    await expectReloadedDoeCount(99);
  });

  it("detaches the editor from its scenario when the current herd is imported", async () => {
    const user = await renderLoaded({
      scenarios: [
        scenarioRow({ id: 7, name: "Expansion", assumptions: { ...DEFAULTS, herd: herd(99) } }),
      ],
    });

    await user.click(scenarioActions("Expansion").getByRole("button", { name: "Load" }));
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update Expansion" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));
    // These head counts are the farm's, not the saved scenario's — updating
    // that scenario from here would overwrite it with something it never said.
    expect(screen.queryByText("Editing scenario: Expansion")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Update Expansion" }),
    ).not.toBeInTheDocument();
  });

  // A 200 whose body is not the calibration envelope must be reported like any
  // other failure — never left half-applied to the editor.
  it("reports a calibration payload the editor cannot apply", async () => {
    const user = await renderLoaded({
      permissions: CALIBRATION_PERMS,
      calibration: () => HttpResponse.json(null),
    });

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "Could not calibrate from farm records.",
      ),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(50);
    expect(screen.queryByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).not.toBeInTheDocument();
  });

  // Every loader claims a fresh editor intent, so an in-flight snapshot that
  // two later loads have overtaken must be reported, never applied.
  it("rejects a herd snapshot a scenario load and a calibration have overtaken", async () => {
    let releaseSnapshot!: () => void;
    let releaseCalibration!: () => void;
    const user = await renderLoaded({
      permissions: CALIBRATION_PERMS,
      scenarios: [
        scenarioRow({ id: 7, name: "Expansion", assumptions: { ...DEFAULTS, herd: herd(99) } }),
      ],
      snapshot: () =>
        new Promise<Response>((resolve) => {
          releaseSnapshot = () => resolve(HttpResponse.json(SNAPSHOT));
        }),
      calibration: () =>
        new Promise<Response>((resolve) => {
          releaseCalibration = () =>
            resolve(HttpResponse.json(calibrationPayload({ ...DEFAULTS, herd: herd(73) })));
        }),
    });

    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(releaseSnapshot).toBeTypeOf("function"));

    await user.click(scenarioActions("Expansion").getByRole("button", { name: "Load" }));
    expect(screen.getByLabelText("Does")).toHaveValue(99);

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(releaseCalibration).toBeTypeOf("function"));

    releaseSnapshot();

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "The editor was reloaded while the herd snapshot was loading. Click “Use current herd” again to apply it.",
      ),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(99);
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();

    // The newest intent still lands when it arrives.
    releaseCalibration();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(73));
  });
});

describe("SimulationPage stored growth curve", () => {
  // The adult-weight floor is the heaviest yearling — ages 0-12 — so a stored
  // curve that keeps growing past the first year must not raise that floor.
  it("measures the yearling weight over the first year of a longer stored curve", async () => {
    const curve = [
      2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 24.5, 26.5, 40, 45,
    ];
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
          weight_by_age_months: curve,
        },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    expect(
      screen.queryByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).not.toBeInTheDocument();
    expect(runButton()).toBeEnabled();

    await user.click(runButton());
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.growth?.weight_by_age_months).toEqual(curve);
  });
});

describe("SimulationPage run failures", () => {
  it("clears a failed run's banner once a later run succeeds", async () => {
    let runFails = true;
    const user = await renderLoaded({
      scenarios: [scenarioRow({ id: 3, name: "Plan B" })],
      run: () =>
        runFails
          ? HttpResponse.json({ detail: "Feed model unavailable." }, { status: 503 })
          : HttpResponse.json(RESULT),
    });
    server.use(
      http.post("/api/simulation/scenarios/3/run", () => HttpResponse.json(RESULT)),
    );

    await user.click(runButton());
    expect(await screen.findByText("Feed model unavailable.")).toBeInTheDocument();

    runFails = false;
    await user.click(runButton());
    expect(await screen.findByText("Source: Current editor assumptions")).toBeInTheDocument();
    expect(screen.queryByText("Feed model unavailable.")).not.toBeInTheDocument();

    runFails = true;
    await user.click(runButton());
    expect(await screen.findByText("Feed model unavailable.")).toBeInTheDocument();

    // A saved-scenario run answers the same question, so it clears the same
    // banner before it starts.
    await user.click(scenarioActions("Plan B").getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Source: Saved scenario “Plan B”")).toBeInTheDocument();
    expect(screen.queryByText("Feed model unavailable.")).not.toBeInTheDocument();
  });
});

describe("SimulationPage scenario writes", () => {
  it("clears a rejected save while its retry is on the wire", async () => {
    let attempts = 0;
    let releaseCreate!: () => void;
    const user = await renderLoaded();
    server.use(
      http.post("/api/simulation/scenarios", async () => {
        attempts += 1;
        if (attempts === 1)
          return HttpResponse.json(
            { detail: "A scenario named “Plan” already exists." },
            { status: 409 },
          );
        await new Promise<void>((resolve) => {
          releaseCreate = resolve;
        });
        return HttpResponse.json(scenarioRow({ id: 11, name: "Plan" }), { status: 201 });
      }),
    );

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    await user.type(screen.getByLabelText("Name *"), "Plan");
    await user.click(screen.getByRole("button", { name: "Save scenario" }));
    expect(
      await screen.findByText("A scenario named “Plan” already exists."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save scenario" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Saving…" })).toBeInTheDocument(),
    );
    // The retry is answering for itself now; the rejected attempt's message
    // must not still be reading as this attempt's verdict.
    expect(
      screen.queryByText("A scenario named “Plan” already exists."),
    ).not.toBeInTheDocument();

    releaseCreate();
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Scenario saved."),
    );
  });

  // Scheduling an event is an edit like any other: the conflict refresh may
  // only replace an editor nobody has touched since the rejected write.
  it("keeps an event added while a conflict refresh was loading", async () => {
    const stored = scenarioRow({ id: 8, name: "Versioned plan", revision: 7 });
    const fresh = {
      ...stored,
      assumptions: { ...DEFAULTS, herd: herd(88) },
      revision: 8,
    };
    let releasePatch!: () => void;
    const user = await renderLoaded({ scenarios: [stored] });
    server.use(
      http.patch("/api/simulation/scenarios/8", async () => {
        await new Promise<void>((resolve) => {
          releasePatch = resolve;
        });
        return HttpResponse.json(
          { detail: "scenario changed concurrently" },
          { status: 409 },
        );
      }),
      http.get("/api/simulation/scenarios/8", () => HttpResponse.json(fresh)),
    );

    await user.click(scenarioActions("Versioned plan").getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Versioned plan" }));
    await waitFor(() => expect(releasePatch).toBeTypeOf("function"));

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const kind = screen.getByLabelText("Kind");
    await user.click(kind);
    await user.click(await screen.findByRole("option", { name: "Sale" }));
    await waitFor(() => expect(kind).toHaveTextContent("Sale"));

    releasePatch();

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("scenario changed concurrently"),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(50);
    expect(screen.getByLabelText("Month")).toHaveValue(12);
    expect(screen.getByLabelText("Kind")).toHaveTextContent("Sale");
  });

  it("re-reads the scenario list after an update and after a conflict refresh", async () => {
    const stored = scenarioRow({ id: 9, name: "Plan A", revision: 1 });
    let patches = 0;
    const user = await renderLoaded({ scenarios: [stored] });
    server.use(
      http.get("/api/simulation/scenarios", () =>
        HttpResponse.json({ items: [stored], total: 1, limit: 20, offset: 0 }),
      ),
      http.patch("/api/simulation/scenarios/9", () => {
        patches += 1;
        if (patches === 1) {
          Object.assign(stored, { name: "Plan A (renamed)", revision: 2 });
          return HttpResponse.json({ ...stored });
        }
        // Someone else got there first, and their name is what the list holds.
        Object.assign(stored, { name: "Plan A (elsewhere)", revision: 3 });
        return HttpResponse.json(
          { detail: "scenario changed concurrently" },
          { status: 409 },
        );
      }),
      http.get("/api/simulation/scenarios/9", () => HttpResponse.json({ ...stored })),
    );

    await user.click(scenarioActions("Plan A").getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Plan A" }));

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Scenario updated."),
    );
    await waitFor(() =>
      expect(within(scenariosCard()).getByText("Plan A (renamed)")).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Update Plan A (renamed)" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "This scenario changed elsewhere. The editor was refreshed to the latest revision; review it before saving again.",
      ),
    );
    await waitFor(() =>
      expect(within(scenariosCard()).getByText("Plan A (elsewhere)")).toBeInTheDocument(),
    );
  });
});

describe("SimulationPage scenario paging", () => {
  // The delete works its landing page out from the count the operator could
  // see, so it moves off the page it just emptied immediately — whatever the
  // list has grown to elsewhere by the time the refreshed count arrives.
  it("re-homes a deleted last page from the count the operator could see", async () => {
    const scenarios = Array.from({ length: 41 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    const user = await renderLoaded({ scenarios });
    server.use(
      http.delete("/api/simulation/scenarios/41", () => {
        scenarios.splice(40, 1);
        // Another operator has been saving scenarios of their own meanwhile.
        for (const n of [1, 2, 3, 4, 5])
          scenarios.push(scenarioRow({ id: 100 + n, name: `Extra ${n}` }));
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 21")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 41")).toBeInTheDocument();

    await user.click(scenarioActions("Plan 41").getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));

    await waitFor(() =>
      expect(
        screen.getByText("Showing 21–40 of 45 saved scenarios"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("Plan 40")).toBeInTheDocument();
    expect(screen.queryByText("Plan 41")).not.toBeInTheDocument();
    expect(screen.queryByText("Extra 1")).not.toBeInTheDocument();
  });
});

describe("SimulationPage comparison lifetime", () => {
  it("drops a rendered comparison when the scenario page turns", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    const user = await renderLoaded({
      scenarios,
      compare: () =>
        HttpResponse.json({
          scenarios: [scenarios[0], scenarios[1]],
          results: [RESULT, RESULT],
        }),
    });

    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    expect(await screen.findByText("Comparison")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("Plan 21")).toBeInTheDocument();
    // The table described the page and selection the operator has just left.
    expect(screen.queryByText("Comparison")).not.toBeInTheDocument();
  });

  it("drops a rendered comparison when a delete refreshes the list", async () => {
    const scenarios = [1, 2, 3].map((id) => scenarioRow({ id, name: `Plan ${id}` }));
    const user = await renderLoaded({
      scenarios,
      compare: () =>
        HttpResponse.json({
          scenarios: [scenarios[0], scenarios[1]],
          results: [RESULT, RESULT],
        }),
    });
    server.use(
      http.delete("/api/simulation/scenarios/3", () => {
        scenarios.splice(2, 1);
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    expect(await screen.findByText("Comparison")).toBeInTheDocument();

    await user.click(scenarioActions("Plan 3").getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));

    await waitFor(() => expect(screen.queryByText("Plan 3")).not.toBeInTheDocument());
    // The comparison was run against a list revision that no longer exists.
    expect(screen.queryByText("Comparison")).not.toBeInTheDocument();
  });

  it("drops a comparison run against a page that vanished while it loaded", async () => {
    let scenarios = Array.from({ length: 21 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    let releasePage!: () => void;
    const user = await renderLoaded({
      onScenarios: (offset) => {
        if (offset !== 20 || releasePage !== undefined) {
          return HttpResponse.json({
            items: scenarios.slice(offset, offset + 20),
            total: scenarios.length,
            limit: 20,
            offset,
          });
        }
        // Another operator empties the tail of the list while this page is on
        // the wire.
        scenarios = scenarios.slice(0, 15);
        return new Promise<Response>((resolve) => {
          releasePage = () =>
            resolve(HttpResponse.json({ items: [], total: 15, limit: 20, offset: 20 }));
        });
      },
      compare: () =>
        HttpResponse.json({
          scenarios: [scenarios[0], scenarios[1]],
          results: [RESULT, RESULT],
        }),
    });

    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(releasePage).toBeTypeOf("function"));

    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    expect(await screen.findByText("Comparison")).toBeInTheDocument();

    releasePage();

    // The page re-homes to the last page that still exists, and the rendered
    // comparison belonged to the selection on the page that vanished.
    expect(await screen.findByText("Plan 15")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByText("Showing 1–15 of 15 saved scenarios"),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("Plan 21")).not.toBeInTheDocument();
    expect(screen.queryByText("Comparison")).not.toBeInTheDocument();
  });
});
