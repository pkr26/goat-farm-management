/**
 * Simulation page: horizon quick presets, the herd events editor (add /
 * remove / validation / payload wiring / scenario load), the monthly
 * projection "Events" column, the numeric-input guards (blank never becomes
 * 0, NaN never reaches the assumptions object), null-safe IRR/BCR rendering,
 * and the Delete-scenario pending state.
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
  metric_explanations: [],
  narrative_report: [],
  monte_carlo: null,
  sensitivity: null,
};

interface RunBody {
  assumptions: {
    meta?: { horizon_months?: number };
    events?: unknown[];
  };
  monte_carlo: boolean;
  sensitivity: boolean;
}

/** Breeds + defaults + scenario list; the run POST captures its body. */
function registerApiHandlers(options: {
  scenarios?: unknown[];
  onRun?: (body: RunBody) => void;
  runResult?: unknown;
} = {}) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json(options.scenarios ?? []),
    ),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return HttpResponse.json(options.runResult ?? RESULT);
    }),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded(options?: Parameters<typeof registerApiHandlers>[0]) {
  registerApiHandlers(options);
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
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
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
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
      await screen.findByText("Event 1: month must be a whole number between 1 and 60."),
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
      await screen.findByText("Event 1: count must be a positive whole number."),
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
    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.events).toEqual([
      {
        month: 12,
        kind: "purchase",
        animal_class: "doe",
        count: 10,
        price_per_head: null,
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

  it("shows event log lines in the monthly projection table", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));

    expect(
      await screen.findByText("Purchased 10 doe(s) at ₹8,000/head (₹80,000)"),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage numeric input guards", () => {
  it("clearing an assumptions number keeps the stored value instead of writing 0", async () => {
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
    // Blank while editing — the stored 0.12 is untouched (was: silent 0).
    expect(rateInput).toHaveValue(null);
    // On blur the field snaps back to the stored value.
    await user.tab();
    expect(rateInput).toHaveValue(0.12);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    const finance = (
      captured.body?.assumptions as {
        finance?: { interest_rate_annual?: number };
      }
    ).finance;
    expect(finance?.interest_rate_annual).toBe(0.12);
  });

  it("clearing an event month keeps the stored month and stays runnable", async () => {
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
    // No phantom validation error from a coerced 0.
    expect(screen.queryByText(/Event 1: month must/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.events?.[0]).toMatchObject({ month: 12 });
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
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.events?.[0]).toMatchObject({
      price_per_head: null,
    });
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
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
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
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await renderLoaded({ scenarios: [scenario] });

    const deleteButton = await screen.findByRole("button", { name: "Delete" });
    await user.click(deleteButton);

    // Mutation in flight: a second click can't fire a duplicate DELETE.
    expect(deleteButton).toBeDisabled();

    resolveDelete!();
    await waitFor(() => expect(deleteCalls).toBe(1));
    await waitFor(() => expect(deleteButton).toBeEnabled());
  });
});
