/**
 * Simulation page: result and editor behaviours the other suites leave open —
 * the sensitivity ranking measured on the widest swing, the truncated
 * assumptions fingerprint, a verdict section that carries no figures, the
 * save dialog's dismissal and blank-name gate, the uncertainty
 * checkpoints across mismatched percentile bands, a repeated
 * optimizer alternative, a comparison payload with unnamed columns, and the
 * ordering guard that decides whether a late herd snapshot may still land in
 * the editor.
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

/** Percentile band of `length` months; each percentile sits one step apart. */
function band(length: number, base: number, step: number) {
  const series = (offset: number) =>
    Array.from({ length }, (_, index) => base + offset * step + index * step);
  return {
    p5: series(0),
    p25: series(1),
    p50: series(2),
    p75: series(3),
    p95: series(4),
  };
}

function monteCarlo(overrides: Record<string, unknown> = {}) {
  return {
    runs: 500,
    seed: 42,
    herd_percentiles: band(24, 40, 1),
    cash_percentiles: band(24, -10000, 1000),
    liquidity_percentiles: band(24, -10000, 1000),
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
    npv_histogram_counts: Array.from({ length: 20 }, () => 25),
    npv_histogram_edges: Array.from({ length: 21 }, (_, i) => -100000 + i * 25000),
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

const STALE_SNAPSHOT =
  "The editor was reloaded while the herd snapshot was loading. Click “Use current herd” again to apply it.";

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

function cells(row: HTMLElement): HTMLElement[] {
  return within(row).getAllByRole("cell");
}

describe("SimulationPage sensitivity ranking", () => {
  // The ranking answers "which assumption can move NPV furthest", so each
  // parameter is measured on the wider of its two swings. Ranking on the
  // narrower one reverses this table exactly.
  it("ranks a parameter by its widest swing, not its narrowest", async () => {
    await runAdHoc({
      runResult: {
        ...RESULT,
        sensitivity: [
          {
            parameter: "conception_rate",
            delta_npv_low: -70000,
            delta_npv_high: 150000,
            label_low: "-10%",
            label_high: "+10%",
          },
          {
            parameter: "feed_price_per_kg",
            delta_npv_low: -80000,
            delta_npv_high: 120000,
            label_low: "-10%",
            label_high: "+10%",
          },
          {
            parameter: "kid_mortality_rate",
            delta_npv_low: -90000,
            delta_npv_high: 100000,
            label_low: "-10%",
            label_high: "+10%",
          },
          {
            parameter: "meat_price_per_kg",
            delta_npv_low: -60000,
            delta_npv_high: 200000,
            label_low: "-10%",
            label_high: "+10%",
          },
        ],
      },
    });

    const rows = within(cardOf("Sensitivity (ΔNPV)")).getAllByRole("row").slice(1);
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Meat Price Per Kg",
      "Conception Rate",
      "Feed Price Per Kg",
      "Kid Mortality Rate",
    ]);
    // The widest swing tops the table even though its downside is the mildest
    // of the four.
    expect(cells(rows[0])[1]).toHaveTextContent("-₹60,000");
    expect(cells(rows[0])[2]).toHaveTextContent("₹2,00,000");
    expect(cells(rows[3])[1]).toHaveTextContent("-₹90,000");
  });
});

describe("SimulationPage result provenance", () => {
  // The fingerprint is a 32-character digest: printed in full it swamps the
  // provenance line, so the page shows a 12-character prefix and keeps the
  // whole value in the tooltip for anyone matching it against a backend log.
  it("shows a twelve-character fingerprint prefix and the full digest as its title", async () => {
    await runAdHoc();

    const fingerprint = screen.getByTitle(
      "0123456789abcdef0123456789abcdef",
    ) as HTMLElement;
    expect(fingerprint.textContent).toBe("0123456789ab");
    expect(screen.getByText(/assumptions fingerprint/).textContent).toBe(
      "Model 3.0.0 · assumptions fingerprint 0123456789ab",
    );
  });
});

describe("SimulationPage narrative report", () => {
  // The verdict badge is driven by a figure the section may simply not carry;
  // a report section without figures is still a section, not a crash.
  it("renders a verdict section that carries no figures, without a badge", async () => {
    await runAdHoc({
      runResult: {
        ...RESULT,
        narrative_report: [
          {
            key: "viability_verdict",
            title: "Viability Verdict",
            paragraphs: ["The unit clears the viability bar."],
          },
          {
            key: "risks",
            title: "Risks",
            paragraphs: ["Meat price swings are the main risk."],
            figures: {},
          },
        ],
      },
    });

    const report = cardOf("Report");
    expect(within(report).getByText("Viability Verdict")).toBeInTheDocument();
    expect(
      within(report).getByText("The unit clears the viability bar."),
    ).toBeInTheDocument();
    expect(within(report).getByText("Risks")).toBeInTheDocument();
    expect(within(report).queryByText("VIABLE")).not.toBeInTheDocument();
  });
});

describe("SimulationPage save dialog", () => {
  it("refuses a name of only spaces and closes on the dialog's own dismiss", async () => {
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Save scenario" });
    const name = within(dialog).getByLabelText(/^Name/);
    expect(save).toBeDisabled();

    // Whitespace is not a name: the payload would carry an empty one.
    await user.type(name, "   ");
    expect(name).toHaveValue("   ");
    expect(save).toBeDisabled();

    await user.type(name, "Base plan");
    expect(save).toBeEnabled();

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});

describe("SimulationPage uncertainty checkpoints", () => {
  async function runMonteCarlo(overrides: Record<string, unknown>) {
    const user = await renderLoaded({
      runResult: { ...RESULT, monte_carlo: monteCarlo(overrides) },
    });
    await user.click(screen.getByRole("checkbox", { name: "Monte Carlo" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByText("Annual uncertainty checkpoints"),
    ).toBeInTheDocument();
    return screen.getByText("Annual uncertainty checkpoints")
      .parentElement as HTMLElement;
  }

  // The two bands are separate arrays on the wire. Checkpointing past the
  // shorter one prints em dashes for cash the run never projected, so the
  // table stops at the last month both bands describe.
  it("checkpoints only the months both percentile bands cover", async () => {
    const bands = await runMonteCarlo({
      herd_percentiles: band(24, 40, 1),
      liquidity_percentiles: band(14, -10000, 1000),
      cash_percentiles: band(14, -10000, 1000),
    });

    const rows = within(bands).getAllByRole("row").slice(1);
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual(["1", "12", "14"]);
    expect(cells(rows[2]).map((cell) => cell.textContent)).toEqual([
      "14",
      "53.0",
      "55.0",
      "57.0",
      "₹3,000",
      "₹5,000",
      "₹7,000",
    ]);
  });

  it("lists no checkpoints at all when the percentile bands are empty", async () => {
    const bands = await runMonteCarlo({
      herd_percentiles: band(0, 40, 1),
      liquidity_percentiles: band(0, -10000, 1000),
      cash_percentiles: band(0, -10000, 1000),
    });

    // Header only: month 0 and a negative last index are not checkpoints.
    expect(within(bands).getAllByRole("row")).toHaveLength(1);
    expect(within(bands).queryByRole("cell")).not.toBeInTheDocument();
  });
});

describe("SimulationPage optimization alternatives", () => {
  it("lists a repeated alternative decision set once", async () => {
    const repeated = { starting_does: 70, starting_bucks: 4 };
    const user = await renderLoaded({
      runResult: {
        ...RESULT,
        optimization: {
          objective: "balanced",
          evaluated_candidates: 24,
          feasible_candidates: 4,
          baseline: candidate({ rank: 0 }),
          recommended: candidate({ rank: 1, starting_does: 60, npv: 310000 }),
          // The optimizer can evaluate the same decision set twice under
          // different perturbations; the table describes decision sets.
          alternatives: [
            candidate({ rank: 2, ...repeated, npv: 280000 }),
            candidate({ rank: 3, ...repeated, npv: 280000 }),
          ],
        },
      },
    });
    await user.click(screen.getByRole("checkbox", { name: "Optimization" }));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByRole("columnheader", { name: "Decision set" }),
    ).toBeInTheDocument();

    const table = screen
      .getByRole("columnheader", { name: "Decision set" })
      .closest("table") as HTMLElement;
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => cells(row)[0].textContent)).toEqual([
      "Baseline",
      "Recommended",
      "Alternative 2",
    ]);
    expect(cells(rows[2])[2].textContent).toBe("70 / 4 / 100");
  });
});

describe("SimulationPage comparison table", () => {
  // The comparison endpoint answers with a scenario list and a result list.
  // A result the scenario list does not name still belongs in the table —
  // dropping the column, or indexing blindly into the names, loses figures
  // the farmer asked for.
  it("keeps a result column the compare payload never names", async () => {
    const scenarios = [
      scenario({ id: 1, name: "Plan A" }),
      scenario({ id: 2, name: "Plan B" }),
    ];
    const user = await renderLoaded({ scenarios });
    server.use(
      http.get("/api/simulation/scenarios/compare", () =>
        HttpResponse.json({
          scenarios,
          results: [
            RESULT,
            { ...RESULT, metrics: { ...RESULT.metrics, npv: -12000 } },
            { ...RESULT, metrics: { ...RESULT.metrics, npv: 987654 } },
          ],
        }),
      ),
    );

    await user.click(screen.getByLabelText("Compare Plan A"));
    await user.click(screen.getByLabelText("Compare Plan B"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));

    const comparison = (await screen.findByText("Comparison"))
      .parentElement as HTMLElement;
    expect(
      within(comparison)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Metric", "Plan A", "Plan B"]);
    const npvRow = within(comparison).getByText("NPV").closest("tr") as HTMLElement;
    expect(cells(npvRow).map((cell) => cell.textContent)).toEqual([
      "NPV",
      "₹2,34,567",
      "-₹12,000",
      "₹9,87,654",
    ]);
  });
});

describe("SimulationPage late loader ordering", () => {
  // The same forward-only rule holds for the editor itself: two whole-editor
  // loads while a herd snapshot is in flight leave nothing for it to land in.
  it("discards a herd snapshot after the editor was reloaded twice", async () => {
    const user = await renderLoaded({
      scenarios: [
        scenario({
          id: 5,
          name: "Expansion",
          assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 99 } },
        }),
      ],
    });
    let releaseSnapshot!: () => void;
    server.use(
      http.get(
        "/api/simulation/herd-snapshot",
        () =>
          new Promise<Response>((resolve) => {
            releaseSnapshot = () => resolve(HttpResponse.json(SNAPSHOT));
          }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() => expect(releaseSnapshot).toBeTypeOf("function"));

    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Load defaults" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Load" }));
    expect(screen.getByLabelText("Does")).toHaveValue(99);

    releaseSnapshot();
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(STALE_SNAPSHOT),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(99);
    expect(screen.getByText("Editing scenario: Expansion")).toBeInTheDocument();
  });
});

describe("SimulationPage horizon presets", () => {
  // Each preset replaces whatever is in the horizon field, including a draft
  // the field itself rejected — otherwise the second preset leaves a refused
  // number on screen while the payload carries something else entirely.
  it("replaces a rejected horizon draft for every preset in turn", async () => {
    const user = await renderLoaded();
    const horizon = () => screen.getByLabelText("Horizon Months");

    await user.clear(horizon());
    await user.type(horizon(), "999");
    expect(screen.getByText("Must be at most 240.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "5 yr" }));
    expect(horizon()).toHaveValue(60);
    expect(screen.queryByText("Must be at most 240.")).not.toBeInTheDocument();

    await user.clear(horizon());
    await user.type(horizon(), "999");
    expect(screen.getByText("Must be at most 240.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "10 yr" }));
    expect(horizon()).toHaveValue(120);
    expect(screen.queryByText("Must be at most 240.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });
});
