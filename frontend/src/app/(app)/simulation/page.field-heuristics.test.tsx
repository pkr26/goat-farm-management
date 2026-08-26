/**
 * Simulation page: the naming heuristics that caption and bound the backend
 * fields no explicit table covers, inline-error wiring on the numeric inputs,
 * horizon-driven re-validation of a blurred draft, herd-event validation of a
 * stored payload, the empty and full-length festival month lists, non-finite
 * figures arriving from the API, and the explanation figures that are already
 * labels.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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

/** Placeholder swapped for the JSON literal `1e999` on the wire: a payload
 *  can only carry a magnitude past float64 as an over-long exponent, which
 *  JSON.parse turns into Infinity. JSON.stringify would emit null instead. */
const BEYOND_FLOAT64 = "__beyond_float64__";

function jsonBody(body: unknown): HttpResponse<string> {
  return new HttpResponse(
    JSON.stringify(body).replaceAll(`"${BEYOND_FLOAT64}"`, "1e999"),
    { headers: { "content-type": "application/json" } },
  );
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
};

const RESULT = {
  months: [monthRow()],
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
  metric_explanations: [
    {
      key: "project_cost",
      title: "Project Cost",
      explanation: "Total project cost is ₹5,00,000 at month 0.",
      // capacity_basis is a real backend figure (explain.py) that arrives as
      // an already-worded label rather than a number.
      figures: {
        project_cost: 500000,
        capacity_places: 58,
        capacity_basis: "projected_peak",
      },
    },
  ],
  narrative_report: [],
  monte_carlo: null,
  sensitivity: null,
  optimization: null,
};

interface RunBody {
  assumptions: {
    meta?: { horizon_months?: number };
    sales?: { festival_sale_months?: number[] };
    growth?: { birth_weight_kg?: number; weight_by_age_months?: number[] };
    events?: Array<Record<string, unknown>>;
  };
  monte_carlo: boolean;
  sensitivity: boolean;
  optimization: boolean;
}

function registerApiHandlers(
  options: {
    defaults?: unknown;
    onRun?: (body: RunBody) => void;
    runResult?: unknown;
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
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
    http.get("/api/simulation/calibration", () =>
      jsonBody(options.calibrationResult ?? {}),
    ),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return jsonBody(options.runResult ?? RESULT);
    }),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(options?: Parameters<typeof registerApiHandlers>[0]) {
  registerApiHandlers(options);
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
}

/** Visible unit caption under an assumption input. */
function unitOf(label: string): string | null | undefined {
  return screen.getByLabelText(label).closest("div")?.querySelector("p")?.textContent;
}

describe("SimulationPage assumption unit and bound heuristics", () => {
  it("captions the fields the explicit unit table leaves to the naming chain", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        culling: { buck_rotation_years: 3 },
        sales: {
          manure_income_per_adult_per_year: 1500,
          lactation_milk_litres: 120,
        },
        feed: {
          concentrate_price_per_kg: 32,
          concentrate_dm_pct: 0.9,
          cultivated_fodder_acres: 2,
        },
        costs: { vet_per_animal_per_year: 900, shed_useful_life_years: 20 },
        risk: { seed: 42 },
      },
    });

    // Rates of money per period stay money; only genuine durations read as
    // years.
    expect(unitOf("Vet Per Animal Per Year")).toBe("Unit: ₹/yr");
    expect(unitOf("Manure Income Per Adult Per Year")).toBe("Unit: ₹/yr");
    expect(unitOf("Shed Useful Life Years")).toBe("Unit: years");
    expect(unitOf("Buck Rotation Years")).toBe("Unit: years");
    expect(unitOf("Lactation Milk Litres")).toBe("Unit: litres");
    expect(unitOf("Cultivated Fodder Acres")).toBe("Unit: acres");
    // A head count and a PRNG seed carry no unit at all — the trailing
    // fraction test must not claim them.
    expect(screen.getByLabelText("Does")).not.toHaveAttribute("data-unit");
    expect(screen.getByLabelText("Seed")).not.toHaveAttribute("data-unit");
  });

  // "concentrate" contains "rate", and every rate-shaped heuristic in the
  // chain sits ahead of the feed branch. Only their section tests keep a
  // ₹32/kg price enterable and a dry-matter share 0-exclusive.
  it("bounds concentrate fields by their own contract, not the rate heuristics", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        feed: { concentrate_price_per_kg: 32, concentrate_dm_pct: 0.9 },
      },
    });

    const price = screen.getByLabelText("Concentrate Price Per Kg");
    expect(price).toHaveAttribute("min", "0");
    expect(price).toHaveAttribute("max", "1000000000");
    expect(price).toHaveAttribute("data-unit", "₹");

    const dryMatter = screen.getByLabelText("Concentrate Dm Pct");
    expect(dryMatter).toHaveAttribute("max", "1");
    expect(dryMatter).toHaveAttribute("data-unit", "fraction");
    // Exclusive minimum: no `min` attribute, and 0 is refused.
    expect(dryMatter).not.toHaveAttribute("min");
    fireEvent.change(dryMatter, { target: { value: "0" } });
    expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
  });
});

describe("SimulationPage numeric input wiring", () => {
  it("points the input at its own inline error only while that error stands", async () => {
    await renderLoaded();
    const rate = screen.getByLabelText("Interest Rate Annual");
    expect(rate).not.toHaveAttribute("aria-describedby");
    expect(rate).not.toHaveAttribute("aria-invalid");

    fireEvent.change(rate, { target: { value: "-0.1" } });

    expect(rate).toHaveAttribute("aria-invalid", "true");
    expect(rate).toHaveAttribute(
      "aria-describedby",
      "sim-finance-interest_rate_annual-error",
    );
    expect(
      document.getElementById("sim-finance-interest_rate_annual-error"),
    ).toHaveTextContent("Must be at least 0.");

    fireEvent.change(rate, { target: { value: "0.2" } });
    expect(rate).not.toHaveAttribute("aria-describedby");
    expect(rate).not.toHaveAttribute("aria-invalid");
  });

  // Birth weight is mirrored into weight_by_age_months[0] and back. The
  // mirrored edit only reaches a field that has released its local draft.
  it("releases a valid draft on blur so the mirrored curve edit reaches it", async () => {
    const curveValues = [
      2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 24.5, 26.5,
    ];
    const user = userEvent.setup();
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          adult_weight_doe_kg: 32,
          adult_weight_buck_kg: 34,
          weight_by_age_months: curveValues,
        },
      },
    });

    await user.click(screen.getByText("Growth"));
    const birthWeight = screen.getByLabelText("Birth Weight Kg");
    await user.clear(birthWeight);
    await user.type(birthWeight, "3");
    expect(screen.getByLabelText(/Weight By Age Months/)).toHaveValue(
      [3, ...curveValues.slice(1)].join(", "),
    );
    await user.tab();

    const curve = screen.getByLabelText(/Weight By Age Months/);
    await user.clear(curve);
    await user.type(curve, [2.8, ...curveValues.slice(1)].join(", "));

    expect(birthWeight).toHaveValue(2.8);
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  // An event month is bounded by live horizon state, so every horizon move
  // has to re-derive that field's error from the value actually on screen —
  // the stored month when nothing is being typed, the draft while it is —
  // and must commit only a draft the new bounds accept.
  it("re-validates a blurred event month every time the horizon moves", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const month = screen.getByLabelText("Month");
    expect(month).toHaveValue(12);

    // Untouched field: a wider horizon must not invent an error.
    await user.click(screen.getByRole("button", { name: "10 yr" }));
    expect(screen.queryByText("A value is required.")).not.toBeInTheDocument();
    expect(month).toHaveValue(12);
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();

    // A draft that stays illegal keeps its error and commits nothing: every
    // prefix of "125" up to 12 was legal, so month 12 is what is stored.
    await user.clear(month);
    await user.type(month, "125");
    expect(screen.getByText("Must be at most 120.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "5 yr" }));
    expect(await screen.findByText("Must be at most 60.")).toBeInTheDocument();
    expect(month).toHaveValue(125);
    expect(
      screen.queryByText("Event 1: month must be a whole number between 1 and 60."),
    ).not.toBeInTheDocument();

    // A horizon that overtakes the draft clears the error and commits it.
    await user.click(screen.getByRole("button", { name: "20 yr" }));
    await waitFor(() =>
      expect(screen.queryByText("Must be at most 60.")).not.toBeInTheDocument(),
    );

    // Moving again re-derives the error from the committed month 999.
    await user.click(screen.getByRole("button", { name: "10 yr" }));
    expect(await screen.findByText("Must be at most 120.")).toBeInTheDocument();
    expect(
      screen.getByText("Event 1: month must be a whole number between 1 and 120."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

describe("SimulationPage stored herd event validation", () => {
  it("names every unusable row in a stored event list and blocks the run", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        events: [
          // 1: legal at the first month of the horizon.
          {
            month: 1,
            kind: "purchase",
            animal_class: "doe",
            count: 10,
            price_per_head: 8000,
          },
          // 2: fractional month.
          {
            month: 12.5,
            kind: "sale",
            animal_class: "male_kid",
            count: 5,
            price_per_head: null,
          },
          // 3: month before the projection starts, and an empty purchase.
          {
            month: 0,
            kind: "purchase",
            animal_class: "doe",
            count: 0,
            price_per_head: null,
          },
          // 4: past the horizon, past the herd cap, and a negative price.
          {
            month: 999,
            kind: "sale",
            animal_class: "buck",
            count: 200000,
            price_per_head: -5,
          },
          // 5: legacy row whose month and count never made it into storage.
          { kind: "purchase", animal_class: "doe" },
        ],
      },
    });

    const month = "month must be a whole number between 1 and 60.";
    const count = "count must be greater than 0 and at most 100,000.";
    for (const message of [
      `Event 2: ${month}`,
      `Event 3: ${month}`,
      `Event 3: ${count}`,
      `Event 4: ${month}`,
      `Event 4: ${count}`,
      "Event 4: price per head must be zero or more (or left blank).",
      `Event 5: ${month}`,
      `Event 5: ${count}`,
    ])
      expect(screen.getByText(message)).toBeInTheDocument();

    // The first row is legal, and no row is numbered from a shifted index.
    for (const message of [
      `Event 1: ${month}`,
      `Event 1: ${count}`,
      "Event 1: price per head must be zero or more (or left blank).",
      `Event 2: ${count}`,
      "Event 2: price per head must be zero or more (or left blank).",
    ])
      expect(screen.queryByText(message)).not.toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

describe("SimulationPage festival month list", () => {
  it("accepts an emptied list and a full-length one", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [3, 9] } },
      onRun: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByText("Sales"));
    const festivals = screen.getByLabelText(/Festival Sale Months/);
    await user.clear(festivals);

    // Festival months are optional: an empty list is a valid list, not a
    // malformed one.
    expect(
      screen.queryByText("Enter only comma-separated numbers."),
    ).not.toBeInTheDocument();
    expect(festivals).not.toHaveAttribute("aria-invalid");
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual([]);

    // 40 entries is the documented ceiling, so 40 must still pass.
    const forty = Array.from({ length: 40 }, (_, index) => index + 1);
    await user.type(festivals, forty.join(","));
    expect(
      screen.queryByText("Enter at most 40 simulation months."),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() =>
      expect(captured.body?.assumptions.sales?.festival_sale_months).toEqual(forty),
    );
  });
});

describe("SimulationPage non-finite API figures", () => {
  it("renders unrepresentable and missing result figures as an em dash", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      runResult: {
        ...RESULT,
        months: [monthRow({ total_herd: BEYOND_FLOAT64 })],
        metrics: {
          ...METRICS,
          bcr: BEYOND_FLOAT64,
          irr: BEYOND_FLOAT64,
          peak_capacity_head: null,
        },
      },
    });

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();

    const cardFor = (label: string) =>
      screen.getByText(label).closest("div.rounded-xl") as HTMLElement;
    expect(within(cardFor("BCR")).getByText("—")).toBeInTheDocument();
    expect(within(cardFor("IRR")).getByText("—")).toBeInTheDocument();
    expect(
      within(cardFor("Funded capacity (head)")).getByText("—"),
    ).toBeInTheDocument();
    // MIRR is finite in the same payload and still prints.
    expect(within(cardFor("MIRR")).getByText("16.0%")).toBeInTheDocument();

    const projection = screen
      .getByText("Monthly projection")
      .closest("[data-slot='card']") as HTMLElement;
    const cells = within(within(projection).getAllByRole("row")[1]).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("—");
    expect(screen.queryByText("Infinity")).not.toBeInTheDocument();
    expect(screen.queryByText("Infinity%")).not.toBeInTheDocument();
  });

  it("prints a calibrated value that is already a label, and one that is a list", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      permissions: CALIBRATION_PERMS,
      calibrationResult: {
        assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 73 } },
        evidence: [
          {
            path: "reproduction.conception_rate",
            previous_value: 0.85,
            calibrated_value: 0.7789,
            sample_size: 40,
            confidence: "medium",
            method: "Observed conceptions per exposure",
            source: "Breeding records",
            period_start: "2024-08-10",
            period_end: "2026-08-10",
          },
          {
            path: "sales.monthly_meat_price_multipliers",
            previous_value: [1, 1],
            calibrated_value: [0.9512, 1.0837],
            sample_size: 24,
            confidence: "low",
            method: "Monthly mean of realized sale prices",
            source: "Sale invoices",
            period_start: "2024-08-10",
            period_end: "2026-08-10",
          },
          {
            path: "herd.does",
            previous_value: BEYOND_FLOAT64,
            calibrated_value: 73,
            sample_size: 41,
            confidence: "high",
            method: "Counted active adult female animals",
            source: "Animal registry",
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

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    expect(await screen.findByText("Animal registry")).toBeInTheDocument();

    const cellsOf = (assumption: string) =>
      within(
        screen.getByText(assumption).closest("tr") as HTMLElement,
      ).getAllByRole("cell");

    const rate = cellsOf("Reproduction / Conception Rate");
    expect(rate[1]).toHaveTextContent("0.850");
    expect(rate[2]).toHaveTextContent("0.779");

    const seasonality = cellsOf("Sales / Monthly Meat Price Multipliers");
    expect(seasonality[1]).toHaveTextContent("1.000, 1.000");
    expect(seasonality[2]).toHaveTextContent("0.951, 1.084");

    const does = cellsOf("Herd / Does");
    expect(does[1]).toHaveTextContent("—");
    expect(does[2]).toHaveTextContent("73");
  });
});

describe("SimulationPage explanation figures", () => {
  it("prints a figure that already reads as a label verbatim", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Explain Project cost" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Capacity Basis")).toBeInTheDocument();
    expect(within(dialog).getByText("projected_peak")).toBeInTheDocument();
    expect(within(dialog).getByText("₹5,00,000")).toBeInTheDocument();
    expect(within(dialog).getByText("58")).toBeInTheDocument();
  });
});
