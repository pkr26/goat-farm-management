/**
 * Simulation page: the scenario-list paging arithmetic a delete or a save
 * commits before the list is re-read, the bindings a delete must leave alone,
 * the full-width comparison, a failed update that is not a conflict, and the
 * editor claims a finished calibration makes (defaults latch, event identity)
 * plus the field identities a horizon preset must not disturb.
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
  months: [],
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
  metric_explanations: [],
  narrative_report: [],
  monte_carlo: null,
  sensitivity: null,
  optimization: null,
};

interface RunBody {
  assumptions: {
    herd?: { auto_purchase_bucks?: boolean };
    events?: unknown[];
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

/** Breeds + defaults + a mutable scenario list whose every page request is
 * recorded, so a page transition can be checked against what was actually
 * fetched and not only against where the operator ended up. */
function registerApiHandlers(options: {
  scenarios?: ReturnType<typeof scenarioRows>;
  offsets?: number[];
  defaults?: unknown;
  permissions?: string[];
  onRun?: (body: RunBody) => void;
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
      options.offsets?.push(offset);
      return HttpResponse.json({
        items: scenarios.slice(offset, offset + limit),
        total: scenarios.length,
        limit,
        offset,
      });
    }),
    http.post("/api/simulation/run", async ({ request }) => {
      options.onRun?.((await request.json()) as RunBody);
      return HttpResponse.json(RESULT);
    }),
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

/** The live "Showing a–b of n saved scenarios" caption. */
function pageCaption(): string {
  return screen.getByText(/saved scenarios$/).textContent ?? "";
}

function deleteHandler(scenarios: ReturnType<typeof scenarioRows>) {
  return http.delete("/api/simulation/scenarios/:scenarioId", ({ params }) => {
    const index = scenarios.findIndex(
      (scenario) => scenario.id === Number(params.scenarioId),
    );
    if (index >= 0) scenarios.splice(index, 1);
    return new HttpResponse(null, { status: 204 });
  });
}

describe("SimulationPage scenario paging after a write", () => {
  // The deleted row is not the last one on the farm, so the page the operator
  // is reading still exists: re-homing anywhere would teleport them away from
  // the rows they were working through.
  it("keeps the operator on the page a deleted row came from", async () => {
    const scenarios = scenarioRows(42);
    const offsets: number[] = [];
    const user = await renderLoaded({ scenarios, offsets });
    server.use(deleteHandler(scenarios));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 21")).toBeInTheDocument();
    expect(pageCaption()).toBe("Showing 21–40 of 42 saved scenarios");

    const mark = offsets.length;
    const row = screen.getByText("Plan 25").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));

    await waitFor(() =>
      expect(pageCaption()).toBe("Showing 21–40 of 41 saved scenarios"),
    );
    expect(screen.queryByText("Plan 25")).not.toBeInTheDocument();
    // Row 41 has moved up onto this page; row 42 is still on the next one.
    expect(screen.getByText("Plan 41")).toBeInTheDocument();
    expect(screen.queryByText("Plan 42")).not.toBeInTheDocument();
    expect(offsets.slice(mark)).toEqual([20]);
  });

  // Deleting the final page's only row empties it. The handler works the
  // landing page out from the count it already has, so the refreshed list is
  // read straight from the page the operator will see.
  it("re-homes to the real last page when the final page's only row is deleted", async () => {
    const scenarios = scenarioRows(41);
    const offsets: number[] = [];
    const user = await renderLoaded({ scenarios, offsets });
    server.use(deleteHandler(scenarios));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 21")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Plan 41")).toBeInTheDocument();
    expect(pageCaption()).toBe("Showing 41–41 of 41 saved scenarios");

    const mark = offsets.length;
    const row = screen.getByText("Plan 41").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));

    await waitFor(() =>
      expect(pageCaption()).toBe("Showing 21–40 of 40 saved scenarios"),
    );
    expect(screen.getByText("Plan 40")).toBeInTheDocument();
    expect(screen.queryByText("Plan 41")).not.toBeInTheDocument();
    // The emptied page is refreshed once by the invalidation, and the only
    // other page read is the one the operator lands on.
    expect(offsets.slice(mark)).toEqual([40, 20]);
  });

  // The API keeps oldest-first order, so a save lands on the final page —
  // which is the page that was already full, not the one after it.
  it("opens the final page a saved scenario lands on without overshooting it", async () => {
    const scenarios = scenarioRows(39);
    const offsets: number[] = [];
    const user = await renderLoaded({ scenarios, offsets });
    server.use(
      http.post("/api/simulation/scenarios", async ({ request }) => {
        const body = (await request.json()) as { name: string; assumptions: unknown };
        const created = scenarioRow(40, {
          name: body.name,
          assumptions: body.assumptions,
        });
        scenarios.push(created);
        return HttpResponse.json(created, { status: 201 });
      }),
    );
    expect(await screen.findByText("Plan 1")).toBeInTheDocument();
    expect(pageCaption()).toBe("Showing 1–20 of 39 saved scenarios");

    const mark = offsets.length;
    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Name/), "Newest plan");
    await user.click(within(dialog).getByRole("button", { name: "Save scenario" }));

    expect(await screen.findByText("Newest plan")).toBeInTheDocument();
    expect(pageCaption()).toBe("Showing 21–40 of 40 saved scenarios");
    // The page being left is refreshed by the invalidation; the only other
    // page read is the one holding the new row.
    expect(offsets.slice(mark)).toEqual([0, 20]);
  });
});

describe("SimulationPage scenario deletion bindings", () => {
  // The editor and the displayed result are bound to a scenario by id.
  // Deleting a different row must leave both bindings — and the update button
  // they enable — exactly where they were.
  it("keeps the editor and result bound to a scenario another delete missed", async () => {
    const scenarios = [scenarioRow(7, { name: "Kept plan" }), scenarioRow(8)];
    const user = await renderLoaded({ scenarios });
    server.use(
      deleteHandler(scenarios),
      http.post("/api/simulation/scenarios/7/run", () => HttpResponse.json(RESULT)),
    );
    const keptRow = (await screen.findByText("Kept plan")).closest("tr") as HTMLElement;
    await user.click(within(keptRow).getByRole("button", { name: "Load" }));
    await user.click(within(keptRow).getByRole("button", { name: "Run" }));
    expect(
      await screen.findByText("Source: Saved scenario “Kept plan”"),
    ).toBeInTheDocument();
    expect(screen.getByText("Editing scenario: Kept plan")).toBeInTheDocument();

    const doomedRow = screen.getByText("Plan 8").closest("tr") as HTMLElement;
    await user.click(within(doomedRow).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete scenario" }));

    await waitFor(() => expect(screen.queryByText("Plan 8")).not.toBeInTheDocument());
    expect(screen.getByText("Editing scenario: Kept plan")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update Kept plan" })).toBeEnabled();
    expect(screen.getByText("Source: Saved scenario “Kept plan”")).toBeInTheDocument();
    expect(screen.getByText("₹2,34,567")).toBeInTheDocument();
  });
});

describe("SimulationPage comparison gate", () => {
  // The selection cap and the compare gate must agree: a full selection of
  // five is the largest comparison the operator is allowed to assemble, and
  // it has to actually run.
  it("compares a full selection of five scenarios", async () => {
    const scenarios = scenarioRows(5);
    let requestedIds: string | null = null;
    const user = await renderLoaded({ scenarios });
    server.use(
      http.get("/api/simulation/scenarios/compare", ({ request }) => {
        requestedIds = new URL(request.url).searchParams.get("ids");
        return HttpResponse.json({
          scenarios,
          results: scenarios.map(() => RESULT),
        });
      }),
    );
    expect(await screen.findByText("Plan 5")).toBeInTheDocument();
    for (const scenario of scenarios)
      await user.click(screen.getByLabelText(`Compare ${scenario.name}`));
    expect(screen.getByText(/5 selected/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Compare selected" }));

    const comparison = (await screen.findByText("Comparison"))
      .parentElement as HTMLElement;
    expect(requestedIds).toBe("1,2,3,4,5");
    const npvRow = within(comparison).getByText("NPV").closest("tr") as HTMLElement;
    expect(within(npvRow).getAllByText("₹2,34,567")).toHaveLength(5);
    for (const scenario of scenarios)
      expect(
        within(comparison).getByRole("columnheader", { name: scenario.name }),
      ).toBeInTheDocument();
  });
});

describe("SimulationPage scenario update failures", () => {
  // Only a 409 means "somebody else moved the revision on". Any other
  // failure leaves the operator's own editor authoritative: re-reading the
  // stored scenario would overwrite the edits they are trying to save.
  it("does not refresh the editor when an update fails for any other reason", async () => {
    const scenarios = [scenarioRow(8, { name: "Versioned plan" })];
    let reads = 0;
    const user = await renderLoaded({ scenarios });
    toastMocks.error.mockClear();
    server.use(
      http.patch("/api/simulation/scenarios/8", () =>
        HttpResponse.json({ detail: "scenario storage is read-only" }, { status: 500 }),
      ),
      http.get("/api/simulation/scenarios/8", () => {
        reads += 1;
        return HttpResponse.json(
          scenarioRow(8, {
            name: "Versioned plan",
            revision: 9,
            assumptions: { ...DEFAULTS, herd: { ...DEFAULTS.herd, does: 88 } },
          }),
        );
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Load" }));
    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "64");

    await user.click(screen.getByRole("button", { name: "Update Versioned plan" }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("scenario storage is read-only"),
    );
    expect(toastMocks.error).toHaveBeenCalledTimes(1);
    expect(reads).toBe(0);
    expect(screen.getByLabelText("Does")).toHaveValue(64);
  });
});

describe("SimulationPage calibration continuation", () => {
  const CALIBRATION = {
    assumptions: {
      ...DEFAULTS,
      herd: { ...DEFAULTS.herd, does: 73 },
      events: [
        { month: 6, kind: "purchase", animal_class: "doe", count: 10, price_per_head: 8000 },
        { month: 9, kind: "sale", animal_class: "male_grower", count: 4, price_per_head: 6000 },
      ],
    },
    evidence: [],
    warnings: [],
    coverage_score: 0.5,
    reference_date: "2026-08-10",
    lookback_months: 24,
  };

  // A finished calibration owns the editor, so the defaults request it
  // overtook must be dropped when it finally lands — otherwise the farm
  // evidence the operator asked for is silently replaced by breed averages.
  it("keeps calibrated assumptions when an older defaults request lands after it", async () => {
    let defaultsCalls = 0;
    let releaseDefaults!: () => void;
    let markDefaultsStarted!: () => void;
    const defaultsGate = new Promise<void>((resolve) => {
      releaseDefaults = resolve;
    });
    const defaultsStarted = new Promise<void>((resolve) => {
      markDefaultsStarted = resolve;
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
      http.get("/api/simulation/calibration", () => HttpResponse.json(CALIBRATION)),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByLabelText("Does")).toHaveValue(50);

    await user.click(screen.getByRole("button", { name: "Load defaults" }));
    await defaultsStarted;
    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(screen.getByLabelText("Does")).toHaveValue(73));

    releaseDefaults();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Load defaults" })).toBeEnabled(),
    );
    expect(screen.getByLabelText("Does")).toHaveValue(73);
    expect(screen.getByText("Farm calibration evidence", { selector: "[data-slot=\'card-title\'], h2" })).toBeInTheDocument();
  });

  // Calibrated event rows are keyed like every other event row, and those
  // keys carry each row's validity into the parent. A key handed out twice
  // lets one row's removal clear another row's outstanding error.
  it("gives every calibrated event row an identity later rows cannot reuse", async () => {
    registerApiHandlers({ permissions: CALIBRATION_PERMS });
    server.use(
      http.get("/api/simulation/calibration", () => HttpResponse.json(CALIBRATION)),
    );
    const user = userEvent.setup();
    renderWithProviders(<SimulationPage />);
    expect(await screen.findByText("Horizon Months")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Calibrate from farm" }));
    await waitFor(() => expect(screen.getAllByLabelText("Month")).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.click(screen.getByRole("button", { name: "Add event" }));
    expect(screen.getAllByLabelText("Month")).toHaveLength(4);

    // The second calibrated row is left incomplete…
    await user.clear(screen.getAllByLabelText("Count")[1]);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();

    // …and removing an unrelated later row must not answer for it.
    await user.click(screen.getAllByRole("button", { name: "Remove" })[3]);

    expect(screen.getAllByLabelText("Month")).toHaveLength(3);
    expect(screen.getAllByLabelText("Count")[1]).toHaveValue(null);
    expect(screen.getByText("A value is required.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });
});

describe("SimulationPage editor field identity", () => {
  // The horizon input is remounted on a preset so the picked number replaces
  // whatever draft it held. Every other field keeps its own identity: a
  // preset must not quietly discard an unrelated correction in progress.
  it("keeps another field's invalid draft when a horizon preset is applied", async () => {
    const user = await renderLoaded();
    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "200000");
    expect(screen.getByText("Must be at most 100000.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "10 yr" }));

    expect(screen.getByLabelText("Horizon Months")).toHaveValue(120);
    expect(screen.getByLabelText("Does")).toHaveValue(200000);
    expect(screen.getByText("Must be at most 100000.")).toBeInTheDocument();
    expect(
      screen.getByText("Fix 1 highlighted numeric field before running or saving."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  // Boolean assumptions travel both ways: the checkbox commits what the box
  // now shows, not a constant.
  it("switches a boolean assumption on and sends it that way", async () => {
    const captured: { body: RunBody | null } = { body: null };
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        herd: { ...DEFAULTS.herd, auto_purchase_bucks: false },
      },
      onRun: (body) => {
        captured.body = body;
      },
    });
    const autoPurchase = screen.getByRole("checkbox", { name: "Auto Purchase Bucks" });
    expect(autoPurchase).not.toBeChecked();

    await user.click(autoPurchase);

    expect(autoPurchase).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(await screen.findByText("₹2,34,567")).toBeInTheDocument();
    expect(captured.body?.assumptions.herd?.auto_purchase_bucks).toBe(true);
  });
});
