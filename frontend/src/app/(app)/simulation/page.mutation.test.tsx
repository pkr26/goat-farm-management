/**
 * Mutation-hardening tests for the simulation page.
 *
 * Each test targets survivor clusters from reports/mutation/app2.json (Stryker
 * mutant ids are cited inline so the next run can verify the kills). The
 * emphasis is logic over cosmetics: URL paging state, the
 * species-sync effect, validation copy and dialog facts, run/scenario status
 * guards, staleness chips, formatting helpers, and payload construction.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

// Router.replace is asserted with exact URLs (scenario paging writes
// `?scenarios=<offset>`); the params object is swapped per test for
// shared-link seeds.
const nav = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  params: new URLSearchParams(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => nav.params,
  useParams: () => ({}),
}));

const MANAGE_PERMS = ["simulation.view", "simulation.manage"];

/** Sparse but valid assumptions object — the editor renders whatever keys
 * are present, so custom keys exercise the generic paths. */
const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2, foundation_flock_state: "open" },
  finance: { interest_rate_annual: 0.12 },
};

function scenarioFixture(overrides: {
  id: number;
  name: string;
  assumptions: unknown;
}) {
  return {
    id: overrides.id,
    farm_id: 1,
    name: overrides.name,
    notes: "",
    assumptions: overrides.assumptions,
    revision: 1,
    valid: true,
    validation_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  };
}

function registerApiHandlers(
  options: {
    permissions?: string[];
    defaults?: unknown;
    scenarios?: unknown[];
    breeds?: string[];
  } = {},
) {
  server.use(
    permissionsHandler(options.permissions ?? MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({
        breeds: options.breeds ?? ["osmanabadi"],
        systems: ["stall_fed"],
      }),
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
  );
}

async function renderLoaded(
  options: Parameters<typeof registerApiHandlers>[0] = {},
) {
  registerApiHandlers(options);
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  return userEvent.setup();
}

/** The facts list inside an open field-help dialog, as term → value pairs. */
function dialogFacts(): Record<string, string> {
  const dialog = screen.getByRole("dialog");
  const facts: Record<string, string> = {};
  within(dialog)
    .queryAllByRole("definition")
    .forEach((definition) => {
      const term = within(definition.parentElement as HTMLElement)
        .getAllByRole("term")
        .find((node) => node.parentElement === definition.parentElement);
      if (term) facts[term.textContent ?? ""] = definition.textContent ?? "";
    });
  return facts;
}

beforeEach(() => {
  toastMocks.error.mockClear();
  toastMocks.success.mockClear();
  nav.push.mockClear();
  nav.replace.mockClear();
  nav.params = new URLSearchParams();
});

describe("SimulationPage mutation hardening: URL paging state", () => {
  // Mutants 4969–4973: setScenarioOffset must write `scenarios=<offset>` for
  // positive offsets and strip the key entirely on page one.
  it("writes the scenario offset to the URL and never writes scenarios=0", async () => {
    const scenarios = Array.from({ length: 21 }, (_, index) =>
      scenarioFixture({ id: index + 1, name: `Plan ${index + 1}`, assumptions: DEFAULTS }),
    );
    const user = await renderLoaded({ scenarios });

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(nav.replace).toHaveBeenCalledWith("/simulation?scenarios=20", {
      scroll: false,
    });
    expect(await screen.findByText("Plan 21")).toBeInTheDocument();

    // Returning to page one deletes the key: no `?scenarios=0` write may happen.
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("Plan 1");
    expect(nav.replace).not.toHaveBeenCalledWith("/simulation?scenarios=0", {
      scroll: false,
    });
    expect(
      nav.replace.mock.calls.filter(([url]) => String(url).includes("scenarios")),
    ).toEqual([["/simulation?scenarios=20", { scroll: false }]]);
  });

  // Mutant 4956: the offset reader must key on "scenarios".
  it("seeds the scenario page from a shared ?scenarios= link", async () => {
    const scenarios = Array.from({ length: 45 }, (_, index) =>
      scenarioFixture({ id: index + 1, name: `Plan ${index + 1}`, assumptions: DEFAULTS }),
    );
    nav.params = new URLSearchParams("scenarios=20");
    await renderLoaded({ scenarios });

    expect(
      await screen.findByText("Showing 21–40 of 45 saved scenarios"),
    ).toBeInTheDocument();
    expect(screen.getByText("Plan 21")).toBeInTheDocument();
    expect(screen.queryByText("Plan 1")).not.toBeInTheDocument();
  });
});

describe("SimulationPage mutation hardening: field-help facts", () => {
  const FACT_DEFAULTS = {
    meta: {
      horizon_months: 60,
      start_year_month: "2026-01",
      plan_note: "x".repeat(81),
      short_note: "hello",
      custom_count: 7,
      custom_tags: [1, 2, 3],
    },
    herd: { does: 50, bucks: 2, foundation_flock_state: "open" },
    finance: { interest_rate_annual: 0.12, flag_on: true, flag_off: false },
    growth: {
      birth_weight_kg: 2.5,
      weight_by_age_months: [2.5, 8, 15, 25, 35, 45, 55, 60, 65, 68, 70, 71, 72],
    },
    sales: { festival_sale_months: [3, 7] },
  };

  // Mutants 4525–4531: the string "Current value" fact only renders for
  // non-empty strings of at most 80 characters.
  it("shows short string values but not blank or oversized ones", async () => {
    const user = await renderLoaded({ defaults: FACT_DEFAULTS });

    const shortNoteButton = screen.getByRole("button", {
      name: /Explain Short Note\?/,
    });
    // FieldHelpButton tooltip copy (mutant 4437).
    expect(shortNoteButton).toHaveAttribute("title", "What is Short Note?");
    await user.click(shortNoteButton);
    expect(dialogFacts()["Current value"]).toBe("hello");
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: /Explain Plan Note\?/ }));
    // Oversized strings contribute no facts at all: the dialog renders no
    // definitions.
    expect(dialogFacts()).toEqual({});
    await user.keyboard("{Escape}");

    // Only the real month field validates as a month (mutant 5968: a plain
    // string field must never show the month error).
    expect(screen.queryByText(/Enter a real month/)).not.toBeInTheDocument();
  });

  // Mutants 4500/4502 (empty bounds), 4475 (`min` guard), 4481/4483
  // (exactLength), 4489/4491 (maxLength), 4542/4517 (array current value),
  // 4509/4511 (boolean On/Off), 4453 (choices), 5894/5899 (no help entry).
  it("derives allowed-value and current-value facts from each rule shape", async () => {
    const user = await renderLoaded({ defaults: FACT_DEFAULTS });

    // A bound-less, unit-less numeric rule contributes no rule facts — only
    // the bare current value (an empty-bounds mutant would add an
    // "Allowed values" row).
    await user.click(screen.getByRole("button", { name: /Explain Custom Count\?/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Custom Count")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/No written explanation for this field yet/),
    ).toBeInTheDocument();
    expect(dialogFacts()).toEqual({ "Current value": "7" });
    await user.keyboard("{Escape}");

    // exclusiveMin-only bounds never print "at least undefined" (mutant 4475).
    await user.click(
      screen.getByRole("button", { name: /Explain Birth Weight Kg\?/ }),
    );
    expect(dialogFacts()["Allowed values"]).toBe("greater than 0, at most 1000");
    await user.keyboard("{Escape}");

    // Array rules: exactLength for the weight curve, maxLength for festival
    // months, joined values for short arrays.
    await user.click(
      screen.getByRole("button", { name: /Explain Weight By Age Months\?/ }),
    );
    expect(dialogFacts()["Allowed values"]).toBe(
      "greater than 0, at most 1000, exactly 13 values",
    );
    expect(dialogFacts()["Current value"]).toBe("13 values");
    await user.keyboard("{Escape}");

    await user.click(
      screen.getByRole("button", { name: /Explain Festival Sale Months\?/ }),
    );
    expect(dialogFacts()["Allowed values"]).toBe(
      "at least 1, at most 60, at most 40 values, whole numbers only",
    );
    await user.keyboard("{Escape}");

    await user.click(
      screen.getByRole("button", { name: /Explain Custom Tags\?/ }),
    );
    expect(dialogFacts()["Current value"]).toBe("1, 2, 3");
    await user.keyboard("{Escape}");

    // Boolean values read On/Off (mutants 4509/4511).
    await user.click(screen.getByRole("button", { name: /Explain Flag On\?/ }));
    expect(dialogFacts()["Current value"]).toBe("On");
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: /Explain Flag Off\?/ }));
    expect(dialogFacts()["Current value"]).toBe("Off");
    await user.keyboard("{Escape}");

    // Select fields list their choices (mutant 4453).
    await user.click(
      screen.getByRole("button", { name: /Explain Foundation Flock State\?/ }),
    );
    expect(dialogFacts()["Choices"]).toBe("Mixed, Open");
    await user.keyboard("{Escape}");
  });

  // Mutants 6605/6606: section help tolerates unknown sections and ships no
  // facts; 6893/6894: an empty facts list renders no <dl> at all.
  it("explains an unknown section without facts or crashing", async () => {
    const user = await renderLoaded({
      defaults: { ...DEFAULTS, bonus: { extra_flag: true } },
    });

    expect(screen.getByText("Bonus")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Explain Bonus\?/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Bonus assumptions")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/No written explanation for this field yet/),
    ).toBeInTheDocument();
    expect(dialog.querySelector("dl")).toBeNull();
  });

  // Mutant 5968 mirror: nested "?" affordances all open the dialog
  // (mutants 6057/6063 among them).
  it("opens help for nested boolean and string subfields", async () => {
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        risk: { meat_price: { enabled: true, low: 0.8, high: 1.2 } },
        meta: { ...DEFAULTS.meta, licence: { code: "ABC" } },
      },
    });

    const meatPriceGroup = screen
      .getByText("Meat Price")
      .closest("div.rounded-lg") as HTMLElement;
    await user.click(
      within(meatPriceGroup).getByRole("button", { name: /Explain Enabled\?/ }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    const licenceGroup = screen
      .getByText("Licence")
      .closest("div.rounded-lg") as HTMLElement;
    await user.click(
      within(licenceGroup).getByRole("button", { name: /Explain Code\?/ }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("SimulationPage mutation hardening: numeric rule heuristics", () => {
  // Mutants 4116/4117/4120: the doe_cull_rate_annual 0–1 rule must apply only
  // to culling.doe_cull_rate_annual — not to other culling keys, nor to the
  // same key in a section that falls through to the culling branch (the
  // mortality branch binds earlier in the heuristic chain, so the same key
  // there cannot observe these mutants).
  it("scopes the doe cull rate bounds to culling.doe_cull_rate_annual", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        mortality: { doe_cull_rate_annual: 0.3 },
        culling: { doe_cull_rate_annual: 0.2, buck_cull_rate_annual: 0.1 },
        sales: { doe_cull_rate_annual: 0.4 },
      },
    });
    const salesSection = screen.getByText("Sales").closest("details") as HTMLElement;
    expect(
      within(salesSection).getByLabelText("Doe Cull Rate Annual"),
    ).not.toHaveAttribute("max");

    const culling = screen.getByText("Culling").closest("details") as HTMLElement;
    expect(
      within(culling).getByLabelText("Doe Cull Rate Annual"),
    ).toHaveAttribute("max", "1");
    // Other culling keys keep the generic (bound-less) rule.
    expect(
      within(culling).getByLabelText("Buck Cull Rate Annual"),
    ).not.toHaveAttribute("max");

    // The same key in another section keeps that section's own bounds.
    const mortality = screen.getByText("Mortality").closest("details") as HTMLElement;
    expect(
      within(mortality).getByLabelText("Doe Cull Rate Annual"),
    ).toHaveAttribute("max", "0.9");
  });

  // Mutants 6024–6030: the ×multiplier low/high rule is risk-only.
  it("applies the multiplier bounds only to risk low/high subfields", async () => {
    await renderLoaded({
      defaults: {
        ...DEFAULTS,
        risk: { meat_price: { enabled: true, low: 0.8, high: 1.2 } },
        sales: { price_band: { low: 5, high: 9 } },
      },
    });

    const meatPriceGroup = screen
      .getByText("Meat Price")
      .closest("div.rounded-lg") as HTMLElement;
    expect(within(meatPriceGroup).getByLabelText("Low")).toHaveAttribute(
      "max",
      "100",
    );
    expect(within(meatPriceGroup).getByLabelText("High")).toHaveAttribute(
      "max",
      "100",
    );

    // Low/high subfields outside `risk` get the generic section rule instead.
    const priceBandGroup = screen
      .getByText("Price Band")
      .closest("div.rounded-lg") as HTMLElement;
    expect(
      within(priceBandGroup).getByLabelText("Low"),
    ).not.toHaveAttribute("max");
    expect(
      within(priceBandGroup).getByLabelText("High"),
    ).not.toHaveAttribute("max");
  });

  // Mutants 6008/6010: a null nested object is dropped, not exploded.
  it("drops a null nested object field instead of rendering it", async () => {
    await renderLoaded({
      defaults: { ...DEFAULTS, risk: { monte_carlo_runs: 500, drought: null } },
    });

    expect(screen.getByText("Monte Carlo Runs")).toBeInTheDocument();
    expect(screen.queryByText("Drought")).not.toBeInTheDocument();
  });

  // Mutant 5197: only growth.weight_by_age_months edits mirror into the birth
  // weight — a different growth array must leave both alone.
  it("does not mirror an unrelated growth array into the birth weight", async () => {
    const user = await renderLoaded({
      defaults: {
        ...DEFAULTS,
        growth: {
          birth_weight_kg: 2.5,
          weight_by_age_months: [2.5, 8, 15, 25, 35, 45, 55, 60, 65, 68, 70, 71, 72],
          custom_curve: [3, 6, 9],
        },
      },
    });

    const curve = screen.getByLabelText(/Weight By Age Months/);
    const custom = screen.getByLabelText(/Custom Curve/);
    await user.clear(custom);
    await user.type(custom, "4, 8, 12");
    expect(custom).toHaveValue("4, 8, 12");

    expect(screen.getByLabelText("Birth Weight Kg")).toHaveValue(2.5);
    expect(curve).toHaveValue(
      "2.5, 8, 15, 25, 35, 45, 55, 60, 65, 68, 70, 71, 72",
    );
  });
});

describe("SimulationPage mutation hardening: cross-field assumption guards", () => {
  // Mutants 5264/5267/5286/5304/5361: each pair-wise guard must require BOTH
  // operands to be numbers — a null operand (stored scenario with an absent
  // optional field) must never trip the error through null coercion.
  it("ignores pair guards when one stored operand is null", async () => {
    const scenarios = [
      scenarioFixture({
        id: 1,
        name: "Nulls A",
        assumptions: {
          ...DEFAULTS,
          finance: {
            // Single-field bounds all hold; only the PAIR guards (loan+
            // subsidy <= 1, moratorium < term) would fire, and each pair has
            // a null operand so they must be ignored. (The old fixture used
                       // out-of-single-bounds values, which the mount-time loaded-value
            // validation now flags independently — P3, 2026-09-20.)
            loan_fraction_of_project_cost: null,
            subsidy_fraction: 0.9,
            moratorium_months: 6,
            loan_term_months: null,
          },
          feed: { initial_fodder_stock_kg_dm: 9, fodder_storage_capacity_kg_dm: null },
          // doe_scale_low stays inside its own single-field bound (0 < x
          // <= 5): only the low<=high PAIR guard is exercised (high is null).
          optimization: { doe_scale_low: 4, doe_scale_high: null },
        },
      }),
      scenarioFixture({
        id: 2,
        name: "Nulls B",
        assumptions: {
          ...DEFAULTS,
          finance: { loan_fraction_of_project_cost: 0.9, subsidy_fraction: null },
        },
      }),
    ];
    const user = await renderLoaded({ scenarios });

    const rowA = (await screen.findByText("Nulls A")).closest("tr") as HTMLElement;
    await user.click(within(rowA).getByRole("button", { name: "Load" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Subsidy Fraction")).toHaveValue(0.9),
    );
    for (const message of [
      /Loan fraction plus subsidy fraction must not exceed 1/,
      /Moratorium must be shorter than the loan term/,
      /Initial fodder stock must fit within fodder storage capacity/,
      /Herd scale low must be less than or equal to herd scale high/,
    ])
      expect(screen.queryByText(message)).not.toBeInTheDocument();
    const runButton = screen.getByRole("button", { name: "Run simulation" });
    // The scenario load releases the single-flight gate a render after the
    // values land; poll for the re-enable instead of asserting synchronously.
    await waitFor(() => expect(runButton).toBeEnabled());

    const rowB = screen.getByText("Nulls B").closest("tr") as HTMLElement;
    await user.click(within(rowB).getByRole("button", { name: "Load" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Loan Fraction Of Project Cost")).toHaveValue(0.9),
    );
    expect(
      screen.queryByText(/Loan fraction plus subsidy fraction/),
    ).not.toBeInTheDocument();
  });
});

describe("SimulationPage mutation hardening: run outcomes", () => {
  // Mutants 6465–6470: the sticky-nav Run button shares the editor-error and
  // assumptions gating.
  it("gates the sticky-nav Run button on editor validity", async () => {
    const user = await renderLoaded();

    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
    const rate = screen.getByLabelText("Interest Rate Annual");
    await user.clear(rate);
    await user.type(rate, "0.9");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  // Mutants 6427/6464/6471: both run buttons announce "Running…" while the
  // flight is pending, and the nav Run actually submits.
  it("labels both run buttons Running… while the flight is pending", async () => {
    let releaseRun!: () => void;
    server.use(
      http.post(
        "/api/simulation/run",
        () =>
          new Promise<Response>((resolve) => {
            releaseRun = () =>
              resolve(HttpResponse.json({ ok: true }, { status: 201 }));
          }),
      ),
    );
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Running…" })).toHaveLength(2),
    );
    releaseRun();
    // A 201 body is not a result: the run settles quietly with no chips.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled(),
    );
    expect(screen.queryByText(/Source:/)).not.toBeInTheDocument();
  });

  // Mutants 5693/5713: a 2xx that is not 200 is not a result.
  it("treats a 201 run response as no result", async () => {
    server.use(
      http.post("/api/simulation/run", () =>
        HttpResponse.json({ ok: true }, { status: 201 }),
      ),
    );
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled(),
    );
    expect(screen.queryByText(/Source:/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Results" })).not.toBeInTheDocument();
  });

  // Mutants 4379/4383: a TimeoutError transport failure keeps its dedicated
  // message instead of the generic fallback. MSW converts handler exceptions
  // into 500 responses, so the transport rejection is injected at the fetch
  // boundary (the one place AbortSignal.timeout failures surface).
  it("explains a connection timeout in the operator's terms", async () => {
    const timeout = new Error("The operation was aborted due to timeout");
    timeout.name = "TimeoutError";
    const originalFetch = globalThis.fetch;
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/api/simulation/run")) throw timeout;
        return originalFetch(input as RequestInfo, init as RequestInit);
      });
    try {
      const user = await renderLoaded();

      await user.click(screen.getByRole("button", { name: "Run simulation" }));

      expect(
        await screen.findByText(/timed out on the connection/i),
      ).toBeInTheDocument();
      expect(screen.queryByText("Simulation failed")).not.toBeInTheDocument();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  // Mutant 6373: the permissions dead-end must retry in place.
  it("retries a failed permissions load in place", async () => {
    let allowPerms = false;
    server.use(
      http.get("/api/auth/permissions", () =>
        allowPerms
          ? HttpResponse.json({ is_owner: false, permissions: MANAGE_PERMS })
          : HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
      http.get("/api/simulation/scenarios", () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
    );
    renderWithProviders(<SimulationPage />);

    expect(
      await screen.findByText(
        "Could not load your permissions — refresh the page to try again.",
      ),
    ).toBeInTheDocument();
    const user = userEvent.setup();
    allowPerms = true;
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect(
      await screen.findByText(
        /Pick a breed and rearing system, then load the baseline/,
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Horizon Months", {}, { timeout: 4000 }),
    ).toBeInTheDocument();
  });
});

describe("SimulationPage mutation hardening: result rendering", () => {
  const monthRow = (overrides: Record<string, unknown> = {}) => ({
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
  });

  const OPTIMIZATION_CANDIDATE = {
    starting_does: 50,
    starting_bucks: 2,
    max_breeding_does: 60,
    sale_age_months: 12,
    female_retention_fraction: 0.5,
    loan_fraction: 0.5,
    project_cost: 100000,
    capacity_places: 60,
    projected_peak_head: 58,
    npv: 100000,
    irr: 0.2,
    min_dscr: 1.5,
    minimum_cash_balance: 1000,
    funding_gap: 0,
    feasible: true,
    constraint_violations: [],
  };

  const RESULT = {
    months: [monthRow(), monthRow({ month: 2, calendar_month: 2, births: 4 })],
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
      break_even_meat_price_per_kg: null,
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
    metric_explanations: [
      {
        key: "npv",
        title: "Net Present Value",
        explanation: "NPV is ₹2,34,567 over the 60-month horizon.",
        figures: {
          fraction_of_loan: 0.5,
          gross_margin_total: 0.32,
          price_uplift_extra: 0.4,
          loan_months_extra: 72,
          equipment_years_old: 5,
          dry_runs_cost: 1200,
        },
      },
    ],
    narrative_report: [],
    monte_carlo: {
      runs: 100,
      seed: 42,
      npv_mean: 200000,
      npv_std: 50000,
      npv_p5: 100000,
      npv_p50: 200000,
      npv_p95: 300000,
      prob_npv_negative: 0.1,
      prob_liquidity_shortfall: 0.2,
      prob_dscr_below_one: 0.3,
      minimum_cash_p5: -5000,
      minimum_cash_p50: 5000,
      mean_disease_outbreaks: 1.25,
      mean_drought_events: 2.5,
      mean_market_crashes: 0.75,
      npv_histogram_counts: [10, 30, 40, 20],
      npv_histogram_edges: [-50000, 50000, 150000, 250000, 350000],
      herd_percentiles: { p5: [55, 60], p50: [58, 64], p95: [61, 68] },
      liquidity_percentiles: { p5: [1000, 2000], p50: [5000, 6000], p95: [9000, 9500] },
    },
    sensitivity: null,
    optimization: {
      feasible_candidates: 3,
      evaluated_candidates: 10,
      objective: "npv",
      baseline: OPTIMIZATION_CANDIDATE,
      recommended: null,
      alternatives: [],
    },
  };

  async function runOnce(user: ReturnType<typeof userEvent.setup>) {
    server.use(
      http.post("/api/simulation/run", () => HttpResponse.json(RESULT)),
    );
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByText("Source: Current editor assumptions"),
    ).toBeInTheDocument();
  }

  // Mutants 6429/6430, 6443–6454, 6456–6463: the sticky-nav fragment links and
  // the two headline chips, fresh and stale.
  it("shows fragment links and headline chips, dimming them when stale", async () => {
    const user = await renderLoaded();

    // Before any run: links without a Results entry, no chips.
    expect(screen.getByRole("link", { name: "Setup" })).toHaveAttribute(
      "href",
      "#sim-setup",
    );
    expect(screen.getByRole("link", { name: "Scenarios" })).toHaveAttribute(
      "href",
      "#sim-scenarios",
    );
    expect(screen.queryByRole("link", { name: "Results" })).not.toBeInTheDocument();
    expect(document.querySelector('[aria-label^="Net present value"]')).toBeNull();
    expect(document.querySelector('[aria-label^="Internal rate of return"]')).toBeNull();

    await runOnce(user);

    expect(screen.getByRole("link", { name: "Results" })).toHaveAttribute(
      "href",
      "#sim-results",
    );
    const npvChip = document.querySelector(
      '[aria-label^="Net present value"]',
    ) as HTMLElement;
    const irrChip = document.querySelector(
      '[aria-label^="Internal rate of return"]',
    ) as HTMLElement;
    expect(npvChip.getAttribute("aria-label")).toBe(
      "Net present value (last run): ₹2,34,567",
    );
    expect(npvChip.getAttribute("title")).toBe("Net present value (last run)");
    expect(npvChip.className).not.toContain("opacity-60");
    expect(irrChip.getAttribute("aria-label")).toBe(
      "Internal rate of return (last run): 18.0%",
    );
    expect(irrChip.getAttribute("title")).toBe("Internal rate of return (last run)");
    expect(irrChip.className).not.toContain("opacity-60");

    // One editor edit later, both chips retitle and dim.
    const does = screen.getByLabelText("Does");
    await user.clear(does);
    await user.type(does, "51");
    expect(npvChip.getAttribute("aria-label")).toBe(
      "Net present value (last run): ₹2,34,567 — inputs changed since this run",
    );
    expect(npvChip.getAttribute("title")).toBe(
      "Net present value (last run) — inputs changed since this run",
    );
    expect(npvChip.className).toContain("opacity-60");
    expect(irrChip.getAttribute("aria-label")).toBe(
      "Internal rate of return (last run): 18.0% — inputs changed since this run",
    );
    expect(irrChip.getAttribute("title")).toBe(
      "Internal rate of return (last run) — inputs changed since this run",
    );
    expect(irrChip.className).toContain("opacity-60");
  });

  // Mutants 6149 (null break-even dash), 6069/6351 (metric explain dialog),
  // 4360–4366 (formatFigure key-shape regexes), 6898–6909 (histogram
  // totals/peak label), 6972 (optimization species header).
  it("renders null-safe cards, formatted figures, the histogram label and the optimization header", async () => {
    const user = await renderLoaded();
    await runOnce(user);

    // A null break-even price renders the em dash (6149 itself is
    // equivalent: formatMoney(null) also returns "—").
    const breakEvenLabel = screen.getByText("Break-even meat (₹/kg)");
    expect(breakEvenLabel.nextElementSibling?.textContent).toBe("—");

    // Monte Carlo histogram summary (mutants 6898/6899/6900/6907/6909).
    expect(
      screen.getByRole("img", {
        name: "NPV histogram: 100 runs across 4 bins, most frequent ₹1,50,000 – ₹2,50,000 with 40 runs",
      }),
    ).toBeInTheDocument();

    // Optimization table speaks the farm's species (mutant 6972).
    expect(
      screen.getByRole("columnheader", { name: "does / bucks / ceiling" }),
    ).toBeInTheDocument();

    // The metric explain dialog opens from the card (mutants 6069/6351) and
    // formats each figure by its key shape (mutants 4360–4366).
    await user.click(screen.getByRole("button", { name: "Explain NPV" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Net Present Value")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/₹2,34,567 over the 60-month horizon/),
    ).toBeInTheDocument();
    expect(dialogFacts()).toEqual({
      "Fraction Of Loan": "₹0.50",
      "Gross Margin Total": "0.32",
      "Price Uplift Extra": "₹0.40",
      "Loan Months Extra": "₹72",
      "Equipment Years Old": "₹5",
      "Dry Runs Cost": "₹1,200",
    });
  });
});

describe("SimulationPage mutation hardening: horizon presets", () => {
  // Behavior lock for the preset path. Mutants 6582/6583 (the explicit
  // validity bookkeeping on preset click) are equivalent: the key change in
  // setHorizonInputVersion remounts the field, whose notify effect re-derives
  // validity from the committed value either way.
  it("keeps the editor runnable after a horizon preset", async () => {
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "10 yr" }));
    expect(screen.getByLabelText("Horizon Months")).toHaveValue(120);
    expect(
      screen.queryByText(/highlighted numeric field/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });
});

describe("SimulationPage mutation hardening: events and recurrence", () => {
  interface EventLike {
    month: number;
    kind: string;
    animal_class: string;
    count: number;
    price_per_head: number | null;
  }

  // Mutants 4950 (recurrence state), 3364/6621 (dialog opens), 6667/6670/
  // 6673/6676 (recurrence commits), 3580/3592/3604/3616 (dialog selects),
  // 4619 (nullable price commits null).
  it("builds recurring rows from every dialog control and commits blank prices as null", async () => {
    const captured: { events?: EventLike[] } = {};
    server.use(
      http.post("/api/simulation/run", async ({ request }) => {
        const body = (await request.json()) as {
          assumptions: { events: EventLike[] };
        };
        captured.events = body.assumptions.events;
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Repeat plan" }));
    const dialog = await screen.findByRole("dialog");
    // The dialog opens pre-filled (mutant 4950).
    expect(within(dialog).getByLabelText("First month")).toHaveValue(1);
    expect(within(dialog).getByLabelText("Count")).toHaveValue(10);
    expect(within(dialog).getByLabelText("Every (months)")).toHaveValue(2);
    expect(within(dialog).getByLabelText("Repeats")).toHaveValue(6);
    expect(within(dialog).getByLabelText("Kind")).toHaveTextContent("Purchase");
    expect(within(dialog).getByLabelText("Class")).toHaveTextContent("Doe");

    await user.clear(within(dialog).getByLabelText("First month"));
    await user.type(within(dialog).getByLabelText("First month"), "3");
    await user.clear(within(dialog).getByLabelText("Count"));
    await user.type(within(dialog).getByLabelText("Count"), "7");
    await user.clear(within(dialog).getByLabelText("Every (months)"));
    await user.type(within(dialog).getByLabelText("Every (months)"), "1");
    await user.clear(within(dialog).getByLabelText("Repeats"));
    await user.type(within(dialog).getByLabelText("Repeats"), "4");

    await user.click(within(dialog).getByLabelText("Kind"));
    await user.click(await screen.findByRole("option", { name: "Sale" }));
    await user.click(within(dialog).getByLabelText("Class"));
    await user.click(await screen.findByRole("option", { name: "Female kid" }));

    await user.click(within(dialog).getByRole("button", { name: "Generate rows" }));

    expect(toastMocks.success).toHaveBeenCalledWith(
      "Added 4 recurring sale event(s).",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const inputValue = (label: string) =>
      screen
        .getAllByLabelText(label)
        .map((input) => (input as HTMLInputElement).value);
    expect(inputValue("Month")).toEqual(["3", "4", "5", "6"]);
    expect(inputValue("Count")).toEqual(["7", "7", "7", "7"]);
    for (const kind of screen.getAllByLabelText("Kind"))
      expect(kind).toHaveTextContent("Sale");
    for (const clazz of screen.getAllByLabelText("Class"))
      expect(clazz).toHaveTextContent("Female kid");

    // A price typed then blanked commits null (mutant 4619).
    const price = screen.getAllByLabelText("Price per head")[0];
    await user.type(price, "5000");
    await user.clear(price);

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(captured.events).toBeDefined());
    expect(captured.events?.[0]).toEqual({
      month: 3,
      kind: "sale",
      animal_class: "female_kid",
      count: 7,
      price_per_head: null,
    });
  });

  // Mutant 6687: Cancel closes the dialog without rows.
  it("cancels the repeat-plan dialog", async () => {
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Repeat plan" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("No scheduled events")).toBeInTheDocument();
  });

  // Mutants 6623–6629 (Repeat plan disabled), 6635 (Add event disabled),
  // 1571 (the 500-event error ceiling): exactly 500 events disable the row
  // generators without producing an error.
  it("allows exactly 500 events and disables the generators there", async () => {
    const user = await renderLoaded();

    // 240-month horizon so one plan can add 120 rows.
    await user.click(screen.getByRole("button", { name: "20 yr" }));
    expect(screen.getByLabelText("Horizon Months")).toHaveValue(240);

    for (const repeats of [120, 120, 120, 120, 20]) {
      await user.click(screen.getByRole("button", { name: "Repeat plan" }));
      const dialog = await screen.findByRole("dialog");
      const repeatInput = within(dialog).getByLabelText("Repeats");
      await user.clear(repeatInput);
      await user.type(repeatInput, String(repeats));
      await user.click(
        within(dialog).getByRole("button", { name: "Generate rows" }),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }

    expect(screen.getByRole("button", { name: "Repeat plan" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add event" })).toBeDisabled();
    // The ceiling is 500 inclusive: no error, and a run stays possible.
    expect(
      screen.queryByText(/A simulation can contain at most 500 herd events/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  }, 120000);
});

describe("SimulationPage mutation hardening: bounds recovery", () => {
  // Locks the NumberInput/NumberArrayInput bounds-recovery behaviour. The
  // listed survivors here (4638/4639/4640, 4830/4831/4832) are equivalent:
  // they only widen when recovery fires, and every extra recovery commits a
  // value the editor already holds.
  it("commits drafts that a horizon change made legal", async () => {
    const captured: {
      horizon?: number;
      events?: { month: number }[];
      festival?: number[];
    } = {};
    server.use(
      http.post("/api/simulation/run", async ({ request }) => {
        const body = (await request.json()) as {
          assumptions: {
            meta: { horizon_months: number };
            sales: { festival_sale_months: number[] };
            events: { month: number }[];
          };
        };
        captured.horizon = body.assumptions.meta.horizon_months;
        captured.events = body.assumptions.events;
        captured.festival = body.assumptions.sales.festival_sale_months;
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );
    const user = await renderLoaded({
      defaults: { ...DEFAULTS, sales: { festival_sale_months: [3] } },
    });

    await user.click(screen.getByRole("button", { name: "Add event" }));
    const horizon = screen.getByLabelText("Horizon Months");
    await user.clear(horizon);
    await user.type(horizon, "40");

    // Both drafts are legal at 60 months but illegal at 40.
    const month = screen.getAllByLabelText("Month")[0];
    await user.clear(month);
    await user.type(month, "45");
    expect(screen.getByText("Must be at most 40.")).toBeInTheDocument();
    const festival = screen.getByLabelText(/Festival Sale Months/);
    await user.clear(festival);
    await user.type(festival, "3, 50");
    expect(screen.getByText("Every entry must be at most 40.")).toBeInTheDocument();

    // Raising the horizon again clears both errors and commits the drafts.
    await user.clear(horizon);
    await user.type(horizon, "60");
    expect(screen.queryByText("Must be at most 40.")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Every entry must be at most 40."),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    await waitFor(() => expect(captured.horizon).toBeDefined());
    expect(captured.horizon).toBe(60);
    expect(captured.events?.[0]?.month).toBe(45);
    expect(captured.festival).toEqual([3, 50]);
  });
});

describe("SimulationPage mutation hardening: invalid-field bookkeeping", () => {
  // Behavior lock. Mutant 5630 (keeping only event:* validity keys) is
  // equivalent: the editorVersion bump remounts the field sections, whose
  // inputs re-notify validity from their committed values anyway.
  it("drops field errors when the current herd is loaded", async () => {
    server.use(
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
    );
    const user = await renderLoaded();

    const rate = screen.getByLabelText("Interest Rate Annual");
    await user.clear(rate);
    await user.type(rate, "0.9");
    expect(
      screen.getByText(/Fix 1 highlighted numeric field/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use current herd" }));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Loaded current herd (72 head)."),
    );
    expect(
      screen.queryByText(/highlighted numeric field/),
    ).not.toBeInTheDocument();
  });
});

describe("SimulationPage mutation hardening: save dialog", () => {
  // Behavior lock. Mutants 6837/6839/6840 (the onOpenChange error clear) are
  // equivalent: the "Save as scenario" click that reopens the dialog clears
  // saveError too, so the reopen always starts clean either way.
  it("clears the save error when the dialog closes", async () => {
    server.use(
      http.post("/api/simulation/scenarios", () =>
        HttpResponse.json({ detail: "scenario name already exists" }, { status: 409 }),
      ),
    );
    const user = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Name/), "Duplicate plan");
    await user.click(
      within(dialog).getByRole("button", { name: "Save scenario" }),
    );
    expect(
      await within(dialog).findByText("scenario name already exists"),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Save as scenario" }));
    dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).queryByText("scenario name already exists"),
    ).not.toBeInTheDocument();
  });
});

describe("SimulationPage mutation hardening: delete flow", () => {
  // Mutants 6870/6871 (delete dialog copy) and 5733 (focus moves to the
  // scenarios section after deletion). The confirmation dialog unmounts the
  // moment the delete is confirmed, so its "Deleting…" label (mutant 6876)
  // can never paint — verified unreachable.
  it("confirms deletion verbatim, then refocuses the scenarios section", async () => {
    const scenarios = [scenarioFixture({ id: 7, name: "Plan B", assumptions: DEFAULTS })];
    let releaseDelete!: () => void;
    let expectedRevision: string | null = null;
    server.use(
      http.delete("/api/simulation/scenarios/:scenarioId", ({ params, request }) => {
        expectedRevision = new URL(request.url).searchParams.get("expected_revision");
        const index = scenarios.findIndex((row) => row.id === Number(params.scenarioId));
        if (index >= 0) scenarios.splice(index, 1);
        return new Promise<Response>((resolve) => {
          releaseDelete = () => resolve(new HttpResponse(null, { status: 204 }));
        });
      }),
    );
    const user = await renderLoaded({ scenarios });

    const row = (await screen.findByText("Plan B")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    const description = within(dialog).getByText(
      /permanently deletes/,
    ).closest("p") as HTMLElement;
    expect(description.textContent).toBe(
      "This permanently deletes Plan B and its saved assumptions. Results already on screen stay until the next run.",
    );

    await user.click(
      within(dialog).getByRole("button", { name: "Delete scenario" }),
    );
    // The dialog closes immediately; the gated DELETE is still in flight.
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(expectedRevision).toBe("1");

    releaseDelete();
    await waitFor(() => {
      expect(toastMocks.success).toHaveBeenCalledWith("Scenario deleted.");
      expect(document.body.textContent).toContain("No saved scenarios yet.");
    });
    expect(document.activeElement?.id).toBe("sim-scenarios");
  });
});

describe("SimulationPage mutation hardening: scenario update guard", () => {
  // Mutant 5850: a 2xx that is not 200 must not rebind the editor to the
  // response — the next PATCH still sends the revision the editor holds.
  it("keeps the loaded revision when the update responds 201", async () => {
    const scenario = scenarioFixture({
      id: 7,
      name: "Plan B",
      assumptions: DEFAULTS,
    });
    const revisions: number[] = [];
    server.use(
      http.patch("/api/simulation/scenarios/7", async ({ request }) => {
        const body = (await request.json()) as { expected_revision: number };
        revisions.push(body.expected_revision);
        return HttpResponse.json(
          { ...scenario, revision: 2 },
          { status: 201 },
        );
      }),
    );
    const user = await renderLoaded({ scenarios: [scenario] });

    const row = (await screen.findByText("Plan B")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Load" }));
    const update = await screen.findByRole("button", { name: "Update Plan B" });

    await user.click(update);
    expect(await screen.findByRole("button", { name: "Update Plan B" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Update Plan B" }));
    await waitFor(() => expect(revisions).toHaveLength(2));
    expect(revisions).toEqual([1, 1]);
  });
});

// Mutant 5656 (the calibration generation guard) is intentionally NOT tested:
// every control that bumps calibrationParamsGeneration (breed, system,
// lookback) is also part of the calibration query key, so changing it
// mid-flight unsubscribes the old query and react-query aborts the request —
// the fetch reliably resolves as an error (first guard branch) before the
// generation comparison can run. The generation check only wins a
// resolution-vs-abort race that cannot be reproduced deterministically here.
