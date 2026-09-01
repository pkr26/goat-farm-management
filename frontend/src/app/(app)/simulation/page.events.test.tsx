/**
 * Simulation page: horizon quick presets, the herd events editor (add /
 * remove / validation / payload wiring / scenario load / pre-load gating),
 * the monthly projection "Events" column, the "Use current herd" breed
 * binding, the numeric-input guards (blank never becomes 0, NaN never
 * reaches the assumptions object), null-safe IRR/BCR rendering, and the
 * Delete-scenario pending state.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

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
  months: [
    monthRow({ events: ["Purchased 10 doe(s) at ₹8,000/head (₹80,000)"] }),
    monthRow({ month: 2, calendar_month: 2, births: 4 }),
  ],
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

interface RunBody {
  assumptions: {
    meta?: { horizon_months?: number };
    sales?: {
      monthly_meat_price_multipliers?: number[];
      festival_sale_months?: number[];
      annual_livestock_price_growth_rate?: number;
    };
    feed?: {
      monthly_green_price_multipliers?: number[];
      annual_feed_price_growth_rate?: number;
    };
    costs?: {
      labour_per_head_threshold?: number;
      operating_cost_growth_rate_annual?: number;
    };
    growth?: { birth_weight_kg?: number; weight_by_age_months?: number[] };
    events?: unknown[];
  };
  monte_carlo: boolean;
  sensitivity: boolean;
  optimization: boolean;
}

/** Breeds + defaults + scenario list; the run POST captures its body. */
function registerApiHandlers(options: {
  scenarios?: unknown[];
  onRun?: (body: RunBody) => void;
  runResult?: unknown;
  defaults?: unknown;
  permissions?: string[];
  calibrationResult?: unknown;
  onCalibration?: (params: URLSearchParams) => void;
} = {}) {
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
    http.get("/api/simulation/herd-snapshot", () =>
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
    ),
    http.get("/api/simulation/calibration", ({ request }) => {
      options.onCalibration?.(new URL(request.url).searchParams);
      return HttpResponse.json(options.calibrationResult ?? {});
    }),
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

describe("SimulationPage herd events", () => {
  it("sets meta.horizon_months via the preset buttons and sends it in the run payload", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "10 yr" }));
    expect(screen.getByLabelText("Horizon Months")).toHaveValue(120);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.meta?.horizon_months).toBe(120);
  });

  it("adds and removes event rows", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(screen.getByText(/No scheduled events/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add event" }));
    expect(screen.getByLabelText("Month")).toHaveValue(12);
    expect(screen.getByLabelText("Count")).toHaveValue(10);
    expect(screen.getByLabelText("Price per head")).toHaveValue(null);

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.queryByLabelText("Month")).not.toBeInTheDocument();
    expect(screen.getByText(/No scheduled events/)).toBeInTheDocument();
  });

  it("blocks the run when an event month exceeds the horizon", async () => {
    let runCalls = 0;
    const user = userEvent.setup();
    await renderLoaded({ onRun: () => (runCalls += 1) });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const monthInput = screen.getByLabelText("Month");
    await user.clear(monthInput);
    await user.type(monthInput, "999");

    expect(
      await screen.findByText("Must be at most 60."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(runCalls).toBe(0);
  });

  it("blocks the run when an event count is zero", async () => {
    let runCalls = 0;
    const user = userEvent.setup();
    await renderLoaded({ onRun: () => (runCalls += 1) });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const countInput = screen.getByLabelText("Count");
    await user.clear(countInput);
    await user.type(countInput, "0");

    expect(
      await screen.findByText("Must be greater than 0."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(runCalls).toBe(0);
  });

  it("includes valid events in the run payload", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const month = screen.getByLabelText("Month");
    await user.clear(month);
    await user.type(month, "60");
    const count = screen.getByLabelText("Count");
    await user.clear(count);
    await user.type(count, "100000");
    await user.type(screen.getByLabelText("Price per head"), "0");
    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.events).toEqual([
      {
        month: 60,
        kind: "purchase",
        animal_class: "doe",
        count: 100000,
        price_per_head: 0,
      },
    ]);
  });

  it("populates the events editor when a scenario is loaded", async () => {
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Expansion",
      notes: "",
      assumptions: {
        ...DEFAULTS,
        events: [
          {
            month: 24,
            kind: "sale",
            animal_class: "male_kid",
            count: 5,
            price_per_head: 2500,
          },
        ],
      },
      revision: 7,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));

    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();
    expect(screen.getByLabelText("Month")).toHaveValue(24);
    expect(screen.getByLabelText("Count")).toHaveValue(5);
    expect(screen.getByLabelText("Price per head")).toHaveValue(2500);
  });

  it("sends and advances the loaded scenario revision on every update", async () => {
    const scenario = {
      id: 8,
      farm_id: 1,
      name: "Versioned plan",
      notes: "",
      assumptions: DEFAULTS,
      revision: 7,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const updates: Array<{ expected_revision?: number; assumptions: unknown }> = [];
    server.use(
      http.patch("/api/simulation/scenarios/:scenarioId", async ({ request }) => {
        const body = (await request.json()) as {
          expected_revision?: number;
          assumptions: unknown;
        };
        updates.push(body);
        return HttpResponse.json({
          ...scenario,
          assumptions: body.assumptions,
          revision: (body.expected_revision ?? 0) + 1,
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));
    const update = screen.getByRole("button", { name: "Update Versioned plan" });
    await user.click(update);
    await waitFor(() => expect(updates).toHaveLength(1));
    expect(updates[0].expected_revision).toBe(7);

    await user.click(update);
    await waitFor(() => expect(updates).toHaveLength(2));
    expect(updates[1].expected_revision).toBe(8);
  });

  it("refreshes a conflicted scenario instead of retrying its stale revision", async () => {
    const scenario = {
      id: 8,
      farm_id: 1,
      name: "Versioned plan",
      notes: "",
      assumptions: DEFAULTS,
      revision: 7,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const fresh = {
      ...scenario,
      assumptions: {
        ...DEFAULTS,
        herd: { ...DEFAULTS.herd, does: 88 },
      },
      revision: 8,
      updated_at: "2026-01-03T00:00:00Z",
    };
    const revisions: number[] = [];
    server.use(
      http.patch("/api/simulation/scenarios/8", async ({ request }) => {
        const body = (await request.json()) as { expected_revision: number };
        revisions.push(body.expected_revision);
        return revisions.length === 1
          ? HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 })
          : HttpResponse.json({ ...fresh, revision: 9 });
      }),
      http.get("/api/simulation/scenarios/8", () => HttpResponse.json(fresh)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Versioned plan" }));
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(88));
    await user.click(screen.getByRole("button", { name: "Update Versioned plan" }));

    await waitFor(() => expect(revisions).toEqual([7, 8]));
  });

  it("does not overwrite edits made while conflict recovery is in flight", async () => {
    const scenario = {
      id: 8,
      farm_id: 1,
      name: "Versioned plan",
      notes: "",
      assumptions: DEFAULTS,
      revision: 7,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const fresh = {
      ...scenario,
      assumptions: {
        ...DEFAULTS,
        herd: { ...DEFAULTS.herd, does: 88 },
      },
      revision: 8,
      updated_at: "2026-01-03T00:00:00Z",
    };
    let releaseUpdate!: () => void;
    let markUpdateStarted!: () => void;
    const updateGate = new Promise<void>((resolve) => {
      releaseUpdate = resolve;
    });
    const updateStarted = new Promise<void>((resolve) => {
      markUpdateStarted = resolve;
    });
    server.use(
      http.patch("/api/simulation/scenarios/8", async () => {
        markUpdateStarted();
        await updateGate;
        return HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 });
      }),
      http.get("/api/simulation/scenarios/8", () => HttpResponse.json(fresh)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Versioned plan" }));
    await updateStarted;
    const does = screen.getByLabelText("Does");
    // One valid DOM edit invokes both the validity callback and the value
    // commit exactly once. Their generation increments must be monotonic: if
    // either moved backwards, the two callbacks would cancel each other and
    // make the conflict refresh mistake this edit for an untouched editor.
    fireEvent.change(does, { target: { value: "77" } });
    expect(does).toHaveValue(77);

    releaseUpdate();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Update Versioned plan" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(77);
  });

  it("does not overwrite a nested edit made while conflict recovery is in flight", async () => {
    const assumptions = {
      ...DEFAULTS,
      risk: { meat_price: { enabled: true, low: 0.8, high: 1.2 } },
    };
    const scenario = {
      id: 8,
      farm_id: 1,
      name: "Nested plan",
      notes: "",
      assumptions,
      revision: 7,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const fresh = {
      ...scenario,
      assumptions: {
        ...assumptions,
        risk: { meat_price: { enabled: true, low: 0.6, high: 1.2 } },
      },
      revision: 8,
    };
    let releaseUpdate!: () => void;
    let markUpdateStarted!: () => void;
    const updateGate = new Promise<void>((resolve) => {
      releaseUpdate = resolve;
    });
    const updateStarted = new Promise<void>((resolve) => {
      markUpdateStarted = resolve;
    });
    server.use(
      http.patch("/api/simulation/scenarios/8", async () => {
        markUpdateStarted();
        await updateGate;
        return HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 });
      }),
      http.get("/api/simulation/scenarios/8", () => HttpResponse.json(fresh)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Nested plan" }));
    await updateStarted;
    await user.click(screen.getByText("Risk"));
    const low = screen.getByLabelText("Low");
    fireEvent.change(low, { target: { value: "0.9" } });
    expect(low).toHaveValue(0.9);

    releaseUpdate();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Update Nested plan" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Low")).toHaveValue(0.9);
  });

  it("does not overwrite an event edit made while conflict recovery is in flight", async () => {
    const originalEvent = {
      month: 12,
      kind: "purchase",
      animal_class: "doe",
      count: 10,
      price_per_head: null,
    };
    const scenario = {
      id: 8,
      farm_id: 1,
      name: "Event plan",
      notes: "",
      assumptions: { ...DEFAULTS, events: [originalEvent] },
      revision: 7,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const fresh = {
      ...scenario,
      assumptions: { ...DEFAULTS, events: [{ ...originalEvent, count: 99 }] },
      revision: 8,
    };
    let releaseUpdate!: () => void;
    let markUpdateStarted!: () => void;
    const updateGate = new Promise<void>((resolve) => {
      releaseUpdate = resolve;
    });
    const updateStarted = new Promise<void>((resolve) => {
      markUpdateStarted = resolve;
    });
    server.use(
      http.patch("/api/simulation/scenarios/8", async () => {
        markUpdateStarted();
        await updateGate;
        return HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 });
      }),
      http.get("/api/simulation/scenarios/8", () => HttpResponse.json(fresh)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Event plan" }));
    await updateStarted;
    const count = screen.getByLabelText("Count");
    fireEvent.change(count, { target: { value: "11" } });
    expect(count).toHaveValue(11);

    releaseUpdate();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Update Event plan" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Count")).toHaveValue(11);
  });

  it("does not overwrite a mixed event removal and editor change during conflict recovery", async () => {
    const originalEvent = {
      month: 12,
      kind: "purchase",
      animal_class: "doe",
      count: 10,
      price_per_head: null,
    };
    const scenario = {
      id: 8,
      farm_id: 1,
      name: "Event fence plan",
      notes: "",
      assumptions: { ...DEFAULTS, events: [originalEvent] },
      revision: 7,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const fresh = {
      ...scenario,
      assumptions: {
        ...DEFAULTS,
        herd: { ...DEFAULTS.herd, does: 88 },
        events: [originalEvent],
      },
      revision: 8,
    };
    let releaseUpdate!: () => void;
    let markUpdateStarted!: () => void;
    const updateGate = new Promise<void>((resolve) => {
      releaseUpdate = resolve;
    });
    const updateStarted = new Promise<void>((resolve) => {
      markUpdateStarted = resolve;
    });
    server.use(
      http.patch("/api/simulation/scenarios/8", async () => {
        markUpdateStarted();
        await updateGate;
        return HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 });
      }),
      http.get("/api/simulation/scenarios/8", () => HttpResponse.json(fresh)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(screen.getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Event fence plan" }));
    await updateStarted;
    await user.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.change(screen.getByLabelText("Start Year Month"), {
      target: { value: "2026-02" },
    });
    expect(screen.getByText(/No scheduled events/)).toBeInTheDocument();
    expect(screen.getByLabelText("Start Year Month")).toHaveValue("2026-02");

    releaseUpdate();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Update Event fence plan" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Start Year Month")).toHaveValue("2026-02");
    expect(screen.getByText(/No scheduled events/)).toBeInTheDocument();
  });

  it("does not let a conflicted first scenario replace a newly loaded second scenario", async () => {
    const first = {
      id: 8,
      farm_id: 1,
      name: "First conflicted plan",
      notes: "",
      assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 61 } },
      revision: 1,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const second = {
      ...first,
      id: 9,
      name: "Second current plan",
      // Keep this whole-editor replacement free of numeric controls. Numeric
      // inputs notify validity when mounted, which independently advances the
      // content epoch and can mask whether the dedicated editor epoch rejected
      // the stale conflict refresh.
      assumptions: { herd: { foundation_flock_state: "open" } },
    };
    const freshFirst = {
      ...first,
      assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 88 } },
      revision: 2,
    };
    let markRefreshStarted!: () => void;
    let releaseRefresh!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });
    server.use(
      http.patch("/api/simulation/scenarios/8", () =>
        HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 }),
      ),
      http.get("/api/simulation/scenarios/8", async () => {
        markRefreshStarted();
        await refreshGate;
        return HttpResponse.json(freshFirst);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [first, second] });

    const firstRow = screen.getByText("First conflicted plan").closest("tr") as HTMLElement;
    const secondRow = screen.getByText("Second current plan").closest("tr") as HTMLElement;
    await user.click(within(firstRow).getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update First conflicted plan" }));
    await refreshStarted;
    await user.click(within(secondRow).getByRole("button", { name: "Load" }));
    expect(screen.getByText("Editing scenario: Second current plan")).toBeInTheDocument();

    releaseRefresh();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Update Second current plan" }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("Editing scenario: Second current plan")).toBeInTheDocument();
    expect(screen.queryByLabelText("Does")).not.toBeInTheDocument();
  });

  it("does not re-arm completed defaults after any kind of editor change", async () => {
    const baselineEvent = {
      month: 12,
      kind: "purchase",
      animal_class: "doe",
      count: 10,
      price_per_head: null,
    };
    const defaults = {
      ...DEFAULTS,
      risk: { meat_price: { enabled: true, low: 0.8, high: 1.2 } },
      events: [baselineEvent],
    };
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded({ defaults }, queryClient);

    // Every response differs so React Query publishes a new data reference and
    // the defaults mirroring effect genuinely re-evaluates. Once the initial
    // defaults have landed, background refetches are cache maintenance rather
    // than permission to replace live editor state.
    let defaultsRefetches = 0;
    server.use(
      http.get("/api/simulation/defaults", () => {
        defaultsRefetches += 1;
        return HttpResponse.json({
          ...defaults,
          meta: {
            ...defaults.meta,
            start_year_month: `2026-${String((defaultsRefetches % 9) + 1).padStart(2, "0")}`,
          },
        });
      }),
    );
    async function refetchDefaults() {
      const before = defaultsRefetches;
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: ["/api/simulation/defaults"] });
      });
      expect(defaultsRefetches).toBe(before + 1);
      // React Query publishes observer changes through its notification
      // scheduler. Let the mirroring effect run before checking that it was
      // correctly disarmed; an immediate assertion can observe the old editor
      // even when a mutant has wrongly re-armed defaults acceptance.
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 50));
      });
    }

    fireEvent.change(screen.getByLabelText("Does"), { target: { value: "77" } });
    await refetchDefaults();
    expect(screen.getByLabelText("Does")).toHaveValue(77);

    await user.click(screen.getByText("Risk"));
    fireEvent.change(screen.getByLabelText("Low"), { target: { value: "0.9" } });
    await refetchDefaults();
    expect(screen.getByLabelText("Low")).toHaveValue(0.9);

    await user.click(screen.getByRole("button", { name: "Add event" }));
    expect(screen.getAllByLabelText("Count")).toHaveLength(2);
    await refetchDefaults();
    expect(screen.getAllByLabelText("Count")).toHaveLength(2);

    fireEvent.change(screen.getAllByLabelText("Count")[0], { target: { value: "11" } });
    await refetchDefaults();
    expect(screen.getAllByLabelText("Count")[0]).toHaveValue(11);

    await user.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    expect(screen.getAllByLabelText("Count")).toHaveLength(1);
    await refetchDefaults();
    expect(screen.getAllByLabelText("Count")).toHaveLength(1);
    expect(screen.getByLabelText("Count")).toHaveValue(11);
  });

  it("does not let a late update rebind the editor after another scenario is loaded", async () => {
    const first = {
      id: 8,
      farm_id: 1,
      name: "First plan",
      notes: "",
      assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 61 } },
      revision: 1,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const second = {
      ...first,
      id: 9,
      name: "Second plan",
      assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 92 } },
    };
    let releaseUpdate!: () => void;
    let markUpdateStarted!: () => void;
    const updateGate = new Promise<void>((resolve) => {
      releaseUpdate = resolve;
    });
    const updateStarted = new Promise<void>((resolve) => {
      markUpdateStarted = resolve;
    });
    server.use(
      http.patch("/api/simulation/scenarios/8", async () => {
        markUpdateStarted();
        await updateGate;
        return HttpResponse.json({ ...first, revision: 2 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [first, second] });

    const firstRow = screen.getByText("First plan").closest("tr") as HTMLElement;
    const secondRow = screen.getByText("Second plan").closest("tr") as HTMLElement;
    await user.click(within(firstRow).getByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update First plan" }));
    await updateStarted;

    await user.click(within(secondRow).getByRole("button", { name: "Load" }));
    expect(screen.getByText("Editing scenario: Second plan")).toBeInTheDocument();
    expect(screen.getByLabelText("Does")).toHaveValue(92);

    releaseUpdate();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Update Second plan" })).toBeEnabled(),
    );
    expect(screen.getByText("Editing scenario: Second plan")).toBeInTheDocument();
    expect(screen.getByLabelText("Does")).toHaveValue(92);
  });

  // REGRESSION — clicking "Add event" during the initial defaults fetch
  // flipped acceptDefaultsRef, so the arriving payload was dropped and the
  // editor stayed permanently stuck on "Loading defaults…" with Run disabled.
  it("cannot cancel the pending defaults auto-load by adding an event early", async () => {
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
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);

    const addEvent = await screen.findByRole("button", { name: "Add event" });
    await waitFor(() => expect(releaseDefaults).toBeTypeOf("function"));
    expect(addEvent).toBeDisabled();
    await user.click(addEvent);

    releaseDefaults();
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
    // With the defaults loaded the editor is live again.
    await user.click(addEvent);
    expect(screen.getByLabelText("Month")).toHaveValue(12);
  });

  it("freezes the editor while an explicit defaults load is in flight", async () => {
    let calls = 0;
    let releaseDefaults!: () => void;
    let markDefaultsStarted!: () => void;
    const defaultsGate = new Promise<void>((resolve) => {
      releaseDefaults = resolve;
    });
    const defaultsStarted = new Promise<void>((resolve) => {
      markDefaultsStarted = resolve;
    });
    registerApiHandlers();
    server.use(
      http.get("/api/simulation/defaults", async () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json(DEFAULTS);
        markDefaultsStarted();
        await defaultsGate;
        return HttpResponse.json({
          ...DEFAULTS,
          herd: { ...DEFAULTS.herd, does: 55 },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByLabelText("Does")).toHaveValue(50);

    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await defaultsStarted;
    expect(screen.getByLabelText("Does")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add event" })).toBeDisabled();

    releaseDefaults();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(55));
    expect(screen.getByLabelText("Does")).toBeEnabled();
  });

  it("shows event log lines in the monthly projection table", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(
      await screen.findByText("Purchased 10 doe(s) at ₹8,000/head (₹80,000)"),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage use current herd", () => {
  it("keeps the editor intact and surfaces the API detail when a snapshot fails", async () => {
    toastMocks.error.mockClear();
    const user = userEvent.setup();
    await renderLoaded();
    server.use(
      http.get("/api/simulation/herd-snapshot", () =>
        HttpResponse.json({ detail: "snapshot unavailable" }, { status: 503 }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(50));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("snapshot unavailable"),
    );
    expect(screen.queryByText(/Loaded current herd/)).not.toBeInTheDocument();
  });

  it("does not let a late herd snapshot overwrite a subsequently loaded scenario", async () => {
    toastMocks.error.mockClear();
    let releaseSnapshot!: () => void;
    let markSnapshotStarted!: () => void;
    const snapshotGate = new Promise<void>((resolve) => {
      releaseSnapshot = resolve;
    });
    const snapshotStarted = new Promise<void>((resolve) => {
      markSnapshotStarted = resolve;
    });
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Later plan",
      notes: "",
      assumptions: {
        ...DEFAULTS,
        herd: { ...DEFAULTS.herd, does: 99 },
      },
      valid: true,
      validation_error: null,
      revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    registerApiHandlers({ scenarios: [scenario] });
    server.use(
      http.get("/api/simulation/herd-snapshot", async () => {
        markSnapshotStarted();
        await snapshotGate;
        return HttpResponse.json({
          does: 48,
          bucks: 3,
          f_kids: 4,
          f_weaners: 5,
          f_growers: 6,
          m_kids: 3,
          m_weaners: 2,
          m_growers: 1,
          total_head: 72,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByLabelText("Does")).toHaveValue(50);

    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await snapshotStarted;
    const scenarioRow = screen.getByText("Later plan").closest("tr") as HTMLElement;
    await user.click(within(scenarioRow).getByRole("button", { name: "Load" }));
    expect(screen.getByLabelText("Does")).toHaveValue(99);

    releaseSnapshot();
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        expect.stringMatching(/editor was reloaded while the herd snapshot was loading/i),
      ),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(99);
    expect(screen.getByText("Editing scenario: Later plan")).toBeInTheDocument();
  });

  // REGRESSION — the herd snapshot was keyed on the live breed dropdown, so
  // changing the dropdown without reloading defaults imported head counts
  // bucketed by the new breed's age-at-first-breeding thresholds into an
  // assumption set that still belonged to the previously loaded breed.
  it("snapshots the breed the loaded assumptions belong to, not the dropdown", async () => {
    const snapshotBreeds: (string | null)[] = [];
    registerApiHandlers();
    server.use(
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({
          breeds: ["osmanabadi", "sirohi"],
          systems: ["stall_fed"],
        }),
      ),
      http.get("/api/simulation/herd-snapshot", ({ request }) => {
        snapshotBreeds.push(new URL(request.url).searchParams.get("breed"));
        return HttpResponse.json({
          does: 48,
          bucks: 3,
          f_kids: 4,
          f_weaners: 5,
          f_growers: 6,
          m_kids: 3,
          m_weaners: 2,
          m_growers: 1,
          total_head: 72,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    // Change the dropdown without clicking "Load defaults": the editor still
    // holds the osmanabadi assumptions.
    await user.click(screen.getByLabelText("Breed"));
    await user.click(await screen.findByRole("option", { name: "sirohi" }));
    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    expect(await screen.findByLabelText("Does")).toHaveValue(48);
    expect(snapshotBreeds).toEqual(["osmanabadi"]);
  });

  it("keeps a newer herd import when an older calibration resolves late", async () => {
    let releaseCalibration!: () => void;
    let markCalibrationStarted!: () => void;
    const calibrationGate = new Promise<void>((resolve) => {
      releaseCalibration = resolve;
    });
    const calibrationStarted = new Promise<void>((resolve) => {
      markCalibrationStarted = resolve;
    });
    registerApiHandlers({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get("/api/simulation/calibration", async () => {
        markCalibrationStarted();
        await calibrationGate;
        return HttpResponse.json({
          assumptions: {
            ...DEFAULTS,
            herd: { ...DEFAULTS.herd, does: 73, bucks: 3 },
          },
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
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await calibrationStarted;
    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));

    releaseCalibration();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Calibrate from farm" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(48);
  });

  it("discards calibration when its visible parameters change in flight", async () => {
    let releaseCalibration!: () => void;
    let markCalibrationStarted!: () => void;
    const calibrationGate = new Promise<void>((resolve) => {
      releaseCalibration = resolve;
    });
    const calibrationStarted = new Promise<void>((resolve) => {
      markCalibrationStarted = resolve;
    });
    registerApiHandlers({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({
          breeds: ["osmanabadi", "sirohi"],
          systems: ["stall_fed"],
        }),
      ),
      http.get("/api/simulation/calibration", async () => {
        markCalibrationStarted();
        await calibrationGate;
        return HttpResponse.json({
          assumptions: {
            ...DEFAULTS,
            herd: { ...DEFAULTS.herd, does: 73 },
          },
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
    await user.click(screen.getByLabelText("Breed"));
    await user.click(await screen.findByRole("option", { name: "sirohi" }));
    releaseCalibration();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Calibrate from farm" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Breed")).toHaveTextContent("sirohi");
    expect(screen.getByLabelText("Does")).toHaveValue(50);
  });

  it("does not swallow an in-flight defaults fallback when calibration fails", async () => {
    toastMocks.error.mockClear();
    let defaultsCalls = 0;
    let releaseDefaults!: () => void;
    let markDefaultsStarted!: () => void;
    let releaseCalibration!: () => void;
    let markCalibrationStarted!: () => void;
    const defaultsGate = new Promise<void>((resolve) => {
      releaseDefaults = resolve;
    });
    const defaultsStarted = new Promise<void>((resolve) => {
      markDefaultsStarted = resolve;
    });
    const calibrationGate = new Promise<void>((resolve) => {
      releaseCalibration = resolve;
    });
    const calibrationStarted = new Promise<void>((resolve) => {
      markCalibrationStarted = resolve;
    });
    registerApiHandlers({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get("/api/simulation/defaults", async () => {
        defaultsCalls += 1;
        if (defaultsCalls === 1) return HttpResponse.json(DEFAULTS);
        markDefaultsStarted();
        await defaultsGate;
        return HttpResponse.json({
          ...DEFAULTS,
          herd: { ...DEFAULTS.herd, does: 55 },
        });
      }),
      http.get("/api/simulation/calibration", async () => {
        markCalibrationStarted();
        await calibrationGate;
        return HttpResponse.json({ detail: "calibration unavailable" }, { status: 503 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await defaultsStarted;
    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await calibrationStarted;

    releaseDefaults();
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(55));
    releaseCalibration();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Calibrate from farm" })).toBeEnabled(),
    );
    expect(toastMocks.error).toHaveBeenCalledWith("calibration unavailable");
    expect(screen.getByLabelText("Does")).toHaveValue(55);
  });
});

describe("SimulationPage numeric input guards", () => {
  it("accepts only real simulation start months inside the supported year range", async () => {
    await renderLoaded();
    const start = screen.getByLabelText("Start Year Month");

    fireEvent.change(start, { target: { value: "2201-01" } });
    expect(
      screen.getAllByText(/real month from 1900-01 to 2200-12/).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    fireEvent.change(start, { target: { value: "2200-12" } });
    expect(
      screen.queryByText("Start year month must be a real month from 1900-01 to 2200-12."),
    ).not.toBeInTheDocument();
    expect(start).toHaveValue("2200-12");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();

    fireEvent.change(start, { target: { value: "1899-12" } });
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("marks a required assumption blank and blocks the run without writing 0", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
    });

    const rateInput = screen.getByLabelText("Interest Rate Annual");
    expect(rateInput).toHaveValue(0.12);

    await user.clear(rateInput);
    // Blank remains visibly invalid instead of silently becoming zero.
    expect(rateInput).toHaveValue(null);
    await user.tab();
    expect(rateInput).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    expect(captured.body).toBeNull();
  });

  it("enforces integer, inclusive-minimum, and inclusive-maximum scalar bounds", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const horizon = screen.getByLabelText("Horizon Months");

    await user.clear(horizon);
    await user.type(horizon, "12.5");
    expect(screen.getByText("Enter a whole number.")).toBeInTheDocument();
    expect(horizon).toHaveAttribute("aria-invalid", "true");

    await user.clear(horizon);
    await user.type(horizon, "11");
    expect(screen.getByText("Must be at least 12.")).toBeInTheDocument();

    await user.clear(horizon);
    await user.type(horizon, "12");
    expect(screen.queryByText("Must be at least 12.")).not.toBeInTheDocument();

    await user.clear(horizon);
    await user.type(horizon, "240");
    expect(screen.queryByText("Must be at most 240.")).not.toBeInTheDocument();
    expect(horizon).not.toHaveAttribute("aria-invalid");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("marks a required event month blank and blocks the run", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const monthInput = screen.getByLabelText("Month");
    await user.clear(monthInput);

    expect(monthInput).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    expect(captured.body).toBeNull();
  });

  it("clears price per head back to null (auto) instead of storing 0", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const priceInput = screen.getByLabelText("Price per head");
    await user.type(priceInput, "2500");
    await user.clear(priceInput);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.events?.[0]).toMatchObject({
      price_per_head: null,
    });
  });

  it("keeps an invalid event draft blocked when current herd assumptions are loaded", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const count = screen.getByLabelText("Count");
    await user.clear(count);
    await user.type(count, "0");
    expect(await screen.findByText("Must be greater than 0.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    expect(count).toHaveValue(0);
    expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("a horizon preset replaces an invalid draft and clears its run gate", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const horizon = screen.getByLabelText("Horizon Months");
    await user.clear(horizon);
    await user.type(horizon, "999");
    expect(await screen.findByText("Must be at most 240.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "10 yr" }));

    expect(screen.getByLabelText("Horizon Months")).toHaveValue(120);
    expect(screen.queryByText("Must be at most 240.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("removing another event preserves an invalid draft and its run gate", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const counts = screen.getAllByLabelText("Count");
    await user.clear(counts[1]);
    expect(await screen.findByText("A value is required.")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);

    expect(screen.getAllByLabelText("Count")).toHaveLength(1);
    expect(screen.getByLabelText("Count")).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("keeps loaded and newly added event identities unique across removals", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: [
          {
            month: 6,
            kind: "purchase",
            animal_class: "doe",
            count: 5,
            price_per_head: null,
          },
        ],
      },
    });
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    expect(screen.getAllByLabelText("Count")).toHaveLength(3);

    // The third row's validation belongs to its stable event key. Removing the
    // loaded first row must not clear that gate through a reused/colliding key.
    await user.clear(screen.getAllByLabelText("Count")[2]);
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);

    expect(screen.getAllByLabelText("Count")).toHaveLength(2);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

describe("SimulationPage results and scenario management", () => {
  it("renders a dash for null IRR/BCR (backend contract change)", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      runResult: {
        ...RESULT,
        metrics: { ...RESULT.metrics, irr: null, bcr: null },
      },
    });

    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    // NPV still formats; IRR/BCR fall back to the dash placeholder.
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    for (const label of ["IRR", "BCR"]) {
      const card = screen.getByText(label).closest("div.rounded-xl");
      expect(card).not.toBeNull();
      expect(within(card as HTMLElement).getByText("—")).toBeInTheDocument();
    }
  });

  it("disables the Delete button while the delete mutation is in flight", async () => {
    let deleteCalls = 0;
    let resolveDelete: (() => void) | null = null;
    server.use(
      http.delete(
        "/api/simulation/scenarios/:scenarioId",
        () =>
          new Promise((resolve) => {
            deleteCalls += 1;
            resolveDelete = () =>
              resolve(new HttpResponse(null, { status: 204 }));
          }),
      ),
    );
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Old plan",
      notes: "",
      assumptions: DEFAULTS,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    const deleteButton = await screen.findByRole("button", { name: "Delete" });
    const runButton = within(screen.getByRole("navigation", { name: "Simulation sections" })).getByRole("button", { name: "Run" });
    await user.click(deleteButton);
    // The confirmation dialog names the scenario before anything is deleted.
    expect(
      await screen.findByRole("heading", { name: "Delete scenario?" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete scenario" }));

    // Mutation in flight: a second click can't fire a duplicate DELETE.
    expect(deleteButton).toBeDisabled();
    expect(runButton).toBeDisabled();
    // Read-only loading stays available, but a late successful deletion must
    // clear this now-orphaned editor binding.
    await user.click(screen.getByRole("button", { name: "Load" }));
    expect(screen.getByText("Editing scenario: Old plan")).toBeInTheDocument();

    resolveDelete!();
    await waitFor(() => expect(deleteCalls).toBe(1));
    await waitFor(() => expect(deleteButton).toBeEnabled());
    expect(screen.queryByText("Editing scenario: Old plan")).not.toBeInTheDocument();
  });

  it("keeps the scenario when the delete confirmation is cancelled", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete("/api/simulation/scenarios/7", () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Kept plan",
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    await user.click(await screen.findByRole("button", { name: "Delete" }));
    // The dialog names the scenario about to be destroyed.
    expect(
      await screen.findByRole("heading", { name: "Delete scenario?" }),
    ).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByText("Kept plan")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("heading", { name: "Delete scenario?" })).not.toBeInTheDocument();
    expect(await screen.findByText("Kept plan")).toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });

  it("clears a deleted scenario from editor, result provenance, and comparison selection", async () => {
    server.use(
      http.post("/api/simulation/scenarios/7/run", () => HttpResponse.json(RESULT)),
      http.delete(
        "/api/simulation/scenarios/7",
        () => new HttpResponse(null, { status: 204 }),
      ),
    );
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Disposable plan",
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });
    const row = screen.getByText("Disposable plan").closest("tr") as HTMLElement;

    await user.click(screen.getByLabelText("Compare Disposable plan"));
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Load" }));
    await user.click(within(row).getByRole("button", { name: "Run" }));
    expect(
      await screen.findByText("Source: Saved scenario “Disposable plan”"),
    ).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));

    await waitFor(() =>
      expect(
        screen.queryByText("Source: Saved scenario “Disposable plan”"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("Editing scenario: Disposable plan")).not.toBeInTheDocument();
    expect(screen.getByText(/0 selected/)).toBeInTheDocument();
  });

  it("re-homes an externally emptied last page instead of leaving a false empty page", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) => ({
      id: index + 1,
      farm_id: 1,
      name: `Concurrent plan ${index + 1}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded({ scenarios }, queryClient);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Concurrent plan 21")).toBeInTheDocument();

    scenarios.splice(0, scenarios.length);
    await act(async () => {
      await queryClient.invalidateQueries();
    });

    expect(await screen.findByText("No saved scenarios yet.")).toBeInTheDocument();
    expect(screen.queryByText(/page no longer exists/)).not.toBeInTheDocument();
  });
});

describe("SimulationPage advanced financial controls", () => {
  it("requires every underlying read permission before exposing farm calibration", async () => {
    await renderLoaded({
      permissions: [...MANAGE_PERMS, "animals.view"],
    });

    expect(
      screen.queryByRole("button", { name: "Calibrate from farm" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Calibration history")).not.toBeInTheDocument();
  });

  it("enforces the backend labour-scaling ceiling without using the herd-size cap", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        costs: { labour_per_head_threshold: 75 },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Costs"));
    const threshold = screen.getByLabelText("Labour Per Head Threshold");
    await user.clear(threshold);
    await user.type(threshold, "1000000000000001");
    expect(
      screen.getByText("Must be at most 1000000000000000."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    await user.clear(threshold);
    await user.type(threshold, "100001");
    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.costs?.labour_per_head_threshold).toBe(100001);
  });

  it("enforces financing relationships at their exact legal boundaries", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        finance: {
          loan_fraction_of_project_cost: 0.8,
          subsidy_fraction: 0.1,
          loan_term_months: 12,
          moratorium_months: 11,
        },
      },
    });

    await user.click(screen.getByText("Finance"));
    const subsidy = screen.getByLabelText("Subsidy Fraction");
    await user.clear(subsidy);
    await user.type(subsidy, "0.3");
    expect(
      screen.getByText("Loan fraction plus subsidy fraction must not exceed 1."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    // Exactly 100% combined financing is legal.
    await user.clear(subsidy);
    await user.type(subsidy, "0.2");
    expect(
      screen.queryByText("Loan fraction plus subsidy fraction must not exceed 1."),
    ).not.toBeInTheDocument();

    const moratorium = screen.getByLabelText("Moratorium Months");
    await user.clear(moratorium);
    await user.type(moratorium, "12");
    expect(
      screen.getByText("Moratorium must be shorter than the loan term."),
    ).toBeInTheDocument();

    await user.clear(moratorium);
    await user.type(moratorium, "11");
    expect(
      screen.queryByText("Moratorium must be shorter than the loan term."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("requires a positive planned capacity only when that basis is selected", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        costs: { capacity_basis: "projected_peak", planned_capacity_head: 0 },
      },
    });

    await user.click(screen.getByText("Costs"));
    await user.click(screen.getByLabelText("Capacity Basis"));
    await user.click(await screen.findByRole("option", { name: "Planned capacity" }));
    expect(
      screen.getByText(
        "Planned capacity must be greater than 0 when the capacity basis is planned.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    const capacity = screen.getByLabelText("Planned Capacity Head");
    await user.clear(capacity);
    await user.type(capacity, "1");
    expect(
      screen.queryByText(
        "Planned capacity must be greater than 0 when the capacity basis is planned.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("enforces optimization range ordering while permitting equal endpoints", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        optimization: {
          objective: "balanced",
          doe_scale_low: 0.75,
          doe_scale_high: 1.25,
        },
      },
    });

    const low = screen.getByLabelText("Doe Scale Low");
    await user.clear(low);
    await user.type(low, "1.5");
    expect(
      screen.getByText("Herd scale low must be less than or equal to herd scale high (measured in does)."),
    ).toBeInTheDocument();

    await user.clear(low);
    await user.type(low, "1.25");
    expect(
      screen.queryByText("Herd scale low must be less than or equal to herd scale high (measured in does)."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("enforces growth-curve ordering and both adult-weight floors", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
          weight_by_age_months: [
            2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5,
            24.5, 26.5,
          ],
        },
      },
    });

    await user.click(screen.getByText("Growth"));
    const birthWeight = screen.getByLabelText("Birth Weight Kg");
    await user.clear(birthWeight);
    await user.type(birthWeight, "5");
    expect(
      screen.getByText(/Weight by age months must not decrease from birth/),
    ).toBeInTheDocument();

    // Equality with month one is legal (nondecreasing, not strictly increasing).
    await user.clear(birthWeight);
    await user.type(birthWeight, "4.5");
    expect(
      screen.queryByText(/Weight by age months must not decrease from birth/),
    ).not.toBeInTheDocument();

    const adultDoe = screen.getByLabelText("Adult Weight Doe Kg");
    await user.clear(adultDoe);
    await user.type(adultDoe, "26");
    expect(
      screen.getByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).toBeInTheDocument();

    await user.clear(adultDoe);
    await user.type(adultDoe, "26.5");
    expect(
      screen.queryByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).not.toBeInTheDocument();

    const adultBuck = screen.getByLabelText("Adult Weight Buck Kg");
    await user.clear(adultBuck);
    await user.type(adultBuck, "26");
    expect(
      screen.getByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).toBeInTheDocument();

    await user.clear(adultBuck);
    await user.type(adultBuck, "26.5");
    expect(
      screen.queryByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("requires every enabled risk spread to bracket the base multiplier", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        risk: {
          meat_price: { enabled: true, low: 0.8, high: 1.2 },
        },
      },
    });

    await user.click(screen.getByText("Risk"));
    const low = screen.getByLabelText("Low");
    const high = screen.getByLabelText("High");
    await user.clear(low);
    await user.type(low, "1.01");
    expect(screen.getByText("Meat Price low and high must bracket 1.")).toBeInTheDocument();

    await user.clear(low);
    await user.type(low, "1");
    expect(
      screen.queryByText("Meat Price low and high must bracket 1."),
    ).not.toBeInTheDocument();

    await user.clear(high);
    await user.type(high, "0.99");
    expect(screen.getByText("Meat Price low and high must bracket 1.")).toBeInTheDocument();

    await user.clear(high);
    await user.type(high, "1");
    expect(
      screen.queryByText("Meat Price low and high must bracket 1."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("keeps birth weight synchronized with age zero in the growth curve", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
          sale_age_months: 12,
          weight_by_age_months: [
            2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5,
            24.5, 26.5,
          ],
        },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Growth"));
    const birthWeight = screen.getByLabelText("Birth Weight Kg");
    await user.clear(birthWeight);
    await user.type(birthWeight, "3.1");
    expect((screen.getByLabelText(/Weight By Age Months/) as HTMLInputElement).value).toMatch(
      /^3\.1,/,
    );

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(captured.body?.assumptions.growth?.birth_weight_kg).toBe(3.1);
    expect(captured.body?.assumptions.growth?.weight_by_age_months?.[0]).toBe(3.1);
  });

  it("validates and sends seasonal arrays and explicit festival months", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        sales: {
          monthly_meat_price_multipliers: Array(12).fill(1),
          festival_sale_months: [],
          annual_livestock_price_growth_rate: 0,
        },
        feed: {
          monthly_green_price_multipliers: Array(12).fill(1),
          annual_feed_price_growth_rate: 0,
        },
        costs: { operating_cost_growth_rate_annual: 0 },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Sales"));
    const meatSeasonality = screen.getByLabelText(/Monthly Meat Price Multipliers/);
    await user.clear(meatSeasonality);
    await user.type(meatSeasonality, "1,1,1,1,1,1,1,1,1,1,1");
    expect(
      screen.getByText("Enter exactly 12 monthly multipliers (Jan-Dec)."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    await user.clear(meatSeasonality);
    await user.type(meatSeasonality, "1,1.01,1,1,1,1,1,1,1,1,1,0.99");
    await user.type(screen.getByLabelText(/Festival Sale Months/), "12,24");
    const livestockGrowth = screen.getByLabelText("Annual Livestock Price Growth Rate");
    await user.clear(livestockGrowth);
    await user.type(livestockGrowth, "-0.05");

    await user.click(screen.getByText("Feed"));
    const greenSeasonality = screen.getByLabelText(/Monthly Green Price Multipliers/);
    await user.clear(greenSeasonality);
    await user.type(greenSeasonality, "1,1,1,1,1,1.1,1.1,1,1,1,1,1");
    const feedGrowth = screen.getByLabelText("Annual Feed Price Growth Rate");
    await user.clear(feedGrowth);
    await user.type(feedGrowth, "-0.03");

    await user.click(screen.getByText("Costs"));
    const costGrowth = screen.getByLabelText("Operating Cost Growth Rate Annual");
    await user.clear(costGrowth);
    await user.type(costGrowth, "-0.02");

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.sales).toMatchObject({
      monthly_meat_price_multipliers: [1, 1.01, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0.99],
      festival_sale_months: [12, 24],
      annual_livestock_price_growth_rate: -0.05,
    });
    expect(captured.body?.assumptions.feed?.monthly_green_price_multipliers).toEqual([
      1, 1, 1, 1, 1, 1.1, 1.1, 1, 1, 1, 1, 1,
    ]);
    expect(captured.body?.assumptions.feed?.annual_feed_price_growth_rate).toBe(-0.03);
    expect(captured.body?.assumptions.costs?.operating_cost_growth_rate_annual).toBe(-0.02);
  });

  it("rejects malformed, out-of-range, duplicate, and decreasing array inputs", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
          weight_by_age_months: [
            2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5,
            24.5, 26.5,
          ],
        },
        sales: {
          monthly_meat_price_multipliers: Array(12).fill(1),
          festival_sale_months: [],
        },
      },
    });

    await user.click(screen.getByText("Sales"));
    const monthly = screen.getByLabelText(/Monthly Meat Price Multipliers/);
    await user.clear(monthly);
    await user.type(monthly, "1,,1,1,1,1,1,1,1,1,1,1");
    expect(screen.getByText("Enter only comma-separated numbers.")).toBeInTheDocument();

    await user.clear(monthly);
    await user.type(monthly, "1,x,1,1,1,1,1,1,1,1,1,1");
    expect(screen.getByText("Enter only comma-separated numbers.")).toBeInTheDocument();

    await user.clear(monthly);
    await user.type(monthly, "0,1,1,1,1,1,1,1,1,1,1,1");
    expect(screen.getByText("Every entry must be greater than 0.")).toBeInTheDocument();

    await user.clear(monthly);
    await user.type(monthly, "11,1,1,1,1,1,1,1,1,1,1,1");
    expect(screen.getByText("Every entry must be at most 10.")).toBeInTheDocument();

    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.type(festivals, "1, 1.5");
    expect(
      screen.getByText("Every simulation months entry must be a whole number."),
    ).toBeInTheDocument();

    await user.clear(festivals);
    await user.type(festivals, "1, 0");
    expect(screen.getByText("Every entry must be at least 1.")).toBeInTheDocument();

    await user.clear(festivals);
    await user.type(festivals, "61");
    expect(screen.getByText("Every entry must be at most 60.")).toBeInTheDocument();

    await user.clear(festivals);
    await user.type(festivals, "12, 12");
    expect(
      screen.getByText("Simulation Months must not contain duplicates."),
    ).toBeInTheDocument();

    await user.clear(festivals);
    await user.type(
      festivals,
      Array.from({ length: 41 }, (_, index) => String(index + 1)).join(","),
    );
    expect(
      screen.getByText("Enter at most 40 simulation months."),
    ).toBeInTheDocument();

    await user.clear(festivals);
    await user.type(festivals, "1, 60");
    expect(screen.queryByText(/simulation months entry/)).not.toBeInTheDocument();
    expect(festivals).not.toHaveAttribute("aria-invalid");

    await user.click(screen.getByText("Growth"));
    const curve = screen.getByLabelText(/Weight By Age Months/);
    await user.clear(curve);
    await user.type(curve, "2.5,4.5,6.5,6,10.5,12.5,14.5,16.5,18.5,20.5,22.5,24.5,26.5");
    expect(
      screen.getByText("Weights (Ages 0-12) must not decrease."),
    ).toBeInTheDocument();

    await user.clear(curve);
    await user.type(curve, "2.5,4.5,4.5,8.5,10.5,12.5,14.5,16.5,18.5,20.5,22.5,24.5,26.5");
    expect(
      screen.queryByText("Weights (Ages 0-12) must not decrease."),
    ).not.toBeInTheDocument();

    await user.clear(monthly);
    await user.type(monthly, "10,1,1,1,1,1,1,1,1,1,1,1");
    expect(screen.queryByText(/Every entry must be/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("does not commit an invalid array draft into the displayed result identity", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        sales: {
          monthly_meat_price_multipliers: Array(12).fill(1),
          festival_sale_months: [],
          annual_livestock_price_growth_rate: 0,
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();

    await user.click(screen.getByText("Sales"));
    const seasonality = screen.getByLabelText(/Monthly Meat Price Multipliers/);
    await user.clear(seasonality);
    await user.type(seasonality, "1,1,1,1,1,1,1,1,1,1,1");

    expect(
      screen.getByText("Enter exactly 12 monthly multipliers (Jan-Dec)."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/These results do not match the current editor assumptions/),
    ).not.toBeInTheDocument();
  });

  it("blocks a run when initial fodder exceeds storage and recovers after correction", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        feed: {
          initial_fodder_stock_kg_dm: 0,
          fodder_storage_capacity_kg_dm: 100,
        },
      },
    });

    await user.click(screen.getByText("Feed"));
    const initialStock = screen.getByLabelText("Initial Fodder Stock Kg Dm");
    await user.clear(initialStock);
    await user.type(initialStock, "200");

    expect(
      screen.getByText("Initial fodder stock must fit within fodder storage capacity."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    const capacity = screen.getByLabelText("Fodder Storage Capacity Kg Dm");
    await user.clear(capacity);
    await user.type(capacity, "300");
    await waitFor(() =>
      expect(
        screen.queryByText(
          "Initial fodder stock must fit within fodder storage capacity.",
        ),
      ).not.toBeInTheDocument(),
    );
    await user.clear(initialStock);
    await user.type(initialStock, "300");
    expect(
      screen.queryByText(
        "Initial fodder stock must fit within fodder storage capacity.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("calibrates assumptions from permitted farm records and shows the evidence", async () => {
    const captured: { breed?: string; system?: string; lookback?: string } = {};
    const user = userEvent.setup();
    await renderLoaded({
      permissions: CALIBRATION_PERMS,
      onCalibration: (params) => {
        captured.breed = params.get("breed") ?? undefined;
        captured.system = params.get("system") ?? undefined;
        captured.lookback = params.get("lookback_months") ?? undefined;
      },
      calibrationResult: {
        assumptions: {
          ...DEFAULTS,
          herd: { ...DEFAULTS.herd, does: 73, bucks: 3 },
        },
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
        ],
        warnings: ["Feed invoice history was too sparse for a reliable price estimate."],
        coverage_score: 0.72,
        reference_date: "2026-08-10",
        lookback_months: 24,
      },
    });

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    expect(await screen.findByText("Animal registry")).toBeInTheDocument();
    expect(screen.getByText("Herd / Does")).toBeInTheDocument();
    expect(screen.getByLabelText("Does")).toHaveValue(73);
    expect(screen.getByText("72.0% coverage")).toBeInTheDocument();
    expect(
      screen.getByText("Feed invoice history was too sparse for a reliable price estimate."),
    ).toBeInTheDocument();
    expect(captured).toEqual({
      breed: "osmanabadi",
      system: "stall_fed",
      lookback: "24",
    });
  });

  it("sends the optimization flag and never recommends an infeasible candidate", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
      runResult: {
        ...RESULT,
        optimization: {
          objective: "balanced",
          evaluated_candidates: 24,
          feasible_candidates: 0,
          baseline: {
            rank: 0,
            starting_does: 50,
            starting_bucks: 2,
            max_breeding_does: 100,
            sale_age_months: 12,
            female_retention_fraction: 0.5,
            loan_fraction: 0.5,
            project_cost: 500000,
            capacity_places: 58,
            projected_peak_head: 70,
            npv: -25000,
            irr: null,
            min_dscr: 0.7,
            minimum_cash_balance: -30000,
            funding_gap: 30000,
            feasible: false,
            constraint_violations: [
              "Minimum DSCR is below 1.20",
              "Projected peak exceeds funded capacity",
            ],
          },
          recommended: null,
          alternatives: [],
        },
      },
    });

    await user.click(screen.getByRole("checkbox", { name: "Optimization" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(
      await screen.findByText(/No evaluated candidate satisfies every financing/),
    ).toBeInTheDocument();
    expect(screen.getByText("Infeasible")).toBeInTheDocument();
    expect(screen.getByText(/Projected peak exceeds funded capacity/)).toBeInTheDocument();
    expect(captured.body?.optimization).toBe(true);
  });

  it("renders Monte Carlo liquidity probabilities and annual path bands", async () => {
    const user = userEvent.setup();
    const band = {
      p5: Array.from({ length: 24 }, (_, index) => 40 + index),
      p25: Array.from({ length: 24 }, (_, index) => 45 + index),
      p50: Array.from({ length: 24 }, (_, index) => 50 + index),
      p75: Array.from({ length: 24 }, (_, index) => 55 + index),
      p95: Array.from({ length: 24 }, (_, index) => 60 + index),
    };
    const liquidityBand = {
      p5: Array.from({ length: 24 }, (_, index) => -10000 + index * 1000),
      p25: Array.from({ length: 24 }, (_, index) => index * 1000),
      p50: Array.from({ length: 24 }, (_, index) => 10000 + index * 1000),
      p75: Array.from({ length: 24 }, (_, index) => 20000 + index * 1000),
      p95: Array.from({ length: 24 }, (_, index) => 30000 + index * 1000),
    };
    await renderLoaded({
      runResult: {
        ...RESULT,
        monte_carlo: {
          runs: 500,
          seed: 42,
          herd_percentiles: band,
          cash_percentiles: liquidityBand,
          liquidity_percentiles: liquidityBand,
          npv_mean: 180000,
          npv_std: 50000,
          npv_p5: -10000,
          npv_p50: 175000,
          npv_p95: 260000,
          prob_npv_negative: 0.08,
          prob_liquidity_shortfall: 0.22,
          prob_dscr_below_one: 0.12,
          minimum_cash_p5: -30000,
          minimum_cash_p50: 12000,
          ending_cash_p5: 20000,
          ending_cash_p50: 90000,
          mean_disease_outbreaks: 0.4,
          mean_drought_events: 0.25,
          mean_market_crashes: 0.1,
          npv_histogram_counts: Array(20).fill(25),
          npv_histogram_edges: Array.from(
            { length: 21 },
            (_, index) => -100000 + index * 25000,
          ),
        },
      },
    });

    await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(await screen.findByText("P(cash shortfall)")).toBeInTheDocument();
    expect(screen.getByText("22.0%")).toBeInTheDocument();
    expect(screen.getByText("Annual uncertainty checkpoints")).toBeInTheDocument();
    expect(screen.getByText(/0.40 disease, 0.25 drought/)).toBeInTheDocument();
  });

  it("re-validates horizon-bounded inputs when the horizon changes", async () => {
    // A herd event's Month is bounded by meta.horizon_months, which is live
    // state. Validation used to run only on keystroke, so a value that was out
    // of range at the old horizon kept its error AND kept the field in the
    // parent's invalidFields set — permanently disabling Run and Save even
    // after the horizon grew past it. Once the draft becomes valid it must
    // also be committed; merely clearing the error would display month 90
    // while silently sending the old month 12 in the payload.
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        sales: { festival_sale_months: [] },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "5 yr" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const month = screen.getByLabelText("Month");
    await user.clear(month);
    await user.type(month, "90");
    expect(await screen.findByText("Must be at most 60.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    await user.click(screen.getByText("Sales"));
    await user.type(screen.getByLabelText(/Festival Sale Months/), "90");
    expect(screen.getByText("Every entry must be at most 60.")).toBeInTheDocument();

    // Raising the horizon to 10 yr (120 months) makes month 90 legal again.
    await user.click(screen.getByRole("button", { name: "10 yr" }));
    await waitFor(() =>
      expect(screen.queryByText("Must be at most 60.")).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled(),
    );

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
    expect(captured.body?.assumptions.events?.[0]).toMatchObject({ month: 90 });
    expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual([90]);
  });
});
