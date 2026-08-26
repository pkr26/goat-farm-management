/**
 * Simulation page: contracts the keyboard cannot reach on its own — the
 * section filter that keeps the generic editor to object-shaped groups,
 * validation of stored payloads (festival months, start month, herd events),
 * numeric draft normalisation and its ARIA error wiring, growth-curve
 * mirroring, saved-scenario result binding, and scenario-page re-homing.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
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

/** Osmanabadi-shaped growth section: 13 entries, ages 0-12. */
const GROWTH = {
  birth_weight_kg: 2.5,
  adult_weight_doe_kg: 32,
  adult_weight_buck_kg: 34,
  weight_by_age_months: [
    2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 24.5, 26.5,
  ],
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

function metrics(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

function result(overrides: Record<string, unknown> = {}) {
  return {
    months: [monthRow()],
    annual_pl: [],
    metrics: metrics(),
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
    ...overrides,
  };
}

const RESULT = result();

interface RunBody {
  assumptions: Record<string, unknown> & {
    meta?: { horizon_months?: number; start_year_month?: string };
    herd?: Record<string, unknown>;
    growth?: Record<string, unknown>;
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

/** Breeds + defaults + scenario list; the run POST captures its body. */
function registerApiHandlers(
  options: {
    scenarios?: unknown[];
    onRun?: (body: RunBody) => void;
    onScenarios?: (offset: number) => void;
    defaults?: unknown;
    permissions?: string[];
    snapshot?: () => Response;
    calibration?: () => Response;
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
      options.onScenarios?.(offset);
      return HttpResponse.json({
        items: scenarios.slice(offset, offset + limit),
        total: scenarios.length,
        limit,
        offset,
      });
    }),
    http.get("/api/simulation/herd-snapshot", () =>
      options.snapshot
        ? options.snapshot()
        : HttpResponse.json({
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
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return HttpResponse.json(RESULT);
    }),
  );
  if (options.calibration)
    server.use(
      http.get("/api/simulation/calibration", () => options.calibration!()),
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

function assumptionsCard() {
  return screen.getByText("Assumptions").closest("[data-slot='card']") as HTMLElement;
}

function sectionTitles() {
  return Array.from(assumptionsCard().querySelectorAll("summary")).map(
    (summary) => summary.textContent,
  );
}

describe("SimulationPage generic section reflection", () => {
  // The editor reflects whatever the defaults payload contains, so a payload
  // that grows a scalar, a null or a list at the top level must not turn that
  // key into a collapsible section of "fields" (Object.entries of a string
  // yields character indices; of null it throws).
  it("renders an editor section only for object-shaped assumption groups", async () => {
    const captured: { body: RunBody | null } = { body: null };
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
        model_version: "3.4.0",
        breed_profile: null,
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    expect(sectionTitles()).toEqual(["Meta", "Herd", "Finance"]);
    expect(screen.queryByText("Model Version")).not.toBeInTheDocument();
    expect(screen.queryByText("Breed Profile")).not.toBeInTheDocument();
    // The list-valued `events` key belongs to the herd events editor instead.
    expect(screen.getByLabelText("Month")).toHaveValue(6);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    // Unreflected keys are still round-tripped rather than silently dropped.
    expect(captured.body?.assumptions.model_version).toBe("3.4.0");
    expect(captured.body?.assumptions.breed_profile).toBeNull();
  });
});

describe("SimulationPage stored payload validation", () => {
  /** Load defaults whose sales section carries the given festival months. */
  async function renderFestivalMonths(festivalMonths: number[]) {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        sales: { festival_sale_months: festivalMonths },
      },
    });
  }

  const RANGE_ERROR = "Festival sale months must be whole numbers between 1 and 60.";
  const DUPLICATE_ERROR = "Festival sale months must not contain duplicates.";

  it("accepts stored festival months that sit inside the horizon", async () => {
    await renderFestivalMonths([12, 60]);

    expect(screen.queryByText(RANGE_ERROR)).not.toBeInTheDocument();
    expect(screen.queryByText(DUPLICATE_ERROR)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("blocks a run on a stored festival month past the horizon", async () => {
    await renderFestivalMonths([90]);

    expect(screen.getByText(RANGE_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("blocks a run on a stored festival month before month one", async () => {
    await renderFestivalMonths([0]);

    expect(screen.getByText(RANGE_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("blocks a run on a fractional stored festival month", async () => {
    await renderFestivalMonths([1.5]);

    expect(screen.getByText(RANGE_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("blocks a run on duplicated stored festival months", async () => {
    await renderFestivalMonths([12, 12]);

    expect(screen.getByText(DUPLICATE_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  const START_ERROR = "Start year month must be a real month from 1900-01 to 2200-12.";

  it("blocks a run on a stored start month with a zero month part", async () => {
    await renderLoaded({
      defaults: { ...DEFAULTS, meta: { horizon_months: 60, start_year_month: "2026-00" } },
    });

    expect(screen.getByText(START_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("blocks a run on a stored start month with a thirteenth month", async () => {
    await renderLoaded({
      defaults: { ...DEFAULTS, meta: { horizon_months: 60, start_year_month: "2026-13" } },
    });

    expect(screen.getByText(START_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("accepts the earliest supported start month", async () => {
    await renderLoaded({
      defaults: { ...DEFAULTS, meta: { horizon_months: 60, start_year_month: "1900-01" } },
    });

    expect(screen.queryByText(START_ERROR)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  // Stored events skip the inline NumberInput bounds (those only run on a
  // keystroke), so validateEvents is the only gate between a legacy payload
  // and a 422 from the API.
  it("blocks a run on stored herd events that break the month and count contract", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: [
          { month: 90, kind: "purchase", animal_class: "doe", count: 10, price_per_head: null },
          { month: 6, kind: "sale", animal_class: "buck", count: 0, price_per_head: null },
        ],
      },
    });

    expect(
      screen.getByText("Event 1: month must be a whole number between 1 and 60."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Event 2: count must be greater than 0 and at most 100,000."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

describe("SimulationPage numeric field semantics", () => {
  it("normalises a committed draft on blur and wires the rejected one to an alert", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const rate = screen.getByLabelText("Interest Rate Annual") as HTMLInputElement;
    const errorId = `${rate.id}-error`;
    const fieldAlert = () =>
      screen.queryAllByRole("alert").find((element) => element.id === errorId);

    await user.clear(rate);
    await user.type(rate, "0.250");
    await user.tab();
    // The draft is released on blur, so the field shows the committed number.
    expect(rate.value).toBe("0.25");
    expect(fieldAlert()).toBeUndefined();
    expect(rate).not.toHaveAttribute("aria-describedby");

    await user.clear(rate);
    await user.type(rate, "0.9");
    expect(fieldAlert()).toHaveTextContent("Must be at most 0.5.");
    expect(rate).toHaveAttribute("aria-invalid", "true");
    expect(rate).toHaveAttribute("aria-describedby", errorId);
    // A rejected draft survives blur so the operator can correct it.
    await user.tab();
    expect(rate.value).toBe("0.9");

    await user.clear(rate);
    await user.type(rate, "0.5");
    expect(fieldAlert()).toBeUndefined();
    expect(rate).not.toHaveAttribute("aria-invalid");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("normalises a committed array draft on blur and wires its rejection to an alert", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [] } },
    });
    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/) as HTMLInputElement;
    const errorId = `${festivals.id}-error`;
    const fieldAlert = () =>
      screen.queryAllByRole("alert").find((element) => element.id === errorId);

    await user.type(festivals, "12,24");
    await user.tab();
    // Blur releases the draft, so the field shows the canonical spacing of the
    // committed list rather than whatever was typed.
    expect(festivals.value).toBe("12, 24");
    expect(fieldAlert()).toBeUndefined();
    expect(festivals).not.toHaveAttribute("aria-describedby");

    await user.clear(festivals);
    await user.type(festivals, "12, 12");
    expect(fieldAlert()).toHaveTextContent(
      "Simulation Months must not contain duplicates.",
    );
    expect(festivals).toHaveAttribute("aria-invalid", "true");
    expect(festivals).toHaveAttribute("aria-describedby", errorId);
    // A rejected draft survives blur so the operator can correct it.
    await user.tab();
    expect(festivals.value).toBe("12, 12");
  });
});

describe("SimulationPage growth curve mirroring", () => {
  it("mirrors the growth curve's first entry back into birth weight", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, growth: GROWTH },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Growth"));
    const curve = screen.getByLabelText(/Weight By Age Months/);
    await user.clear(curve);
    await user.type(curve, "3,4.5,6.5,8.5,10.5,12.5,14.5,16.5,18.5,20.5,22.5,24.5,26.5");

    expect(screen.getByLabelText("Birth Weight Kg")).toHaveValue(3);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.growth).toMatchObject({
      birth_weight_kg: 3,
      weight_by_age_months: [
        3, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 24.5, 26.5,
      ],
    });
  });

  it("does not invent a growth curve when the defaults carry none", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: { birth_weight_kg: 2.5, adult_weight_doe_kg: 32, sale_age_months: 12 },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Growth"));
    const birthWeight = screen.getByLabelText("Birth Weight Kg");
    await user.clear(birthWeight);
    await user.type(birthWeight, "3.1");

    // A single-entry curve would fail the 13-entry contract at the API; the
    // birth weight mirror only rewrites age zero of a curve that exists.
    expect(screen.getByLabelText(/Weight By Age Months/)).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.growth).toMatchObject({
      birth_weight_kg: 3.1,
      weight_by_age_months: [],
    });
  });

  it("confines a field edit to the section that owns it", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, growth: GROWTH },
      onRun: (body) => {
        captured.body = body;
      },
    });

    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "51");

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.herd).toEqual({ ...DEFAULTS.herd, does: 51 });
    expect(captured.body?.assumptions.growth).toEqual(GROWTH);
  });

  it("keeps a partial growth section out of the paired adult-weight check", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_buck_kg: 20,
          weight_by_age_months: GROWTH.weight_by_age_months,
        },
      },
    });

    // The message names both adult weights, so it may only fire once both are
    // present — 20 kg is below the 26.5 kg yearling peak.
    expect(
      screen.queryByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("keeps a growth section without an adult buck weight out of that check", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 20,
          weight_by_age_months: GROWTH.weight_by_age_months,
        },
      },
    });

    expect(
      screen.queryByText(
        "Adult doe and buck weights must be at least the highest yearling weight.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });
});

describe("SimulationPage horizon-bounded array drafts", () => {
  it("keeps a rejected festival draft out of the assumptions when the horizon grows", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [9] } },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.clear(festivals);
    await user.type(festivals, "12, 12");
    expect(
      screen.getByText("Simulation Months must not contain duplicates."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "10 yr" }));

    // Re-deriving the draft against the new bound must not commit it: the
    // duplicate is still a duplicate, so the stored months are untouched.
    expect(
      screen.getByText("Simulation Months must not contain duplicates."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Festival sale months must not contain duplicates."),
    ).not.toBeInTheDocument();

    await user.clear(festivals);
    await user.type(festivals, "12, 24");
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual([12, 24]);
  });

  it("keeps a recovered festival draft through a second horizon change", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [] } },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.type(festivals, "90");
    expect(screen.getByText("Every entry must be at most 60.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "10 yr" }));
    await waitFor(() =>
      expect(screen.queryByText("Every entry must be at most 60.")).not.toBeInTheDocument(),
    );

    // The recovery commit fires once. A later bound change re-derives an
    // already-valid draft and must leave the committed months alone.
    await user.click(screen.getByRole("button", { name: "15 yr" }));
    expect(screen.getByLabelText(/Festival Sale Months/)).toHaveValue("90");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual([90]);
  });
});

describe("SimulationPage herd event identity", () => {
  it("patches only the event row that changed", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const months = screen.getAllByLabelText("Month");
    await user.clear(months[1]);
    await user.type(months[1], "30");
    await user.click(screen.getAllByLabelText("Kind")[1]);
    await user.click(await screen.findByRole("option", { name: "Sale" }));

    expect(months[0]).toHaveValue(12);
    expect(screen.getAllByLabelText("Kind")[0]).toHaveTextContent("Purchase");

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.events).toEqual([
      { month: 12, kind: "purchase", animal_class: "doe", count: 10, price_per_head: null },
      { month: 30, kind: "sale", animal_class: "doe", count: 10, price_per_head: null },
    ]);
  });

  it("releases the run gate held by a removed event row", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const month = screen.getByLabelText("Month");
    await user.clear(month);
    await user.type(month, "0");
    expect(screen.getByText("Must be at least 1.")).toBeInTheDocument();
    expect(
      screen.getByText("Fix 1 highlighted numeric field before running or saving."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(screen.queryByText(/highlighted numeric field/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("gives events loaded from a scenario keys that later additions cannot reuse", async () => {
    const scenario = scenarioRow({
      id: 4,
      name: "Two purchases",
      assumptions: {
        ...DEFAULTS,
        events: [
          { month: 6, kind: "purchase", animal_class: "doe", count: 5, price_per_head: null },
          { month: 9, kind: "purchase", animal_class: "buck", count: 2, price_per_head: null },
        ],
      },
    });
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    const row = screen.getByText("Two purchases").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Load" }));
    expect(screen.getAllByLabelText("Month")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    expect(screen.getAllByLabelText("Month")).toHaveLength(4);

    // The second loaded row owns its own validity entry; removing the last
    // added row must not release it through a recycled key.
    await user.clear(screen.getAllByLabelText("Month")[1]);
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    await user.click(screen.getAllByRole("button", { name: "Remove" })[3]);

    expect(screen.getAllByLabelText("Month")).toHaveLength(3);
    expect(
      screen.getByText("Fix 1 highlighted numeric field before running or saving."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

describe("SimulationPage scenario comparison", () => {
  it("dashes an unpaid-back scenario and drops the table when the selection changes", async () => {
    const scenarios = [
      scenarioRow({ id: 1, name: "Plan A" }),
      scenarioRow({ id: 2, name: "Plan B" }),
    ];
    server.use(
      http.get("/api/simulation/scenarios/compare", () =>
        HttpResponse.json({
          scenarios,
          results: [RESULT, result({ metrics: metrics({ payback_month: null }) })],
        }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios });

    await user.click(screen.getByLabelText("Compare Plan A"));
    await user.click(screen.getByLabelText("Compare Plan B"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));

    const comparison = (await screen.findByText("Comparison")).parentElement as HTMLElement;
    const paybackRow = within(comparison)
      .getByText("Payback month")
      .closest("tr") as HTMLElement;
    expect(within(paybackRow).getByText("30")).toBeInTheDocument();
    expect(within(paybackRow).getByText("—")).toBeInTheDocument();

    // The table describes the selection that produced it, so changing the
    // selection must retire it rather than leave stale figures on screen.
    await user.click(screen.getByLabelText("Compare Plan B"));
    expect(screen.queryByText("Comparison")).not.toBeInTheDocument();
  });
  it("keeps a rendered comparison when the scenario list is emptied elsewhere", async () => {
    const scenarios = [
      scenarioRow({ id: 1, name: "Plan A" }),
      scenarioRow({ id: 2, name: "Plan B" }),
    ];
    server.use(
      http.get("/api/simulation/scenarios/compare", () =>
        HttpResponse.json({ scenarios, results: [RESULT, RESULT] }),
      ),
    );
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded({ scenarios }, queryClient);

    await user.click(screen.getByLabelText("Compare Plan A"));
    await user.click(screen.getByLabelText("Compare Plan B"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    expect(await screen.findByText("Comparison")).toBeInTheDocument();

    scenarios.splice(0, scenarios.length);
    await act(async () => {
      await queryClient.invalidateQueries();
    });

    expect(await screen.findByText("No saved scenarios yet.")).toBeInTheDocument();
    // Computed figures are retired by explicit actions, never by a background
    // list refresh — the same rule the run result follows.
    expect(screen.getByText("Comparison")).toBeInTheDocument();
  });
});

describe("SimulationPage saved-scenario result binding", () => {
  const STALE = /results do not match the current saved scenario/i;

  function twoScenarios() {
    return [
      scenarioRow({ id: 1, name: "Plan A", assumptions: DEFAULTS }),
      scenarioRow({
        id: 2,
        name: "Plan B",
        assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 77 } },
      }),
    ];
  }

  it("measures a run against the scenario it came from, not the first row", async () => {
    const scenarios = twoScenarios();
    server.use(
      http.post("/api/simulation/scenarios/2/run", () => HttpResponse.json(RESULT)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios });

    const row = screen.getByText("Plan B").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Run" }));

    expect(await screen.findByText("Source: Saved scenario “Plan B”")).toBeInTheDocument();
    expect(screen.queryByText(STALE)).not.toBeInTheDocument();
  });

  it("warns once the saved scenario behind a result changes on the server", async () => {
    const scenarios = twoScenarios();
    server.use(
      http.post("/api/simulation/scenarios/2/run", () => HttpResponse.json(RESULT)),
    );
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded({ scenarios }, queryClient);

    const row = screen.getByText("Plan B").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Source: Saved scenario “Plan B”")).toBeInTheDocument();

    scenarios[1] = scenarioRow({
      id: 2,
      name: "Plan B",
      revision: 2,
      assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 120 } },
    });
    await act(async () => {
      await queryClient.invalidateQueries();
    });

    expect(await screen.findByText(STALE)).toBeInTheDocument();
  });

  it("keeps a result whose scenario is on another page instead of guessing", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    server.use(
      http.post("/api/simulation/scenarios/1/run", () => HttpResponse.json(RESULT)),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios });

    const row = screen.getByText("Plan 1").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Source: Saved scenario “Plan 1”")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 21")).toBeInTheDocument();

    // The basis is off-screen: it cannot be compared, so it must not be
    // guessed from an unrelated row either.
    expect(screen.getByText("Source: Saved scenario “Plan 1”")).toBeInTheDocument();
    expect(screen.queryByText(STALE)).not.toBeInTheDocument();
  });
});

describe("SimulationPage scenario paging", () => {
  it("stays on a valid middle page", async () => {
    const scenarios = Array.from({ length: 41 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    const user = userEvent.setup();
    await renderLoaded({ scenarios });
    expect(
      await screen.findByText("Showing 1–20 of 41 saved scenarios"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(
      await screen.findByText("Showing 21–40 of 41 saved scenarios"),
    ).toBeInTheDocument();
    expect(screen.getByText("Plan 21")).toBeInTheDocument();
  });

  it("re-homes a shrinking scenario list to the real last page in one request", async () => {
    const scenarios = Array.from({ length: 61 }, (_, index) =>
      scenarioRow({ id: index + 1, name: `Plan ${index + 1}` }),
    );
    const offsets: number[] = [];
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded(
      { scenarios, onScenarios: (offset) => offsets.push(offset) },
      queryClient,
    );

    for (const range of ["21–40", "41–60", "61–61"]) {
      await user.click(screen.getByRole("button", { name: "Next" }));
      expect(
        await screen.findByText(`Showing ${range} of 61 saved scenarios`),
      ).toBeInTheDocument();
    }

    async function shrinkTo(total: number) {
      scenarios.splice(total, scenarios.length - total);
      await act(async () => {
        await queryClient.invalidateQueries();
      });
    }

    // Offset 60 no longer exists: re-home to the page that holds row 41.
    await shrinkTo(41);
    expect(
      await screen.findByText("Showing 41–41 of 41 saved scenarios"),
    ).toBeInTheDocument();

    // Offset 40 is now exactly the total, which is one past the last row.
    await shrinkTo(40);
    expect(
      await screen.findByText("Showing 21–40 of 40 saved scenarios"),
    ).toBeInTheDocument();
    expect(screen.getByText("Plan 40")).toBeInTheDocument();

    await shrinkTo(0);
    expect(await screen.findByText("No saved scenarios yet.")).toBeInTheDocument();
    expect(screen.queryByText(/page no longer exists/)).not.toBeInTheDocument();
    // Every re-home lands on a page the API can serve, in a single hop.
    const hops = offsets.filter((offset, index) => offset !== offsets[index - 1]);
    expect(hops).toEqual([0, 20, 40, 60, 40, 20, 0]);
  });
});

describe("SimulationPage editor load latches", () => {
  async function refetchDefaults(queryClient: ReturnType<typeof createTestQueryClient>) {
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["/api/simulation/defaults"] });
    });
    // React Query publishes observer changes through its notification
    // scheduler; let the mirroring effect run before asserting.
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    });
  }

  it("treats a background defaults refresh as cache maintenance, not a reload", async () => {
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    await renderLoaded({}, queryClient);
    expect(screen.getByLabelText("Does")).toHaveValue(50);

    server.use(
      http.get("/api/simulation/defaults", () =>
        HttpResponse.json({ ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 88 } }),
      ),
    );
    await refetchDefaults(queryClient);
    expect(screen.getByLabelText("Does")).toHaveValue(50);

    // The same holds after an imported herd replaced the head counts.
    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));
    await refetchDefaults(queryClient);
    expect(screen.getByLabelText("Does")).toHaveValue(48);
  });

  it("never re-applies the previous herd snapshot when a later import fails", async () => {
    let calls = 0;
    const user = userEvent.setup();
    await renderLoaded({
      snapshot: () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({
              does: 48,
              bucks: 3,
              f_kids: 4,
              f_weaners: 5,
              f_growers: 6,
              m_kids: 3,
              m_weaners: 2,
              m_growers: 1,
              total_head: 72,
            })
          : HttpResponse.json({ detail: "herd snapshot unavailable" }, { status: 503 });
      },
    });

    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(48));
    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "51");
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("herd snapshot unavailable"),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Does")).toHaveValue(51);
  });

  // The generated client hands the page a {data, status} envelope, so a
  // 2xx that is not the documented 200 arrives without a snapshot body.
  it("reports a herd snapshot response that is not a 200 instead of applying it", async () => {
    const user = userEvent.setup();
    await renderLoaded({ snapshot: () => new HttpResponse(null, { status: 204 }) });
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();

    await user.click(screen.getByRole("button", { name: "Use current herd" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not load the herd snapshot."),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Does")).toHaveValue(50);
  });

  it("never re-applies the previous calibration when a later one fails", async () => {
    let calls = 0;
    const calibrated = {
      assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 63 } },
      evidence: [],
      warnings: [],
      coverage_score: 0.5,
      lookback_months: 24,
      reference_date: "2026-08-01",
    };
    const user = userEvent.setup();
    await renderLoaded({
      permissions: CALIBRATION_PERMS,
      calibration: () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(calibrated)
          : HttpResponse.json({ detail: "not enough farm records" }, { status: 422 });
      },
    });

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(63));
    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "64");
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("not enough farm records"),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Does")).toHaveValue(64);
  });
});
