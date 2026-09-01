/**
 * Simulation page: the editor's opening shape and its event cap, the run
 * options a farmer turns back off, the scenario table's trust in `valid`, the
 * comparison that came back with nothing in it, the save dialog's quiet
 * opening and its exits, and the two result tables whose rows are chosen by
 * arithmetic — the Monte Carlo checkpoints and the optimization candidates.
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

/** Sparse but valid full assumptions object (only present keys render). */
const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2, foundation_flock_state: "open" },
  finance: { interest_rate_annual: 0.12 },
  feed: { green_kg_per_head_per_day: 3 },
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
  months: [monthRow()],
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

/** One percentile band of `length` months, each series a fixed step apart. */
function band(length: number, base: number, step: number) {
  const series = (offset: number) =>
    Array.from({ length }, (_, index) => base + offset + index * step);
  return {
    p5: series(0),
    p25: series(step),
    p50: series(2 * step),
    p75: series(3 * step),
    p95: series(4 * step),
  };
}

function monteCarlo(overrides: Record<string, unknown> = {}) {
  return {
    runs: 400,
    seed: 7,
    herd_percentiles: band(26, 40, 1),
    cash_percentiles: band(26, -10000, 1000),
    liquidity_percentiles: band(26, -10000, 1000),
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
    npv_histogram_counts: Array.from({ length: 20 }, (_, index) =>
      index === 19 ? 40 : 0,
    ),
    npv_histogram_edges: Array.from(
      { length: 21 },
      (_, index) => -100000 + index * 25000,
    ),
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

type RunOptions = {
  monte_carlo: boolean;
  sensitivity: boolean;
  optimization: boolean;
};

/** Breeds + defaults + scenario list; the run POST returns `runResult`. */
function registerApiHandlers(
  options: {
    scenarios?: { id: number }[];
    defaults?: unknown;
    runResult?: unknown;
    onRun?: (options: RunOptions) => void;
    scenarioPage?: (params: URLSearchParams) => Record<string, unknown>;
  } = {},
) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () =>
      HttpResponse.json(options.defaults ?? DEFAULTS),
    ),
    http.get("/api/simulation/scenarios", ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (options.scenarioPage) return HttpResponse.json(options.scenarioPage(params));
      const scenarios = options.scenarios ?? [];
      const limit = Number(params.get("limit") ?? 20);
      const offset = Number(params.get("offset") ?? 0);
      return HttpResponse.json({
        items: scenarios.slice(offset, offset + limit),
        total: scenarios.length,
        limit,
        offset,
      });
    }),
    http.post("/api/simulation/run", async ({ request }) => {
      const body = (await request.json()) as RunOptions;
      options.onRun?.(body);
      return HttpResponse.json(options.runResult ?? RESULT);
    }),
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

/** Tick the named run options, run, and wait for the results section. */
async function runWithOptions(
  runResult: unknown,
  optionLabels: string[] = [],
): Promise<ReturnType<typeof userEvent.setup>> {
  const user = await renderLoaded({ runResult });
  for (const label of optionLabels)
    await user.click(screen.getByRole("checkbox", { name: label }));
  await user.click(screen.getByRole("button", { name: "Run simulation" }));
  expect(
    await screen.findByText("Source: Current editor assumptions"),
  ).toBeInTheDocument();
  return user;
}

function deleteHandler(scenarios: { id: number }[]) {
  return http.delete("/api/simulation/scenarios/:scenarioId", ({ params }) => {
    const index = scenarios.findIndex(
      (scenario) => scenario.id === Number(params.scenarioId),
    );
    if (index >= 0) scenarios.splice(index, 1);
    return new HttpResponse(null, { status: 204 });
  });
}

function cells(row: HTMLElement): HTMLElement[] {
  return within(row).getAllByRole("cell");
}

function rowFor(name: string): HTMLElement {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

/** The `<details>` wrapper of a named assumptions section. */
function sectionDetails(name: string): HTMLElement {
  const summary = screen
    .getAllByText(name)
    .find((node) => node.tagName === "SUMMARY") as HTMLElement;
  return summary.closest("details") as HTMLElement;
}

/** The live "Select 2–5 valid scenarios (n selected)." caption. */
function selectionCaption(): string {
  return screen.getByText(/valid scenarios \(\d+ selected\)/).textContent ?? "";
}

/** The annual uncertainty checkpoint rows, header excluded. */
function checkpointRows(): HTMLElement[] {
  const bands = screen.getByText("Annual uncertainty checkpoints")
    .parentElement as HTMLElement;
  return within(bands).getAllByRole("row").slice(1);
}

/** Optimization candidate rows; the card title is an icon+text span. */
function candidateRows(): HTMLElement[] {
  const table = screen
    .getByRole("columnheader", { name: "Decision set" })
    .closest("table") as HTMLElement;
  return within(table).getAllByRole("row").slice(1);
}

describe("SimulationPage editor sections", () => {
  // The editor opens on the two sections every plan starts from — the horizon
  // and the herd it stocks. Everything downstream of them stays folded, so the
  // first screen is a decision, not a wall of eighty numbers.
  it("opens the horizon and herd sections and folds the rest", async () => {
    await renderLoaded();

    expect(sectionDetails("Meta")).toHaveAttribute("open");
    expect(sectionDetails("Herd")).toHaveAttribute("open");
    expect(sectionDetails("Finance")).not.toHaveAttribute("open");
    expect(sectionDetails("Feed")).not.toHaveAttribute("open");
  });
});

describe("SimulationPage run options", () => {
  // A run option is a question the farmer can withdraw. Un-ticking one has to
  // reach the request body: a stale `true` bills a 400-run Monte Carlo the
  // operator explicitly turned off and reports figures they did not ask for.
  it("sends each run option back off once it is unticked", async () => {
    const sent: RunOptions[] = [];
    const user = await renderLoaded({ onRun: (options) => sent.push(options) });
    const labels = ["Monte Carlo", "Sensitivity", "Optimization"];

    for (const label of labels)
      await user.click(screen.getByRole("checkbox", { name: label }));
    for (const label of labels)
      expect(screen.getByRole("checkbox", { name: label })).toBeChecked();

    for (const label of labels)
      await user.click(screen.getByRole("checkbox", { name: label }));
    for (const label of labels)
      expect(screen.getByRole("checkbox", { name: label })).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      monte_carlo: false,
      sensitivity: false,
      optimization: false,
    });
  });
});

describe("SimulationPage scenario table", () => {
  // A page that came back empty while the farm still holds scenarios is a
  // vanished page, not an empty farm — saying "no saved scenarios yet" there
  // would invite the farmer to re-create work that still exists.
  it("names a vanished scenario page instead of claiming the farm has none", async () => {
    const user = await renderLoaded({
      scenarioPage: (params) => {
        const offset = Number(params.get("offset") ?? 0);
        return {
          items: offset === 0 ? scenarioRows(20) : [],
          total: 41,
          limit: 20,
          offset,
        };
      },
    });
    expect(await screen.findByText("Plan 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));

    const notice = await screen.findByText(
      "This scenario page no longer exists. Returning to the last available page…",
    );
    expect(notice).toHaveAttribute("role", "status");
    expect(screen.queryByText("No saved scenarios yet.")).not.toBeInTheDocument();
    expect(screen.queryByText("Plan 1")).not.toBeInTheDocument();
  });

  // `valid` is the server's verdict; `validation_error` is only ever its
  // explanation. A row the server re-validated must not be branded by a
  // message left over from the revision that failed.
  it("keeps a re-validated row's leftover error out of the table", async () => {
    await renderLoaded({
      scenarios: [
        scenarioRow(1, {
          name: "Expansion",
          notes: "Conservative case",
          valid: true,
          validation_error: "horizon_months must be at least 12",
        }),
      ],
    });
    expect(await screen.findByText("Expansion")).toBeInTheDocument();

    expect(screen.getByText("Conservative case")).toBeInTheDocument();
    expect(
      screen.queryByText("horizon_months must be at least 12"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Invalid saved assumptions")).not.toBeInTheDocument();
    const row = rowFor("Expansion");
    expect(within(row).getByRole("button", { name: "Load" })).toBeEnabled();
    expect(within(row).getByRole("button", { name: "Run" })).toBeEnabled();
  });
});

describe("SimulationPage compare selection", () => {
  // Un-ticking is a statement about one row. The other two selections are the
  // farmer's work and have to survive it — dropping or inverting them silently
  // compares a different set than the one on screen.
  it("unticks only the row that was clicked", async () => {
    const user = await renderLoaded({ scenarios: scenarioRows(3) });
    expect(await screen.findByText("Plan 3")).toBeInTheDocument();

    for (const id of [1, 2, 3])
      await user.click(screen.getByLabelText(`Compare Plan ${id}`));
    expect(selectionCaption()).toContain("(3 selected)");

    await user.click(screen.getByLabelText("Compare Plan 2"));

    expect(screen.getByLabelText("Compare Plan 1")).toBeChecked();
    expect(screen.getByLabelText("Compare Plan 2")).not.toBeChecked();
    expect(screen.getByLabelText("Compare Plan 3")).toBeChecked();
    expect(selectionCaption()).toContain("(2 selected)");
  });

  // The cap counts each selected row against its own stored validity. Reading
  // one row's verdict for the whole selection — the first row's, or none at
  // all — jams the fifth slot shut after a row further down stops validating.
  it("frees a slot for the row that stopped validating, not the first row", async () => {
    const scenarios = scenarioRows(7);
    const user = await renderLoaded({ scenarios });
    server.use(deleteHandler(scenarios));
    expect(await screen.findByText("Plan 7")).toBeInTheDocument();

    for (const id of [1, 2, 3, 4, 5])
      await user.click(screen.getByLabelText(`Compare Plan ${id}`));
    expect(selectionCaption()).toContain("(5 selected)");
    expect(screen.getByLabelText("Compare Plan 6")).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    // Plan 5 — the last of the five, not the first — stops validating; the
    // delete's refetch brings it back invalid while it is still selected.
    scenarios[4] = scenarioRow(5, {
      valid: false,
      validation_error: "horizon_months must be positive",
    });
    await user.click(within(rowFor("Plan 7")).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));
    await waitFor(() => expect(screen.queryByText("Plan 7")).not.toBeInTheDocument());
    expect(selectionCaption()).toContain("(4 selected)");

    await user.click(screen.getByLabelText("Compare Plan 6"));

    expect(screen.getByLabelText("Compare Plan 6")).toBeChecked();
    expect(selectionCaption()).toContain("(5 selected)");
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeEnabled();
  });

  // The compare endpoint re-checks tenant scope and stored validity, so it can
  // answer with fewer rows than were asked for — or none. An empty answer is
  // not a comparison, and it is not a failure either.
  it("draws no comparison table when the endpoint returned no rows", async () => {
    let requestedIds: string | null = null;
    const user = await renderLoaded({ scenarios: scenarioRows(2) });
    server.use(
      http.get("/api/simulation/scenarios/compare", ({ request }) => {
        requestedIds = new URL(request.url).searchParams.get("ids");
        return HttpResponse.json({ scenarios: [], results: [] });
      }),
    );
    expect(await screen.findByText("Plan 2")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));

    await waitFor(() => expect(requestedIds).toBe("1,2"));
    expect(
      await screen.findByRole("button", { name: "Compare selected" }),
    ).toBeEnabled();
    expect(screen.queryByText("Comparison")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Could not compare the selected scenarios."),
    ).not.toBeInTheDocument();
  });
});

describe("SimulationPage save dialog", () => {
  // The dialog opens on a blank form: no alert for a screen reader to
  // announce, no enabled Save to send a nameless scenario, and no trap — a
  // farmer who opened it by mistake gets out with Escape.
  it("opens quiet, refuses a blank name and closes on Escape", async () => {
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();

    const save = within(dialog).getByRole("button", { name: "Save scenario" });
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/^Name/), "   ");
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/^Name/), "Base plan");
    expect(save).toBeEnabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("SimulationPage uncertainty checkpoints", () => {
  // The checkpoints are the first month, each anniversary, and the horizon
  // itself. A 26-month run ends between anniversaries, and month 26 is exactly
  // the row a farmer reads for the ending herd and cash.
  it("checkpoints the final month of a run that ends between anniversaries", async () => {
    await runWithOptions({ ...RESULT, monte_carlo: monteCarlo() }, ["Monte Carlo"]);
    expect(
      await screen.findByText("Annual uncertainty checkpoints"),
    ).toBeInTheDocument();

    const rows = checkpointRows();
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "1",
      "12",
      "24",
      "26",
    ]);
    expect(cells(rows[3]).map((cell) => cell.textContent)).toEqual([
      "26",
      "65.0",
      "67.0",
      "69.0",
      "₹15,000",
      "₹17,000",
      "₹19,000",
    ]);
  });

  // Both bands describe the same months, so a payload whose liquidity band
  // came back empty has no month either band can vouch for. The table lists
  // nothing rather than inventing a month 0 of dashes.
  it("lists no checkpoints when one band came back empty", async () => {
    await runWithOptions(
      {
        ...RESULT,
        monte_carlo: monteCarlo({ liquidity_percentiles: band(0, 0, 0) }),
      },
      ["Monte Carlo"],
    );
    expect(
      await screen.findByText("Annual uncertainty checkpoints"),
    ).toBeInTheDocument();

    expect(checkpointRows()).toHaveLength(0);
    expect(
      screen.queryByRole("columnheader", { name: "Herd P50" }),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage optimization candidates", () => {
  // The red banner belongs to a run that found nothing to recommend. Printing
  // it over a recommendation tells the farmer their best plan is diagnostic
  // only — and a candidate that ends the horizon on exactly nothing has not
  // gone short, so its cash column stays plain.
  it("keeps the no-recommendation banner off a run that has one", async () => {
    await runWithOptions(
      {
        ...RESULT,
        optimization: {
          objective: "balanced",
          evaluated_candidates: 24,
          feasible_candidates: 3,
          baseline: candidate({ rank: 0 }),
          recommended: candidate({
            rank: 1,
            starting_does: 60,
            minimum_cash_balance: 0,
            funding_gap: 0,
          }),
          alternatives: [],
        },
      },
      ["Optimization"],
    );
    expect(
      await screen.findByRole("columnheader", { name: "Decision set" }),
    ).toBeInTheDocument();

    expect(
      screen.queryByText(/No evaluated candidate satisfies every financing/),
    ).not.toBeInTheDocument();

    const recommended = cells(candidateRows()[1]);
    expect(recommended[0]).toHaveTextContent("Recommended");
    expect(recommended[11]).toHaveTextContent("₹0");
    expect(recommended[11]).not.toHaveClass("text-destructive");
    // Styled exactly like the neutral money columns beside it.
    expect(recommended[11].className).toBe(recommended[6].className);
  });
});
