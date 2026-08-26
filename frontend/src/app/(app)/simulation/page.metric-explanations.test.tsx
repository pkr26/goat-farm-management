/**
 * Simulation page: how a finished result is painted (metric tints, the
 * per-metric explain wiring, the capital bridge, the annual P&L cash-flow
 * column and the sensitivity ranking), the generic assumptions editor's
 * value-type dispatch and per-field run gates, and the toasts and explicit
 * re-reads around scenario writes.
 */

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

type RunBody = { assumptions: Record<string, Record<string, unknown>> };

/** Healthy headline metrics; tests override only what they assert. */
const METRICS = {
  project_cost: 515000,
  loan_amount: 250000,
  subsidy_amount: 100000,
  equity: 165000,
  npv: 234567,
  irr: 0.18,
  mirr: 0.16,
  bcr: 1.42,
  dscr_per_year: [1.8],
  avg_dscr: 1.8,
  min_dscr: 1.15,
  payback_month: 30,
  break_even_meat_price_per_kg: 320,
  peak_capacity_head: 58,
  terminal_value: 158000,
  tax_total: 0,
  accounting_profit_total: 25000,
  minimum_cash_balance: 43000,
  minimum_cash_month: 7,
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

const RESULT = {
  months: [],
  annual_pl: [],
  metrics: METRICS,
  amortization: [],
  feed_summary: FEED_SUMMARY,
  project_cost_breakdown: {
    shed_cost: 210000,
    equipment_cost: 55000,
    stock_cost: 205000,
    working_capital: 45000,
    capacity_places: 58,
    capacity_basis: "projected_peak",
    projected_peak_head: 52.4,
  },
  terminal_value_breakdown: {
    livestock: 105000,
    shed: 31000,
    equipment: 6000,
    working_capital: 16000,
    total: 158000,
  },
  model_version: "3.0.0",
  assumptions_fingerprint: "0123456789abcdef0123456789abcdef",
  narrative_report: [],
  monte_carlo: null,
  sensitivity: null,
  optimization: null,
};

/** Breeds + defaults + scenario list; the run POST captures its body. */
function registerApiHandlers(
  options: {
    scenarios?: unknown[];
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
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return HttpResponse.json(options.runResult ?? RESULT);
    }),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(
  options?: Parameters<typeof registerApiHandlers>[0],
  queryClient?: QueryClient,
) {
  registerApiHandlers(options);
  const user = userEvent.setup();
  renderWithProviders(<SimulationPage />, queryClient);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  return user;
}

/** Render, run once, and wait for the result cards. */
async function renderWithResult(
  result: unknown = RESULT,
  defaults: unknown = DEFAULTS,
) {
  const user = await renderLoaded({ defaults, runResult: result });
  await user.click(screen.getByRole("button", { name: "Run simulation" }));
  expect(await screen.findByText("Project cost")).toBeInTheDocument();
  return user;
}

/** The StatCard icon chip beside a metric label carries that metric's tint. */
function tintChip(label: string): HTMLElement {
  const card = screen.getByText(label).closest("[data-slot='card']") as HTMLElement;
  return card.querySelector("span") as HTMLElement;
}

/** Only Meta and Herd start expanded; the rest need their summary clicked. */
async function openSection(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  const summary = screen
    .getAllByText(name)
    .find((node) => node.tagName === "SUMMARY") as HTMLElement;
  await user.click(summary);
}

const SCENARIO = {
  id: 8,
  farm_id: 1,
  name: "Plan B",
  notes: "Conservative case",
  assumptions: DEFAULTS,
  revision: 7,
  valid: true,
  validation_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

beforeEach(() => {
  toastMocks.success.mockClear();
  toastMocks.error.mockClear();
});

describe("SimulationPage result metric tints", () => {
  it("tints a healthy result by each metric's own threshold", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: { ...METRICS, avg_dscr: 1.8, min_dscr: 1.15 },
    });

    // NPV >= 0, BCR >= 1, avg DSCR >= 1.2, margin >= 0, cash >= 0 and no
    // extra working capital are all "healthy"; min DSCR of 1.15 covers the
    // debt service but leaves no 1.2x headroom, so it warns rather than passes.
    expect(tintChip("NPV")).toHaveClass("bg-emerald-100", "text-emerald-700");
    expect(tintChip("BCR")).toHaveClass("bg-emerald-100", "text-emerald-700");
    expect(tintChip("Avg DSCR")).toHaveClass("bg-emerald-100", "text-emerald-700");
    expect(tintChip("Minimum DSCR")).toHaveClass("bg-amber-100", "text-amber-700");
    expect(tintChip("Operating margin")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-700",
    );
    expect(tintChip("Minimum cash (month 7)")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-700",
    );
    expect(tintChip("Additional working capital")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-700",
    );
    expect(tintChip("Fodder deficit months")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-700",
    );
    // The minimum-cash card names the month the trough happened in, and the
    // fodder land requirement carries its unit.
    expect(screen.getByText("Minimum cash (month 7)")).toBeInTheDocument();
    expect(screen.getByText("1.75 acres")).toBeInTheDocument();
  });

  it("tints a failing result amber or red by the same thresholds", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: {
        ...METRICS,
        npv: -50000,
        bcr: 0.82,
        avg_dscr: 1.05,
        min_dscr: 0.5,
        operating_margin: -0.1,
        minimum_cash_balance: -5000,
        additional_working_capital_required: 25000,
      },
      feed_summary: { ...FEED_SUMMARY, fodder_deficit_months: 3 },
    });

    expect(tintChip("NPV")).toHaveClass("bg-red-100", "text-red-700");
    expect(tintChip("BCR")).toHaveClass("bg-red-100", "text-red-700");
    expect(tintChip("Avg DSCR")).toHaveClass("bg-amber-100", "text-amber-700");
    expect(tintChip("Minimum DSCR")).toHaveClass("bg-red-100", "text-red-700");
    expect(tintChip("Operating margin")).toHaveClass("bg-red-100", "text-red-700");
    expect(tintChip("Minimum cash (month 7)")).toHaveClass(
      "bg-red-100",
      "text-red-700",
    );
    expect(tintChip("Additional working capital")).toHaveClass(
      "bg-red-100",
      "text-red-700",
    );
    expect(tintChip("Fodder deficit months")).toHaveClass(
      "bg-amber-100",
      "text-amber-700",
    );
    expect(screen.getByText("-₹50,000")).toBeInTheDocument();
    expect(screen.getByText("0.82")).toBeInTheDocument();
    expect(screen.getByText("-10.0%")).toBeInTheDocument();
    expect(screen.getByText("-₹5,000")).toBeInTheDocument();
  });

  it("tints both debt-service cards emerald when every year clears 1.2x cover", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: { ...METRICS, avg_dscr: 1.8, min_dscr: 1.35 },
    });

    expect(tintChip("Avg DSCR")).toHaveClass("bg-emerald-100", "text-emerald-700");
    expect(tintChip("Minimum DSCR")).toHaveClass("bg-emerald-100", "text-emerald-700");
    expect(screen.getByText("1.35")).toBeInTheDocument();
  });

  it("tints both debt-service cards red when debt service is not covered", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: { ...METRICS, avg_dscr: 0.95, min_dscr: 0.7 },
    });

    expect(tintChip("Avg DSCR")).toHaveClass("bg-red-100", "text-red-700");
    expect(tintChip("Minimum DSCR")).toHaveClass("bg-red-100", "text-red-700");
    expect(screen.getByText("0.95")).toBeInTheDocument();
    expect(screen.getByText("0.70")).toBeInTheDocument();
  });

  it("leaves undefined ratio metrics untinted and dashed", async () => {
    await renderWithResult({
      ...RESULT,
      metrics: {
        ...METRICS,
        bcr: null,
        avg_dscr: null,
        min_dscr: null,
        operating_margin: null,
        break_even_meat_price_per_kg: null,
      },
    });

    for (const label of ["BCR", "Avg DSCR", "Minimum DSCR", "Operating margin"]) {
      expect(tintChip(label)).toHaveClass("bg-muted", "text-muted-foreground");
      expect(tintChip(label)).not.toHaveClass("bg-emerald-100");
      expect(tintChip(label)).not.toHaveClass("bg-amber-100");
      expect(tintChip(label)).not.toHaveClass("bg-red-100");
    }
    // A break-even price the engine could not compute reads as a dash, not
    // as ₹0 or an empty card.
    const breakEven = screen
      .getByText("Break-even meat (₹/kg)")
      .closest("[data-slot='card']") as HTMLElement;
    expect(within(breakEven).getByText("—")).toBeInTheDocument();
  });
});

describe("SimulationPage metric explanations", () => {
  const EXPLAINED: [string, string][] = [
    ["npv", "NPV"],
    ["irr", "IRR"],
    ["mirr", "MIRR"],
    ["bcr", "BCR"],
    ["avg_dscr", "Avg DSCR"],
    ["min_dscr", "Minimum DSCR"],
    ["operating_margin", "Operating margin"],
    ["payback_month", "Payback month"],
    ["subsidy_amount", "Subsidy"],
    ["equity", "Equity"],
    ["peak_capacity_head", "Funded capacity (head)"],
    ["terminal_value", "Terminal value"],
    ["minimum_cash_balance", "Minimum cash (month 7)"],
    ["additional_working_capital_required", "Additional working capital"],
  ];

  it("wires an explain button to every metric the backend explained", async () => {
    const user = await renderWithResult({
      ...RESULT,
      metric_explanations: EXPLAINED.map(([key, label]) => ({
        key,
        title: `About ${label}`,
        explanation: `How ${label} was derived.`,
        figures: {},
      })),
    });

    for (const [, label] of EXPLAINED)
      expect(
        screen.getByRole("button", { name: `Explain ${label}` }),
      ).toBeInTheDocument();
    // Metrics the backend did not explain get no info button at all.
    for (const label of [
      "Project cost",
      "Loan",
      "Break-even meat (₹/kg)",
      "Fodder land required",
      "Fodder deficit months",
    ])
      expect(
        screen.queryByRole("button", { name: `Explain ${label}` }),
      ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Explain Minimum DSCR" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("About Minimum DSCR")).toBeInTheDocument();
    expect(
      within(dialog).getByText("How Minimum DSCR was derived."),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage result tables", () => {
  it("summarises the funded capacity and bridges every capital component", async () => {
    await renderWithResult();

    const bridge = screen
      .getByText("Capital and terminal-value bridge")
      .closest("[data-slot='card']") as HTMLElement;
    expect(
      within(bridge).getByText(
        "Projected Peak basis funds 58.0 places against a projected peak of 52.4 head.",
      ),
    ).toBeInTheDocument();

    const bridged: [string, string, string][] = [
      ["Shed", "₹2,10,000", "₹31,000"],
      ["Equipment", "₹55,000", "₹6,000"],
      ["Livestock", "₹2,05,000", "₹1,05,000"],
      ["Working capital", "₹45,000", "₹16,000"],
      ["Total", "₹5,15,000", "₹1,58,000"],
    ];
    for (const [label, opening, closing] of bridged) {
      const row = within(bridge).getByText(label).closest("tr") as HTMLElement;
      expect(within(row).getByText(opening)).toBeInTheDocument();
      expect(within(row).getByText(closing)).toBeInTheDocument();
    }
    // Every component is listed in the funding order the backend reports.
    expect(
      within(bridge)
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[0].textContent),
    ).toEqual(["Shed", "Equipment", "Livestock", "Working capital", "Total"]);
  });

  it("marks only a negative annual net cash flow as destructive", async () => {
    await renderWithResult({
      ...RESULT,
      annual_pl: [1, 2].map((year) => ({
        year,
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
        net_cash_flow: year === 1 ? 30000 : -12000,
      })),
    });

    const annual = screen
      .getByText("Annual P&L")
      .closest("[data-slot='card']") as HTMLElement;
    const netCashOf = (year: string) => {
      const row = within(annual).getByText(year).closest("tr") as HTMLElement;
      const cells = within(row).getAllByRole("cell");
      return cells[cells.length - 1];
    };
    expect(netCashOf("1")).toHaveTextContent("₹30,000");
    expect(netCashOf("1")).toHaveClass("text-right", "tabular-nums");
    expect(netCashOf("1")).not.toHaveClass("text-destructive");
    expect(netCashOf("2")).toHaveTextContent("-₹12,000");
    expect(netCashOf("2")).toHaveClass("text-right", "tabular-nums", "text-destructive");
  });

  it("ranks sensitivity parameters by their largest absolute NPV swing", async () => {
    await renderWithResult({
      ...RESULT,
      sensitivity: [
        { parameter: "meat_price_per_kg", delta_npv_low: -50000, delta_npv_high: 60000 },
        { parameter: "feed_cost", delta_npv_low: -120000, delta_npv_high: 90000 },
        { parameter: "conception_rate", delta_npv_low: -10000, delta_npv_high: 15000 },
      ],
    });

    const sensitivity = screen
      .getByText("Sensitivity (ΔNPV)")
      .closest("[data-slot='card']") as HTMLElement;
    // Feed cost swings NPV by 1.2 L against meat price's 60 k, so it leads
    // even though the backend listed it second.
    expect(
      within(sensitivity)
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[0].textContent),
    ).toEqual(["Feed Cost", "Meat Price Per Kg", "Conception Rate"]);
    const leader = within(sensitivity).getByText("Feed Cost").closest("tr") as HTMLElement;
    expect(within(leader).getByText("-₹1,20,000")).toHaveClass("text-destructive");
    expect(within(leader).getByText("₹90,000")).not.toHaveClass("text-destructive");
  });
});

describe("SimulationPage narrative verdict badge", () => {
  async function renderVerdict(verdict: string) {
    await renderWithResult({
      ...RESULT,
      narrative_report: [
        {
          key: "viability_verdict",
          title: "Viability Verdict",
          paragraphs: ["The unit was assessed against the viability bar."],
          figures: { verdict },
        },
      ],
    });
    return screen.getByText(verdict);
  }

  it("paints a clear pass emerald", async () => {
    const badge = await renderVerdict("VIABLE");
    expect(badge).toHaveClass(
      "rounded-full",
      "px-2",
      "py-0.5",
      "text-xs",
      "font-medium",
      "bg-emerald-100",
      "text-emerald-700",
    );
  });

  it("paints a qualified pass amber", async () => {
    const badge = await renderVerdict("VIABLE WITH CAUTION");
    expect(badge).toHaveClass("bg-amber-100", "text-amber-700");
    expect(badge).not.toHaveClass("bg-emerald-100");
    expect(badge).not.toHaveClass("bg-red-100");
  });

  it("paints a failure red", async () => {
    const badge = await renderVerdict("NOT VIABLE");
    expect(badge).toHaveClass("bg-red-100", "text-red-700");
    expect(badge).not.toHaveClass("bg-emerald-100");
    expect(badge).not.toHaveClass("bg-amber-100");
  });
});

describe("SimulationPage assumption editor field types", () => {
  const OPTIONAL_CEILINGS = {
    ...DEFAULTS,
    optimization: { maximum_project_cost: 750000, maximum_funding_gap: null },
  };
  const RISK_SPREAD = {
    ...DEFAULTS,
    risk: { meat_price: { enabled: true, low: 0.8, high: 1.2 } },
  };

  it("renders an optional ceiling as a blankable input that commits no limit", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: OPTIONAL_CEILINGS,
      onRun: (body) => {
        captured.body = body;
      },
    });
    await openSection(user, "Optimization");

    const ceiling = screen.getByLabelText("Maximum Project Cost");
    expect(ceiling).toHaveValue(750000);
    expect(ceiling).toHaveAttribute("min", "0");
    expect(ceiling).toHaveAttribute("data-unit", "₹");
    expect(ceiling.closest("div")?.querySelector("p")?.textContent).toBe(
      "Unit: ₹ — blank means no limit",
    );

    // Blank is a legal value here (no ceiling), unlike every required field.
    await user.clear(ceiling);
    expect(screen.queryByText("A value is required.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Project cost")).toBeInTheDocument();
    expect(captured.body?.assumptions.optimization).toMatchObject({
      maximum_project_cost: null,
      maximum_funding_gap: null,
    });
  });

  it("bounds nested risk multipliers by the spread limits", async () => {
    const user = await renderLoaded({ defaults: RISK_SPREAD });
    await openSection(user, "Risk");

    const low = screen.getByLabelText("Low");
    const high = screen.getByLabelText("High");
    for (const input of [low, high]) {
      expect(input).toHaveAttribute("max", "100");
      // The backend bound is exclusive, so no inclusive min reaches the DOM.
      expect(input).not.toHaveAttribute("min");
      expect(input).toHaveAttribute("data-unit", "multiplier");
    }
    expect(low.closest("div")?.querySelector("p")?.textContent).toBe(
      "Unit: multiplier",
    );
    expect(high.closest("div")?.querySelector("p")?.textContent).toBe(
      "Unit: multiplier",
    );

    await user.clear(low);
    await user.type(low, "0");
    expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
    await user.clear(high);
    await user.type(high, "101");
    expect(screen.getByText("Must be at most 100.")).toBeInTheDocument();
  });

  it("renders boolean assumptions as checkboxes at both nesting levels", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...RISK_SPREAD,
        herd: { ...DEFAULTS.herd, auto_purchase_bucks: true },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });
    await openSection(user, "Risk");

    const autoPurchase = screen.getByRole("checkbox", { name: "Auto Purchase Bucks" });
    const enabled = screen.getByRole("checkbox", { name: "Enabled" });
    expect(autoPurchase).toBeChecked();
    expect(enabled).toBeChecked();

    await user.click(autoPurchase);
    await user.click(enabled);
    expect(autoPurchase).not.toBeChecked();
    expect(enabled).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Project cost")).toBeInTheDocument();
    expect(captured.body?.assumptions.herd?.auto_purchase_bucks).toBe(false);
    expect(captured.body?.assumptions.risk?.meat_price).toMatchObject({
      enabled: false,
      low: 0.8,
      high: 1.2,
    });
  });

  // The editor is generic: it renders whatever keys the defaults payload
  // carries. A textual assumption the frontend has no dropdown for must stay
  // an editable text box at both nesting levels instead of disappearing from
  // the form — and from the payload the run is built out of.
  it("renders unrecognised textual assumptions as plain text boxes", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        meta: { ...DEFAULTS.meta, scenario_label: "Baseline" },
        risk: { meat_price: { enabled: true, low: 0.8, high: 1.2, label: "Meat" } },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });
    await openSection(user, "Risk");

    const label = screen.getByLabelText("Scenario Label");
    const nested = screen.getByLabelText("Label");
    expect(label).toHaveAttribute("type", "text");
    expect(label).toHaveValue("Baseline");
    expect(nested).toHaveAttribute("type", "text");
    expect(nested).toHaveValue("Meat");

    await user.type(label, " v2");
    await user.type(nested, " price");

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("Project cost")).toBeInTheDocument();
    expect(captured.body?.assumptions.meta?.scenario_label).toBe("Baseline v2");
    expect(captured.body?.assumptions.risk?.meat_price).toMatchObject({
      label: "Meat price",
    });
  });
});

describe("SimulationPage start month picker", () => {
  const ERROR_ID = "sim-meta-start_year_month-error";
  const FIELD_ERROR = "Enter a real month from 1900-01 to 2200-12.";

  it("renders the start month as a bounded month picker", async () => {
    await renderLoaded();

    const start = screen.getByLabelText("Start Year Month");
    expect(start).toHaveAttribute("type", "month");
    expect(start).toHaveAttribute("min", "1900-01");
    expect(start).toHaveAttribute("max", "2200-12");
    expect(start).not.toHaveAttribute("aria-invalid");
    expect(start).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByText(FIELD_ERROR)).not.toBeInTheDocument();
  });

  it("accepts every month the backend's own year range allows", async () => {
    await renderLoaded();
    const start = screen.getByLabelText("Start Year Month");

    for (const month of ["1900-01", "1999-05", "2026-10", "2026-12", "2150-06", "2200-12"]) {
      fireEvent.change(start, { target: { value: month } });
      expect(screen.queryByText(FIELD_ERROR)).not.toBeInTheDocument();
      expect(start).not.toHaveAttribute("aria-invalid");
      expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
    }
  });

  it("flags a stored start month with a trailing digit the picker cannot show", async () => {
    await renderLoaded({
      defaults: { ...DEFAULTS, meta: { ...DEFAULTS.meta, start_year_month: "2026-013" } },
    });

    const start = screen.getByLabelText("Start Year Month");
    expect(start).toHaveAttribute("aria-invalid", "true");
    expect(start).toHaveAttribute("aria-describedby", ERROR_ID);
    const alert = screen.getByText(FIELD_ERROR);
    expect(alert).toHaveAttribute("id", ERROR_ID);
    expect(alert).toHaveAttribute("role", "alert");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("flags a stored start month whose year is padded", async () => {
    await renderLoaded({
      defaults: { ...DEFAULTS, meta: { ...DEFAULTS.meta, start_year_month: "02026-01" } },
    });

    const start = screen.getByLabelText("Start Year Month");
    expect(start).toHaveAttribute("aria-invalid", "true");
    expect(start).toHaveAttribute("aria-describedby", ERROR_ID);
    expect(screen.getByText(FIELD_ERROR)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

// Every editor input reports its validity under its own key. A shared key
// would let a keystroke in any other field clear an outstanding error and
// re-open Run/Save on assumptions the backend would reject.
describe("SimulationPage per-field run gates", () => {
  const runButton = () => screen.getByRole("button", { name: "Run simulation" });

  it("keeps a numeric field's error while a sibling number is edited", async () => {
    const user = await renderLoaded();
    const rate = screen.getByLabelText("Interest Rate Annual");
    await user.clear(rate);
    await user.type(rate, "0.9");
    expect(screen.getByText("Must be at most 0.5.")).toBeInTheDocument();

    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "51");

    expect(screen.getByText("Must be at most 0.5.")).toBeInTheDocument();
    expect(rate).toHaveAttribute("aria-invalid", "true");
    expect(runButton()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeDisabled();

    await user.clear(rate);
    await user.type(rate, "0.11");
    expect(runButton()).toBeEnabled();
  });

  it("keeps an optional ceiling's error while the other ceiling is edited", async () => {
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        optimization: { maximum_project_cost: 750000, maximum_funding_gap: null },
      },
    });
    await openSection(user, "Optimization");

    const ceiling = screen.getByLabelText("Maximum Project Cost");
    await user.clear(ceiling);
    await user.type(ceiling, "-1");
    expect(screen.getByText("Must be at least 0.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.type(screen.getByLabelText("Maximum Funding Gap"), "5000");

    expect(screen.getByText("Must be at least 0.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.clear(ceiling);
    expect(runButton()).toBeEnabled();
  });

  it("keeps an array field's error while a sibling array is edited", async () => {
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        sales: {
          monthly_meat_price_multipliers: Array(12).fill(1),
          festival_sale_months: [],
        },
      },
    });
    await openSection(user, "Sales");

    const monthly = screen.getByLabelText(/Monthly Meat Price Multipliers/);
    await user.clear(monthly);
    await user.type(monthly, "1,1");
    const lengthError = "Enter exactly 12 monthly multipliers (Jan-Dec).";
    expect(screen.getByText(lengthError)).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.type(screen.getByLabelText(/Festival Sale Months/), "12");

    expect(screen.getByText(lengthError)).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.clear(monthly);
    await user.type(monthly, "1,1,1,1,1,1,1,1,1,1,1,1");
    expect(runButton()).toBeEnabled();
  });

  it("keeps a nested parameter's error while its sibling is edited", async () => {
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        risk: { meat_price: { enabled: true, low: 0.8, high: 1.2 } },
      },
    });
    await openSection(user, "Risk");

    const low = screen.getByLabelText("Low");
    await user.clear(low);
    await user.type(low, "-1");
    expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    const high = screen.getByLabelText("High");
    await user.clear(high);
    await user.type(high, "1.3");

    expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    await user.clear(low);
    await user.type(low, "0.9");
    expect(runButton()).toBeEnabled();
  });
});

describe("SimulationPage scenario write feedback", () => {
  it("announces a deleted scenario", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.delete("/api/simulation/scenarios/8", () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    const user = await renderLoaded({ scenarios: [SCENARIO] });

    const row = (await screen.findByText("Plan B")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Scenario deleted."),
    );
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("reports a delete that never reached the server and keeps the row", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(
      http.delete("/api/simulation/scenarios/8", () => HttpResponse.error()),
    );
    const user = await renderLoaded({ scenarios: [SCENARIO] });

    const row = (await screen.findByText("Plan B")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not delete the scenario."),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByText("Plan B")).toBeInTheDocument();
  });

  it("announces a saved scenario and reopens the dialog empty", async () => {
    const scenarios: unknown[] = [];
    server.use(
      http.post("/api/simulation/scenarios", async ({ request }) => {
        const body = (await request.json()) as {
          name: string;
          notes?: string;
          assumptions: unknown;
        };
        const created = {
          ...SCENARIO,
          id: 1,
          name: body.name,
          notes: body.notes ?? "",
          assumptions: body.assumptions,
          revision: 1,
        };
        scenarios.push(created);
        return HttpResponse.json(created, { status: 201 });
      }),
    );
    const user = await renderLoaded({ scenarios });

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Name/), "Base plan");
    await user.type(within(dialog).getByLabelText("Notes"), "Conservative case");
    await user.click(within(dialog).getByRole("button", { name: "Save scenario" }));

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Scenario saved."),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // The next save starts from a blank form, not from the previous name.
    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByLabelText(/^Name/)).toHaveValue("");
    expect(within(reopened).getByLabelText("Notes")).toHaveValue("");
    expect(
      within(reopened).getByRole("button", { name: "Save scenario" }),
    ).toBeDisabled();
  });

  it("reports a save that never reached the server without losing the form", async () => {
    server.use(
      http.post("/api/simulation/scenarios", () => HttpResponse.error()),
    );
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Name/), "Base plan");
    await user.click(within(dialog).getByRole("button", { name: "Save scenario" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not save the scenario.",
    );
    expect(toastMocks.error).toHaveBeenCalledWith("Could not save the scenario.");
    expect(within(dialog).getByLabelText(/^Name/)).toHaveValue("Base plan");
  });

  it("announces an updated scenario", async () => {
    server.use(
      http.patch("/api/simulation/scenarios/8", async ({ request }) => {
        const body = (await request.json()) as { expected_revision: number };
        return HttpResponse.json({
          ...SCENARIO,
          revision: body.expected_revision + 1,
        });
      }),
    );
    const user = await renderLoaded({ scenarios: [SCENARIO] });

    await user.click(await screen.findByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Plan B" }));

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Scenario updated."),
    );
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("explains the refreshed editor after a conflicting update", async () => {
    server.use(
      http.patch("/api/simulation/scenarios/8", () =>
        HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 }),
      ),
      http.get("/api/simulation/scenarios/8", () =>
        HttpResponse.json({
          ...SCENARIO,
          revision: 8,
          assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 88 } },
        }),
      ),
    );
    const user = await renderLoaded({ scenarios: [SCENARIO] });

    await user.click(await screen.findByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Plan B" }));

    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(88));
    expect(toastMocks.error).toHaveBeenCalledWith(
      "This scenario changed elsewhere. The editor was refreshed to the latest revision; review it before saving again.",
    );
    // The raw conflict detail is replaced by that explanation, not added to it.
    expect(toastMocks.error).toHaveBeenCalledTimes(1);
    expect(toastMocks.success).not.toHaveBeenCalled();
  });

  it("reports an update that never reached the server", async () => {
    server.use(
      http.patch("/api/simulation/scenarios/8", () => HttpResponse.error()),
    );
    const user = await renderLoaded({ scenarios: [SCENARIO] });

    await user.click(await screen.findByRole("button", { name: "Load" }));
    await user.click(screen.getByRole("button", { name: "Update Plan B" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not update the scenario."),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
  });
});

// The production client caches reads for 15 s. Explicitly requested
// executions must still hit the API instead of replaying that cache.
describe("SimulationPage explicit re-reads", () => {
  const cachingClient = () =>
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15_000 },
        mutations: { retry: false },
      },
    });

  it("re-runs the comparison on every compare click", async () => {
    const scenarios = [1, 2].map((id) => ({
      ...SCENARIO,
      id,
      name: `Plan ${id}`,
      revision: 1,
    }));
    let compareCalls = 0;
    server.use(
      http.get("/api/simulation/scenarios/compare", () => {
        compareCalls += 1;
        return HttpResponse.json({ scenarios, results: [RESULT, RESULT] });
      }),
    );
    const user = await renderLoaded({ scenarios }, cachingClient());

    await user.click(await screen.findByLabelText("Compare Plan 1"));
    await user.click(screen.getByLabelText("Compare Plan 2"));
    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    expect(await screen.findByText("Comparison")).toBeInTheDocument();
    await waitFor(() => expect(compareCalls).toBe(1));

    await user.click(screen.getByRole("button", { name: "Compare selected" }));
    await waitFor(() => expect(compareCalls).toBe(2));
  });

  it("re-reads the conflicting scenario on every recovery", async () => {
    let reads = 0;
    server.use(
      http.patch("/api/simulation/scenarios/8", () =>
        HttpResponse.json({ detail: "scenario changed concurrently" }, { status: 409 }),
      ),
      http.get("/api/simulation/scenarios/8", () => {
        reads += 1;
        return HttpResponse.json({
          ...SCENARIO,
          revision: 7 + reads,
          assumptions: {
            ...DEFAULTS,
            herd: { ...DEFAULTS.herd, does: reads === 1 ? 88 : 99 },
          },
        });
      }),
    );
    const user = await renderLoaded({ scenarios: [SCENARIO] }, cachingClient());

    await user.click(await screen.findByRole("button", { name: "Load" }));
    const update = screen.getByRole("button", { name: "Update Plan B" });
    await user.click(update);
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(88));

    // A second conflict must show the newest revision, not the one this
    // recovery already cached.
    await user.click(update);
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(99));
    expect(reads).toBe(2);
  });
});
