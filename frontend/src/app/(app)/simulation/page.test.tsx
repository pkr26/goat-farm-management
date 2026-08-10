/**
 * Simulation page: RBAC gating, defaults auto-load into the generic
 * assumptions editor, ad-hoc run results (metric cards + annual P&L),
 * scenario save with list refresh, and the run error state.
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
  project_cost_breakdown: {
    shed_cost: 200000,
    equipment_cost: 50000,
    stock_cost: 200000,
    working_capital: 50000,
  },
  metric_explanations: [
    {
      key: "npv",
      title: "Net Present Value",
      explanation: "NPV is ₹2,34,567 over the 60-month horizon.",
      figures: { npv: 234567, discount_rate: 0.12 },
    },
    {
      key: "project_cost",
      title: "Project Cost",
      explanation: "Total project cost is ₹5,00,000 at month 0.",
      figures: {
        shed_cost: 200000,
        equipment_cost: 50000,
        stock_cost: 200000,
        working_capital: 50000,
      },
    },
  ],
  narrative_report: [
    {
      key: "overview",
      title: "Overview",
      paragraphs: ["A 50-doe stall-fed Osmanabadi unit over 60 months."],
      figures: {},
    },
    {
      key: "herd_trajectory",
      title: "Herd Trajectory",
      paragraphs: ["The herd grows from 52 to 90 head."],
      figures: {},
    },
    {
      key: "revenue_mix",
      title: "Revenue Mix",
      paragraphs: ["Meat sales dominate revenue."],
      figures: {},
    },
    {
      key: "cost_mix",
      title: "Cost Mix",
      paragraphs: ["Feed is the largest cost."],
      figures: {},
    },
    {
      key: "viability_verdict",
      title: "Viability Verdict",
      paragraphs: ["The unit clears the viability bar."],
      figures: { verdict: "VIABLE" },
    },
    {
      key: "risks",
      title: "Risks",
      paragraphs: ["Meat price swings are the main risk."],
      figures: {},
    },
  ],
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
    http.get("/api/simulation/scenarios", ({ request }) => {
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

  it("does not let a late defaults response overwrite a scenario loaded afterward", async () => {
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Expansion",
      notes: "",
      assumptions: {
        ...DEFAULTS,
        herd: { ...DEFAULTS.herd, does: 99 },
      },
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    let releaseDefaults!: () => void;
    registerApiHandlers([scenario]);
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

    const scenarioRow = (await screen.findByText("Expansion")).closest("tr") as HTMLElement;
    await user.click(within(scenarioRow).getByRole("button", { name: "Load" }));
    expect(await screen.findByLabelText("Does")).toHaveValue(99);
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();

    releaseDefaults();
    expect(
      await screen.findByRole("button", { name: "Load defaults" }),
    ).toBeEnabled();
    expect(screen.getByLabelText("Does")).toHaveValue(99);
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();
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

  it("prevents saved and ad-hoc simulation runs from overlapping", async () => {
    const scenario = {
      id: 7,
      farm_id: 1,
      name: "Plan B",
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    let releaseRun!: () => void;
    let scenarioRuns = 0;
    server.use(
      http.post(
        "/api/simulation/run",
        () =>
          new Promise<Response>((resolve) => {
            releaseRun = () => resolve(HttpResponse.json(RESULT));
          }),
      ),
      http.post("/api/simulation/scenarios/7/run", () => {
        scenarioRuns += 1;
        return HttpResponse.json(RESULT);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded([scenario]);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(releaseRun).toBeTypeOf("function"));
    const scenarioRow = screen.getByText("Plan B").closest("tr") as HTMLElement;
    const scenarioRun = within(scenarioRow).getByRole("button", { name: "Run" });
    expect(scenarioRun).toBeDisabled();
    await user.click(scenarioRun);
    expect(scenarioRuns).toBe(0);

    releaseRun();
    expect(await screen.findByText("Source: Current editor assumptions")).toBeInTheDocument();
  });

  it("prevents comparison while an ad-hoc run is in flight", async () => {
    const scenarios = [1, 2].map((id) => ({
      id,
      farm_id: 1,
      name: `Plan ${id}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    let releaseRun!: () => void;
    let compareCalls = 0;
    server.use(
      http.post(
        "/api/simulation/run",
        () =>
          new Promise<Response>((resolve) => {
            releaseRun = () => resolve(HttpResponse.json(RESULT));
          }),
      ),
      http.get("/api/simulation/scenarios/compare", () => {
        compareCalls += 1;
        return HttpResponse.json({ scenarios, results: [RESULT, RESULT] });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded(scenarios);
    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(releaseRun).toBeTypeOf("function"));
    const compare = screen.getByRole("button", { name: "Compare selected" });
    expect(compare).toBeDisabled();
    await user.click(compare);
    expect(compareCalls).toBe(0);

    releaseRun();
    expect(await screen.findByText("Source: Current editor assumptions")).toBeInTheDocument();
  });

  it("prevents ad-hoc and saved runs while comparison is in flight", async () => {
    const scenarios = [1, 2].map((id) => ({
      id,
      farm_id: 1,
      name: `Plan ${id}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    let releaseCompare!: () => void;
    let adHocRuns = 0;
    let savedRuns = 0;
    server.use(
      http.get(
        "/api/simulation/scenarios/compare",
        () =>
          new Promise<Response>((resolve) => {
            releaseCompare = () =>
              resolve(HttpResponse.json({ scenarios, results: [RESULT, RESULT] }));
          }),
      ),
      http.post("/api/simulation/run", () => {
        adHocRuns += 1;
        return HttpResponse.json(RESULT);
      }),
      http.post("/api/simulation/scenarios/1/run", () => {
        savedRuns += 1;
        return HttpResponse.json(RESULT);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded(scenarios);
    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));

    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    await waitFor(() => expect(releaseCompare).toBeTypeOf("function"));
    const adHocRun = screen.getByRole("button", { name: "Run simulation" });
    const planOneRow = screen.getByText("Plan 1").closest("tr") as HTMLElement;
    const savedRun = within(planOneRow).getByRole("button", { name: "Run" });
    expect(adHocRun).toBeDisabled();
    expect(savedRun).toBeDisabled();
    await user.click(adHocRun);
    await user.click(savedRun);
    expect(adHocRuns).toBe(0);
    expect(savedRuns).toBe(0);

    releaseCompare();
    expect(await screen.findByText("Comparison")).toBeInTheDocument();
  });

  it("labels result provenance and warns when the editor changes after a run", async () => {
    server.use(http.post("/api/simulation/run", () => HttpResponse.json(RESULT)));
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Source: Current editor assumptions")).toBeInTheDocument();
    expect(
      screen.queryByText(/results do not match the current editor/i),
    ).not.toBeInTheDocument();

    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "51");
    expect(
      screen.getByText(/results do not match the current editor assumptions or run options/i),
    ).toBeInTheDocument();
  });

  it("keeps an invalid legacy scenario visible and deletable but blocks load and run", async () => {
    await renderLoaded([
      {
        id: 9,
        farm_id: 1,
        name: "Legacy broken plan",
        notes: "Created before validation was tightened",
        assumptions: null,
        valid: false,
        validation_error: "horizon_months must be at least 12",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-02T00:00:00Z",
      },
    ]);

    expect(screen.getByText("Invalid saved assumptions")).toBeInTheDocument();
    expect(screen.getByText(/horizon_months must be at least 12/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("caps comparison selection at five valid scenarios", async () => {
    const scenarios = Array.from({ length: 6 }, (_, index) => ({
      id: index + 1,
      farm_id: 1,
      name: `Plan ${index + 1}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    const user = userEvent.setup();
    await renderLoaded(scenarios);

    for (let index = 1; index <= 5; index += 1)
      await user.click(screen.getByLabelText(`Compare Plan ${index}`));

    expect(screen.getByLabelText("Compare Plan 6")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByText(/5 selected/)).toBeInTheDocument();
  });

  it("pages truthfully and preserves valid compare selections across pages", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) => ({
      id: index + 1,
      farm_id: 1,
      name: `Plan ${index + 1}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    const user = userEvent.setup();
    await renderLoaded(scenarios);

    expect(
      await screen.findByText("Showing 1–20 of 21 saved scenarios"),
    ).toBeInTheDocument();
    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("Plan 21")).toBeInTheDocument();
    expect(screen.getByText("Showing 21–21 of 21 saved scenarios")).toBeInTheDocument();
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Compare Plan 21"));
    expect(screen.getByText(/2 selected/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Plan 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Compare Plan 1")).toBeChecked();
  });

  it("clamps to the previous page after deleting the final page's only row", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) => ({
      id: index + 1,
      farm_id: 1,
      name: `Plan ${index + 1}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    server.use(
      http.delete("/api/simulation/scenarios/:scenarioId", ({ params }) => {
        const index = scenarios.findIndex(
          (scenario) => scenario.id === Number(params.scenarioId),
        );
        if (index >= 0) scenarios.splice(index, 1);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await renderLoaded(scenarios);
    await user.click(screen.getByRole("button", { name: "Next" }));

    const finalRow = (await screen.findByText("Plan 21")).closest("tr") as HTMLElement;
    await user.click(within(finalRow).getByRole("button", { name: "Delete" }));

    expect(
      await screen.findByText("Showing 1–20 of 20 saved scenarios"),
    ).toBeInTheDocument();
    expect(screen.getByText("Plan 1")).toBeInTheDocument();
    expect(screen.queryByText("Plan 21")).not.toBeInTheDocument();
  });

  it("enforces backend numeric bounds before a run", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const rate = screen.getByLabelText("Interest Rate Annual");

    await user.clear(rate);
    await user.type(rate, "0.9");

    expect(screen.getByText("Must be at most 0.5.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
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
          assumptions: body.assumptions as typeof DEFAULTS,
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

  it("moves to the final oldest-first page after saving a new scenario", async () => {
    const scenarios = Array.from({ length: 20 }, (_, index) => ({
      id: index + 1,
      farm_id: 1,
      name: `Existing ${index + 1}`,
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    }));
    server.use(
      http.post("/api/simulation/scenarios", async ({ request }) => {
        const body = (await request.json()) as {
          name: string;
          notes?: string;
          assumptions: unknown;
        };
        const scenario = {
          id: 21,
          farm_id: 1,
          name: body.name,
          notes: body.notes ?? "",
          assumptions: body.assumptions as typeof DEFAULTS,
          valid: true,
          validation_error: null,
          created_at: "2026-01-03T00:00:00Z",
          updated_at: "2026-01-03T00:00:00Z",
        };
        scenarios.push(scenario);
        return HttpResponse.json(scenario, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded(scenarios);
    expect(
      await screen.findByText("Showing 1–20 of 20 saved scenarios"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Name/), "Newest plan");
    await user.click(within(dialog).getByRole("button", { name: "Save scenario" }));

    expect(await screen.findByText("Newest plan")).toBeInTheDocument();
    expect(screen.getByText("Showing 21–21 of 21 saved scenarios")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
  });

  // REGRESSION — the assumption-editor unit caption was inferred from
  // substrings, so ₹10,000/month of labour read "Unit: months" and a
  // head-count threshold read "Unit: ₹".
  it("captions per-month money, head counts and fodder yield with real units", async () => {
    registerApiHandlers();
    server.use(
      http.get("/api/simulation/defaults", () =>
        HttpResponse.json({
          ...DEFAULTS,
          costs: {
            labour_per_month: 10000,
            labour_per_head_threshold: 75,
            misc_overhead_per_month: 2000,
          },
          feed: { fodder_yield_t_dm_per_acre_year: 6 },
        }),
      ),
    );
    renderWithProviders(<SimulationPage />);
    await screen.findByText("Horizon Months");

    const unitOf = (label: string) =>
      screen.getByLabelText(label).closest("div")?.querySelector("p")?.textContent;
    expect(unitOf("Labour Per Month")).toBe("Unit: ₹/month");
    expect(unitOf("Misc Overhead Per Month")).toBe("Unit: ₹/month");
    expect(unitOf("Labour Per Head Threshold")).toBe("Unit: head per labourer");
    expect(unitOf("Fodder Yield T Dm Per Acre Year")).toBe("Unit: t DM/acre/yr");
    // The genuine duration field is unaffected.
    expect(unitOf("Horizon Months")).toBe("Unit: months");
  });

  // REGRESSION — the System trigger passed no `items` map, so Base UI showed
  // the raw enum "stall_fed" while the open list read "Stall Fed".
  it("shows the humanized system label in the closed trigger", async () => {
    await renderLoaded();
    expect(screen.getByLabelText("System")).toHaveTextContent("Stall Fed");
    expect(screen.getByLabelText("System")).not.toHaveTextContent("stall_fed");
  });

  // REGRESSION — head counts are expected-value float64s; the cells used to
  // print the full 17-significant-digit repr.
  it("rounds the monthly projection head counts instead of printing raw floats", async () => {
    server.use(
      http.post("/api/simulation/run", () =>
        HttpResponse.json({
          ...RESULT,
          months: [
            monthRow({
              total_herd: 58.851702943044856,
              births: 7.127272727272728,
              deaths: 0.28410042178298056,
              sales_head: 1.5000000000000002,
            }),
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    const projection = (await screen.findByText("Monthly projection")).closest(
      "[data-slot='card']",
    ) as HTMLElement;
    const row = within(projection).getByText("58.9").closest("tr") as HTMLElement;
    expect(within(row).getByText("7.1")).toBeInTheDocument();
    expect(within(row).getByText("0.3")).toBeInTheDocument();
    expect(within(row).getByText("1.5")).toBeInTheDocument();
    expect(
      within(projection).queryByText("58.851702943044856"),
    ).not.toBeInTheDocument();
  });

  // REGRESSION — updated_at is a naive-UTC datetime; the date-only formatter
  // printed the UTC day, which is the previous day for farms ahead of UTC.
  it("renders the scenario Updated column in the farm timezone", async () => {
    await renderLoaded([
      {
        id: 1,
        farm_id: 1,
        name: "Plan B",
        notes: "",
        assumptions: DEFAULTS,
        valid: true,
        validation_error: null,
        created_at: "2026-08-08T20:30:00",
        // 20:30 UTC on 8 Aug is 02:00 on 9 Aug for the Asia/Kolkata test farm.
        updated_at: "2026-08-08T20:30:00",
      },
    ]);

    expect(await screen.findByText("09-08-2026 02:00")).toBeInTheDocument();
    expect(screen.queryByText("8 Aug 2026")).not.toBeInTheDocument();
  });

  // REGRESSION — a scenario run was fingerprinted from the scenario's stored
  // assumptions while the comparison value was always derived from the editor,
  // so the "results are stale" banner was on after every saved-scenario run.
  describe("saved-scenario run staleness", () => {
    const SCENARIO = {
      id: 7,
      farm_id: 1,
      name: "Plan B",
      notes: "",
      assumptions: DEFAULTS,
      valid: true,
      validation_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const STALE = /results do not match the current/i;

    async function runScenario() {
      server.use(
        http.post("/api/simulation/scenarios/7/run", () => HttpResponse.json(RESULT)),
      );
      const user = userEvent.setup();
      await renderLoaded([SCENARIO]);
      await user.click(screen.getByRole("button", { name: "Run" }));
      expect(await screen.findByText("Source: Saved scenario “Plan B”")).toBeInTheDocument();
      return user;
    }

    it("does not warn about staleness right after running a saved scenario", async () => {
      await runScenario();
      expect(screen.queryByText(STALE)).not.toBeInTheDocument();
    });

    it("still warns when a run option is toggled after the scenario run", async () => {
      const user = await runScenario();
      await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));
      expect(screen.getByText(STALE)).toBeInTheDocument();
    });

    it("warns once the editor diverges from the scenario it is showing", async () => {
      const user = await runScenario();
      // Load the same scenario into the editor: it now describes the run.
      await user.click(screen.getByRole("button", { name: "Load" }));
      expect(screen.queryByText(STALE)).not.toBeInTheDocument();

      const does = screen.getByLabelText("Does");
      await user.clear(does);
      await user.type(does, "51");
      expect(screen.getByText(STALE)).toBeInTheDocument();
    });

    it("leaves an editor-only edit alone while the results describe a scenario", async () => {
      const user = await runScenario();
      const does = screen.getByLabelText("Does");
      await user.clear(does);
      await user.type(does, "51");
      expect(screen.queryByText(STALE)).not.toBeInTheDocument();
    });
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
