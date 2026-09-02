/**
 * Simulation page: the exact figure each headline metric changes tint at — a
 * value sitting on its bar is still healthy, and a zero is a real figure and
 * not a missing one — plus the report card and verdict badge the narrative
 * gates, the capacity warning's silent state, the break-even rows the cash
 * tables must not paint as losses, and the nested assumption values the
 * generic editor does and does not offer a control for.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

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
  herd: { does: 50, bucks: 2 },
  finance: { interest_rate_annual: 0.12 },
};

type RunBody = { assumptions: Record<string, Record<string, unknown>> };

/** Headline metrics well clear of every bar; tests override what they assert. */
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
  min_dscr: 1.6,
  payback_month: 30,
  break_even_meat_price_per_kg: 320,
  peak_capacity_head: 58,
  terminal_value: 150000,
  tax_total: 0,
  accounting_profit_total: 25000,
  minimum_cash_balance: 43000,
  minimum_cash_month: 4,
  additional_working_capital_required: 0,
  operating_margin: 0.25,
};

const FEED_SUMMARY = {
  annual_green_kg: [],
  annual_homegrown_green_kg: [],
  annual_purchased_green_kg: [],
  annual_dry_kg: [],
  annual_concentrate_kg: [],
  annual_feed_cost: [],
  annual_fodder_waste_kg_dm: [],
  land_requirement_acres: 1.75,
  fodder_deficit_months: 0,
  peak_fodder_stock_kg_dm: 0,
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
    net_cash_flow: 12000,
    cumulative_cash_flow: 12000,
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
  months: [],
  annual_pl: [],
  metrics: METRICS,
  amortization: [],
  feed_summary: FEED_SUMMARY,
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

/** Breeds + defaults + scenario list; the run POST captures its body. */
function registerApiHandlers(
  options: {
    defaults?: unknown;
    runResult?: unknown;
    onRun?: (body: RunBody) => void;
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
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return HttpResponse.json(options.runResult ?? RESULT);
    }),
  );
}

/** The editor's first paint is four round trips deep (auth refresh, farms,
 * permissions, defaults), so give the chain more room than the one second
 * findBy* allows by default. */
const LOADED = { timeout: 4000 };

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(options?: Parameters<typeof registerApiHandlers>[0]) {
  registerApiHandlers(options);
  const user = userEvent.setup();
  renderWithProviders(<SimulationPage />);
  expect(
    await screen.findByText("Horizon Months", undefined, LOADED),
  ).toBeInTheDocument();
  return user;
}

/** Render, run once, and wait for the result cards. */
async function renderWithResult(result: unknown = RESULT) {
  const user = await renderLoaded({ runResult: result });
  await user.click(screen.getByRole("button", { name: "Run simulation" }));
  expect(await screen.findByText("Project cost", undefined, LOADED)).toBeInTheDocument();
  return user;
}

function metricCard(label: string): HTMLElement {
  return screen.getByText(label).closest("[data-slot='card']") as HTMLElement;
}

/** The StatCard icon chip beside a metric label carries that metric's tint. */
function tintChip(label: string): HTMLElement {
  return metricCard(label).querySelector("span") as HTMLElement;
}

/** Semantic tint classes per historical colour name. */
const TINT_CLASSES: Record<string, [string, string]> = {
  emerald: ["bg-success-tint", "text-success-tint-foreground"],
  amber: ["bg-warning-tint", "text-warning-tint-foreground"],
  red: ["bg-destructive/10", "text-destructive"],
};

/** Assert the tint chip and the printed figure of each labelled metric. */
function expectMetrics(cases: [label: string, value: string, tint: string][]) {
  for (const [label, value, tint] of cases) {
    expect(tintChip(label)).toHaveClass(...TINT_CLASSES[tint]);
    expect(within(metricCard(label)).getByText(value)).toBeInTheDocument();
  }
}

/** Only Meta and Herd start expanded; the rest need their summary clicked. */
async function openSection(user: ReturnType<typeof userEvent.setup>, name: string) {
  const summary = screen
    .getAllByText(name)
    .find((node) => node.tagName === "SUMMARY") as HTMLElement;
  await user.click(summary);
}

// Every tint threshold in the result header is inclusive: a plan that clears
// its bar by exactly nothing is still the healthy colour, and a plan that
// misses it by a rupee (or by 0.01 of cover) is not.
describe("SimulationPage metric tint thresholds", () => {
  it("keeps a metric that lands exactly on its bar in the healthy tint", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: {
        ...METRICS,
        npv: 0,
        bcr: 1,
        avg_dscr: 1.2,
        min_dscr: 1.2,
        operating_margin: 0,
        minimum_cash_balance: 0,
        additional_working_capital_required: 0,
      },
      feed_summary: { ...FEED_SUMMARY, fodder_deficit_months: 0 },
    });

    expectMetrics([
      ["NPV", "₹0", "emerald"],
      ["BCR", "1.00", "emerald"],
      ["Avg DSCR", "1.20", "emerald"],
      ["Minimum DSCR", "1.20", "emerald"],
      ["Operating margin", "0.0%", "emerald"],
      ["Minimum cash (month 4)", "₹0", "emerald"],
      ["Additional working capital", "₹0", "emerald"],
      ["Fodder deficit months", "0", "emerald"],
    ]);
    // The two cards that dash a figure the engine could not produce still
    // print the figure it did produce.
    expect(within(metricCard("Payback month")).getByText("30")).toBeInTheDocument();
    expect(
      within(metricCard("Break-even meat (₹/kg)")).getAllByText("₹320")[0],
    ).toBeInTheDocument();
  });

  it("drops each metric one tint the moment it misses its bar", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: {
        ...METRICS,
        npv: -1,
        bcr: 0.99,
        // 1.00x cover services the debt but leaves no 1.2x headroom.
        avg_dscr: 1,
        min_dscr: 1,
        operating_margin: -0.01,
        minimum_cash_balance: -1,
        additional_working_capital_required: 1,
      },
      feed_summary: { ...FEED_SUMMARY, fodder_deficit_months: 1 },
    });

    expectMetrics([
      ["NPV", "-₹1", "red"],
      ["BCR", "0.99", "red"],
      ["Avg DSCR", "1.00", "amber"],
      ["Minimum DSCR", "1.00", "amber"],
      ["Operating margin", "-1.0%", "red"],
      ["Minimum cash (month 4)", "-₹1", "red"],
      ["Additional working capital", "₹1", "red"],
      ["Fodder deficit months", "1", "amber"],
    ]);
  });

  // A computed zero is a real (bad) figure. Confusing it with the null the
  // engine sends for "not computable" would grey out a plan that in fact
  // covers none of its debt and returns nothing on its capital.
  it("treats a zero ratio as a failing figure, not a missing one", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: {
        ...METRICS,
        bcr: 0,
        avg_dscr: 0,
        min_dscr: 0,
        payback_month: 0,
      },
    });

    expectMetrics([
      ["BCR", "0.00", "red"],
      ["Avg DSCR", "0.00", "red"],
      ["Minimum DSCR", "0.00", "red"],
    ]);
    for (const label of ["BCR", "Avg DSCR", "Minimum DSCR"]) {
      expect(tintChip(label)).not.toHaveClass("bg-muted");
      expect(tintChip(label)).not.toHaveClass("bg-warning-tint");
    }
    // Month 0 payback (recovered at commissioning) is a month, not a dash.
    const payback = metricCard("Payback month");
    expect(within(payback).getByText("0")).toBeInTheDocument();
    expect(within(payback).queryByText("—")).not.toBeInTheDocument();
  });
});

describe("SimulationPage narrative report", () => {
  it("renders no report card at all when the backend sent no narrative", async () => {
    await renderWithResult({ ...RESULT, narrative_report: [] });

    expect(screen.queryByText("Report")).not.toBeInTheDocument();
    // The rest of the result is unaffected by the missing narrative.
    expect(screen.getByText("Project cost")).toBeInTheDocument();
    expect(screen.getByText("Capital and terminal-value bridge")).toBeInTheDocument();
  });

  // The badge is the verdict section's alone, and only for a verdict the
  // backend actually spelled out: a verdict figure hung on another section, or
  // a verdict that arrived as a number, must not be painted as a ruling.
  it("badges no section when no verdict string reached the verdict section", async () => {
    await renderWithResult({
      ...RESULT,
      narrative_report: [
        {
          key: "overview",
          title: "Overview",
          paragraphs: ["A 50-doe stall-fed unit over 60 months."],
          figures: {},
        },
        {
          key: "risks",
          title: "Risks",
          paragraphs: ["Fodder prices dominate the downside."],
          figures: { verdict: "NOT VIABLE" },
        },
        {
          key: "viability_verdict",
          title: "Viability Verdict",
          paragraphs: ["The unit was scored against the viability bar."],
          figures: { verdict: 1 },
        },
      ],
    });

    const report = screen.getByText("Report").closest("[data-slot='card']") as HTMLElement;
    const body = report.querySelector("[data-slot='card-content']") as HTMLElement;
    expect(within(body).queryByText("NOT VIABLE")).not.toBeInTheDocument();
    expect(body.querySelectorAll("span")).toHaveLength(0);
    // Every section still renders its heading and its prose.
    for (const [title, paragraph] of [
      ["Overview", "A 50-doe stall-fed unit over 60 months."],
      ["Risks", "Fodder prices dominate the downside."],
      ["Viability Verdict", "The unit was scored against the viability bar."],
    ]) {
      expect(within(body).getByText(title)).toBeInTheDocument();
      expect(within(body).getByText(paragraph)).toBeInTheDocument();
    }
  });

  it("badges a qualified verdict amber on the verdict section alone", async () => {
    await renderWithResult({
      ...RESULT,
      narrative_report: [
        {
          key: "overview",
          title: "Overview",
          paragraphs: ["A 50-doe stall-fed unit over 60 months."],
          figures: { verdict: null },
        },
        {
          key: "viability_verdict",
          title: "Viability Verdict",
          paragraphs: ["The unit clears the bar only on today's meat price."],
          figures: { verdict: "VIABLE WITH CAUTION" },
        },
      ],
    });

    const badge = screen.getByText("VIABLE WITH CAUTION");
    expect(badge.tagName).toBe("SPAN");
    expect(badge).toHaveClass("bg-warning-tint", "text-warning-tint-foreground");
    expect(badge).not.toHaveClass("bg-success-tint");
    expect(badge).not.toHaveClass("bg-destructive/10");
    // The badge sits in the verdict section's heading row; no other section
    // gains one.
    const verdictSection = screen
      .getByText("Viability Verdict")
      .closest("section") as HTMLElement;
    expect(verdictSection).toContainElement(badge);
    const overview = screen.getByText("Overview").closest("section") as HTMLElement;
    expect(overview.querySelector("span")).toBeNull();
  });
});

describe("SimulationPage capacity warning", () => {
  it("stays silent when the funded places exactly meet the projected peak", async () => {
    await renderWithResult({
      ...RESULT,
      project_cost_breakdown: {
        ...RESULT.project_cost_breakdown,
        capacity_places: 52.4,
        projected_peak_head: 52.4,
      },
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/not physically feasible/)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Projected Peak basis funds 52.4 places against a projected peak of 52.4 head.",
      ),
    ).toBeInTheDocument();
  });
});

// Zero is break-even, not a loss: the destructive colour is reserved for
// figures that are actually negative, and a month with no events listed is a
// quiet month even when the backend sends an empty list rather than none.
describe("SimulationPage break-even cash rows", () => {
  const BREAK_EVEN = {
    ...RESULT,
    annual_pl: [
      annualRow({ year: 1, net_cash_flow: 0 }),
      annualRow({ year: 2, net_cash_flow: -1 }),
    ],
    months: [
      monthRow({
        month: 1,
        net_cash_flow: 0,
        cash_balance: 0,
        cumulative_cash_flow: 0,
        events: [],
      }),
      monthRow({
        month: 2,
        calendar_month: 2,
        net_cash_flow: -1,
        cash_balance: -1,
        cumulative_cash_flow: -1,
        events: ["Purchased 5 doe(s) at ₹8,000/head (₹40,000)"],
      }),
    ],
  };

  function rowsOf(title: string): HTMLElement[] {
    const card = screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
    return within(card).getAllByRole("row").slice(1);
  }

  it("marks the year that loses a rupee and leaves the break-even year plain", async () => {
    await renderWithResult(BREAK_EVEN);

    const [breakEven, loss] = rowsOf("Annual P&L");
    const netCashOf = (row: HTMLElement) => {
      const cells = within(row).getAllByRole("cell");
      return cells[cells.length - 1];
    };
    expect(netCashOf(breakEven)).toHaveTextContent("₹0");
    expect(netCashOf(breakEven)).toHaveClass("text-right", "tabular-nums");
    expect(netCashOf(breakEven)).not.toHaveClass("text-destructive");
    expect(netCashOf(loss)).toHaveTextContent("-₹1");
    expect(netCashOf(loss)).toHaveClass("text-destructive");
  });

  it("leaves a break-even month with an empty event list plain and unhighlighted", async () => {
    await renderWithResult(BREAK_EVEN);

    const [quiet, deficit] = rowsOf("Monthly projection");
    const quietCells = within(quiet).getAllByRole("cell");
    expect(quiet).not.toHaveClass("bg-warning-tint/50");
    expect(quietCells[20]).toHaveTextContent("—");
    for (const index of [16, 17]) {
      expect(quietCells[index]).toHaveTextContent("₹0");
      expect(quietCells[index]).not.toHaveClass("text-destructive");
    }

    const deficitCells = within(deficit).getAllByRole("cell");
    expect(deficit).toHaveClass("bg-warning-tint/50");
    expect(deficitCells[20]).toHaveTextContent(
      "Purchased 5 doe(s) at ₹8,000/head (₹40,000)",
    );
    for (const index of [16, 17]) {
      expect(deficitCells[index]).toHaveTextContent("-₹1");
      expect(deficitCells[index]).toHaveClass("text-destructive");
    }
  });
});

describe("SimulationPage nested assumption controls", () => {
  it("commits a nested flag the operator switches on as a real true", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        risk: { meat_price: { enabled: false, low: 0.8, high: 1.2 } },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });
    await openSection(user, "Risk");

    const enabled = screen.getByRole("checkbox", { name: "Enabled" });
    expect(enabled).not.toBeChecked();
    await user.click(enabled);
    expect(enabled).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Project cost", undefined, LOADED)).toBeInTheDocument();
    expect(captured.body?.assumptions.risk?.meat_price).toEqual({
      enabled: true,
      low: 0.8,
      high: 1.2,
    });
  });

  // The editor is generic: it draws a control for the value types it knows and
  // stays out of the way of the rest. A nested value it has no control for
  // must still reach the backend exactly as it arrived, not be dropped or
  // shown as an editable blank.
  it("offers no control for a nested value it cannot edit but keeps it in the payload", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        risk: {
          meat_price: { enabled: true, low: 0.8, high: 1.2, label: "Meat", ceiling: null },
        },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });
    await openSection(user, "Risk");

    const label = screen.getByLabelText("Label");
    expect(label).toHaveAttribute("type", "text");
    expect(label).toHaveValue("Meat");
    expect(screen.getByLabelText("Low")).toHaveValue(0.8);
    expect(screen.queryByLabelText("Ceiling")).not.toBeInTheDocument();
    expect(screen.queryByText("Ceiling")).not.toBeInTheDocument();

    await user.type(label, " price");
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Project cost", undefined, LOADED)).toBeInTheDocument();
    expect(captured.body?.assumptions.risk?.meat_price).toEqual({
      enabled: true,
      low: 0.8,
      high: 1.2,
      label: "Meat price",
      ceiling: null,
    });
  });
});
