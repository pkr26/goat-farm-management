/**
 * Planner page: target-based backward planning. The targets editor posts
 * calendar-dated targets plus the anchored assumptions to /api/planner/plan
 * and renders the feasibility table, dated action timeline, requirement
 * chains and the stage plan. Dairy farms additionally get the milk-target
 * section. Plans save to /api/planner/plans. Request failures surface as an
 * alert.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import PlannerPage from "./page";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const GOAT_DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  reproduction: { gestation_months: 5, conception_rate: 0.85, litter_size: 1.6 },
  growth: { sale_age_months: 9 },
  mortality: { kid_pre_weaning: 0.15, kid_post_weaning: 0.05 },
  sales: { meat_price_per_kg: 400 },
};

function currentYearMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function addMonths(yearMonth: string, months: number): string {
  const [year, month] = yearMonth.split("-").map(Number);
  const total = year * 12 + (month - 1) + months;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

const PLAN_REPORT = {
  start_year_month: currentYearMonth(),
  horizon_months: 60,
  targets_echo: [
    { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
  ],
  plan: {
    before: {
      targets: [
        {
          month: 13,
          animal_class: "male_grower",
          requested: 20,
          filled: 14.2,
          shortfall: 5.8,
          price_per_head: 9800,
          revenue: 139160,
          met: false,
        },
      ],
      npv: 1250000,
      minimum_cash_balance: -40000,
      minimum_cash_month: 6,
      additional_working_capital_required: 40000,
      total_shortfall: 5.8,
      all_met: false,
    },
    after: {
      targets: [
        {
          month: 13,
          animal_class: "male_grower",
          requested: 20,
          filled: 20.1,
          shortfall: 0,
          price_per_head: 9800,
          revenue: 196980,
          met: true,
        },
      ],
      npv: 1410000,
      minimum_cash_balance: -3000,
      minimum_cash_month: 4,
      additional_working_capital_required: 3000,
      total_shortfall: 0,
      all_met: true,
    },
    recommended_purchases: [{ month: 2, kind: "purchase", animal_class: "doe", count: 12 }],
    gaps_closed: true,
    probabilities: [{ month: 13, animal_class: "male_grower", requested: 20, p_full: 0.82, p_eighty: 0.97 }],
    notes: ["Month 13: selling 20 male grower(s) exceeds the projected pool by 5.8 head."],
  },
  stage_plan: [
    {
      month: 1,
      year_month: currentYearMonth(),
      female_kids: 3.7,
      male_kids: 3.7,
      female_weaners: 3.5,
      male_weaners: 3.5,
      female_growers: 3.2,
      male_growers: 3.2,
      open_does: 6.5,
      pregnant_does: 28.4,
      lactating_does: 14.9,
      bucks: 2.1,
      total_head: 72.7,
      births: 7.8,
      deaths: 0.6,
      culls_head: 0,
      sales_head: 0,
      purchases_head: 1,
    },
    {
      month: 13,
      year_month: addMonths(currentYearMonth(), 12),
      female_kids: 4.1,
      male_kids: 4.1,
      female_weaners: 3.9,
      male_weaners: 3.9,
      female_growers: 3.6,
      male_growers: 3.4,
      open_does: 7.2,
      pregnant_does: 30.1,
      lactating_does: 15.6,
      bucks: 2.3,
      total_head: 78.2,
      births: 8.2,
      deaths: 0.7,
      culls_head: 0.5,
      sales_head: 20,
      purchases_head: 0,
    },
  ],
  actions: [
    {
      month: 2,
      year_month: addMonths(currentYearMonth(), 1),
      kind: "purchase",
      headline: "Buy ~12 does",
      detail: "Young proven does bought in this month settle, breed and raise the young stock a later sale target needs.",
    },
    {
      month: 6,
      year_month: addMonths(currentYearMonth(), 5),
      kind: "breed",
      headline: "Breed ~34 does (male grower(s) for the target)",
      detail: "Serve every open, settled doe this month; conception ≈ 85% per service.",
    },
    {
      month: 13,
      year_month: addMonths(currentYearMonth(), 12),
      kind: "sell",
      headline: "Sell 20 male grower(s)",
      detail: "Planned revenue ≈ ₹1,96,980. The closed plan fills this target.",
    },
  ],
  chains: [
    {
      year_month: addMonths(currentYearMonth(), 12),
      animal_class: "male_grower",
      count: 20,
      achievable: true,
      steps: [
        { year_month: addMonths(currentYearMonth(), 5), quantity: 34, label: "breedable doe(s) bred (one service)" },
        { year_month: addMonths(currentYearMonth(), 10), quantity: 29, label: "doe(s) due to kidding" },
        { year_month: addMonths(currentYearMonth(), 10), quantity: 45, label: "kids born (both sexes)" },
        { year_month: addMonths(currentYearMonth(), 12), quantity: 20, label: "male grower(s) sold" },
      ],
      explanation:
        "Selling 20 male grower(s) at ~7 months needs ~21 alive at sale; with 12% born-to-sale mortality that is ~45 kids born, from ~29 does kidding and ~34 bred about 5 months earlier.",
    },
  ],
  notes: [`Plan runs ${currentYearMonth()} → ${addMonths(currentYearMonth(), 12)} (13 months).`],
};

interface PlanBody {
  targets: { year_month: string; animal_class: string; count: number }[];
  close_gaps: boolean;
  risk_runs: number;
  assumptions: { meta?: { start_year_month?: string } } & Record<string, unknown>;
}

function savedPlanRow() {
  return [
    {
      id: 7,
      farm_id: 1,
      name: "Festival plan",
      notes: "",
      start_year_month: currentYearMonth(),
      targets: [
        { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
      ],
      assumptions: GOAT_DEFAULTS,
      valid: true,
      validation_error: null,
      revision: 1,
      created_at: "2026-09-02T10:00:00Z",
      updated_at: "2026-09-02T10:00:00Z",
    },
  ];
}

function registerApiHandlers(options: {
  defaults?: unknown;
  farms?: unknown;
  onPlan?: (body: PlanBody) => void;
  planResult?: unknown;
  planStatus?: number;
  savedPlans?: unknown[];
  onSave?: (body: Record<string, unknown>) => void;
  onMilkPlan?: () => void;
  milkPlanResult?: unknown;
  onUpdate?: (query: Record<string, string>, body: Record<string, unknown>) => void;
  savedPlan?: unknown;
} = {}) {
  server.use(
    http.get("/api/auth/farms", () =>
      HttpResponse.json(options.farms ?? TEST_FARMS),
    ),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi", "murrah_dairy"], systems: ["stall_fed", "semi_intensive"] }),
    ),
    http.get("/api/simulation/defaults", () =>
      HttpResponse.json(options.defaults ?? GOAT_DEFAULTS),
    ),
    http.get("/api/simulation/herd-snapshot", () =>
      HttpResponse.json({
        does: 50,
        bucks: 2,
        f_kids: 0,
        f_weaners: 0,
        f_growers: 0,
        m_kids: 0,
        m_weaners: 0,
        m_growers: 0,
        total_head: 52,
      }),
    ),
    http.get("/api/planner/plans", () =>
      HttpResponse.json({
        items: options.savedPlans ?? (options.onUpdate ? savedPlanRow() : []),
        total: options.savedPlans?.length ?? (options.onUpdate ? 1 : 0),
        limit: 50,
        offset: 0,
      }),
    ),
    http.post("/api/planner/plan", async ({ request }) => {
      options.onPlan?.((await request.json()) as PlanBody);
      return HttpResponse.json(options.planResult ?? PLAN_REPORT, {
        status: options.planStatus ?? 200,
      });
    }),
    http.post("/api/planner/milk-plan", async () => {
      options.onMilkPlan?.();
      return HttpResponse.json(options.milkPlanResult ?? {}, { status: 200 });
    }),
    http.patch("/api/planner/plans/:id", async ({ request }) => {
      options.onUpdate?.(
        Object.fromEntries(new URL(request.url).searchParams),
        (await request.json()) as Record<string, unknown>,
      );
      return HttpResponse.json(
        {
          id: 7,
          farm_id: 1,
          name: "Renamed plan",
          notes: "",
          start_year_month: currentYearMonth(),
          targets: [
            { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
          ],
          assumptions: GOAT_DEFAULTS,
          valid: true,
          validation_error: null,
          revision: 2,
          created_at: "2026-09-02T10:00:00Z",
          updated_at: "2026-09-02T10:00:00Z",
        },
        { status: 200 },
      );
    }),
    http.post("/api/planner/plans", async ({ request }) => {
      options.onSave?.((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(
        {
          id: 7,
          farm_id: 1,
          name: "Festival plan",
          notes: "",
          start_year_month: currentYearMonth(),
          targets: [
            { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
          ],
          assumptions: GOAT_DEFAULTS,
          valid: true,
          validation_error: null,
          revision: 1,
          created_at: "2026-09-02T10:00:00Z",
          updated_at: "2026-09-02T10:00:00Z",
        },
        { status: 201 },
      );
    }),
  );
}

async function renderLoaded(options?: Parameters<typeof registerApiHandlers>[0]) {
  registerApiHandlers(options);
  const view = renderWithProviders(<PlannerPage />, createTestQueryClient());
  expect(await screen.findByText("Sale targets")).toBeInTheDocument();
  return view;
}

const targetMonthLabel = (offset: number) => {
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [year, month] = addMonths(currentYearMonth(), offset).split("-").map(Number);
  return `${names[month - 1]} ${year}`;
};

describe("PlannerPage backward plan", () => {
  it("sends calendar-dated targets with anchored assumptions and renders the plan", async () => {
    const captured: { body: PlanBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onPlan: (body) => {
        captured.body = body;
      },
    });

    await user.click(screen.getByRole("button", { name: "Add target" }));
    const count = screen.getByLabelText("Count");
    await user.clear(count);
    await user.type(count, "20");

    await user.click(screen.getByRole("button", { name: "Plan" }));

    expect(captured.body).not.toBeNull();
    expect(captured.body?.targets).toEqual([
      { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
    ]);
    expect(captured.body?.close_gaps).toBe(true);
    expect(captured.body?.risk_runs).toBe(100);
    expect(captured.body?.assumptions?.meta?.start_year_month).toBe(currentYearMonth());

    // Feasibility verdict with the calendar labels the farmer plans in.
    expect(await screen.findByText("The plan is feasible")).toBeInTheDocument();
    // The sale month appears in both the feasibility table and the stage plan.
    expect(screen.getAllByText(targetMonthLabel(12)).length).toBeGreaterThan(0);
    // Action timeline: buy, breed, sell with dates.
    expect(screen.getByText("Buy ~12 does")).toBeInTheDocument();
    expect(screen.getByText(/Breed ~34 does/)).toBeInTheDocument();
    expect(screen.getByText("Sell 20 male grower(s)")).toBeInTheDocument();
    // Requirement chain with its backward steps and explanation.
    expect(screen.getByText("breedable doe(s) bred (one service)")).toBeInTheDocument();
    expect(screen.getByText(/45 kids born/)).toBeInTheDocument();
    // Stage plan rows carry month labels and head counts.
    expect(screen.getByText("Stage plan — the herd shape the targets require")).toBeInTheDocument();
    expect(screen.getAllByText("78.2").length).toBeGreaterThan(0);
  });

  it("saves the current targets as a named plan", async () => {
    const saved: { body: Record<string, unknown> | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({ onSave: (body) => (saved.body = body) });

    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.type(screen.getByLabelText("Plan name"), "Festival plan");
    await user.click(screen.getByRole("button", { name: "Save plan" }));

    await waitFor(() => expect(saved.body).not.toBeNull());
    expect(saved.body?.name).toBe("Festival plan");
    expect(saved.body?.start_year_month).toBe(currentYearMonth());
    expect(saved.body?.targets).toEqual([
      { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
    ]);
    expect((saved.body?.assumptions as { meta?: { start_year_month?: string } }).meta
      ?.start_year_month).toBe(currentYearMonth());
  });

  it("sends the edited name when updating a saved plan (rename reaches the API)", async () => {
    const updated: { body: Record<string, unknown> | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onUpdate: (_query, body) => {
        updated.body = body;
      },
    });

    // Open the saved plan, rename it, update.
    await user.click(await screen.findByRole("button", { name: "Open" }));
    const name = screen.getByLabelText("Plan name");
    await user.clear(name);
    await user.type(name, "Renamed plan");
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));

    await waitFor(() => expect(updated.body).not.toBeNull());
    expect(updated.body?.name).toBe("Renamed plan");
    expect(updated.body?.expected_revision).toBe(1);
  });

  it("surfaces a failed plan request as an alert", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      planStatus: 422,
      planResult: { detail: "Target month 2025-12 is at or before the plan start 2026-09" },
    });

    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("button", { name: "Plan" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/at or before the plan start/);
  });

  it("blocks the plan while a target is invalid", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Add target" }));
    // A count of 0 is not a sale target.
    const count = screen.getByLabelText("Count");
    await user.clear(count);
    await user.type(count, "0");

    expect(screen.getByRole("button", { name: "Plan" })).toBeDisabled();
    expect(screen.getByText(/count must be greater than 0/i)).toBeInTheDocument();
  });
});

describe("PlannerPage milk target section", () => {
  const DAIRY_FARM = { ...TEST_FARMS[0], name: "Test Dairy", farm_type: "BUFFALO_DAIRY" };
  const DAIRY_DEFAULTS = {
    ...GOAT_DEFAULTS,
    herd: { does: 60, bucks: 0 },
    reproduction: { ...GOAT_DEFAULTS.reproduction, lactation_months: 10, gestation_months: 10 },
    sales: { ...GOAT_DEFAULTS.sales, lactation_milk_litres: 2400 },
  };

  it("appears for a buffalo dairy farm and not for a goat farm", async () => {
    await renderLoaded({ farms: [DAIRY_FARM], defaults: DAIRY_DEFAULTS });
    expect(
      await screen.findByText("Milk target", { selector: "[data-slot='card-title'], h2, h3" }),
    ).toBeInTheDocument();
  });

  it("stays hidden for a goat farm", async () => {
    await renderLoaded();
    await screen.findByText("Plan basis");
    expect(
      screen.queryByText("Milk target", { selector: "[data-slot='card-title'], h2, h3" }),
    ).not.toBeInTheDocument();
  });

  it("plans backward from a litres target and explains the curve math", async () => {
    const milkReport = {
      target_daily_litres: 1000,
      ramp_months: 1,
      hold_year_round: false,
      curve: {
        shape: "wood",
        lactation_litres: 2100,
        lactation_months: 10,
        peak_day: 65,
        peak_month_of_lactation: 3,
        peak_daily_litres: 10.2,
        avg_daily_litres_per_milking_doe: 6.9,
        monthly_litres: [220, 290, 311, 292, 258, 224, 192, 161, 133, 119],
      },
      herd: {
        daily_target_litres: 1000,
        calving_interval_months: 13.7,
        expected_services_per_conception: 2.2,
        breeding_does: 204,
        milking_does: 167,
        dry_does: 37,
        dry_months_per_cycle: 3.7,
        milking_share_of_herd: 0.82,
        calvings_per_month: 15.4,
        ai_services_per_month: 34.7,
        replacement_does_per_month: 3.9,
        heifer_calves_available_per_month: 7.1,
        heifer_surplus_per_month: 3.2,
        starting_does_credited: 60,
        purchases_total: 151,
        replacement_purchases_total: 7,
        seasonal_low_daily_litres: 860,
        seasonal_high_daily_litres: 1080,
        herd_for_year_round_target: 233,
      },
      purchases: [
        { month: 1, count: 144, profile: "in-milk buffalo at mixed lactation stages" },
      ],
      projection: [
        {
          month: 1,
          calendar_month: 1,
          breeding_does: 207,
          milking_does: 143,
          dry_does: 64,
          freshenings: 15.9,
          ai_services: 31.9,
          heifer_graduates: 0,
          projected_daily_litres: 1027,
          target_daily_litres: 1000,
          gap_daily_litres: -27,
          projected_monthly_litres: 31270,
          projected_monthly_revenue: 2019482,
          meets_target: true,
        },
      ],
      steady_from_month: 1,
      steady_average_daily_litres: 1000,
      achievable: true,
      explanations: [
        "1. What one animal gives: one milking buffalo produces 2,100 L over her 10-month lactation, following the lactation curve: rising after calving to a peak of ~10.2 L/day in month 3 of lactation, then declining to ~3.9 L/day in the final month.",
        "2. Why she stops: milk does not fade to zero — it STOPS. For the last ~4 months of every 13.7-month calving cycle she is dry (heavily pregnant, preparing the next calving): eating, not milking.",
        "3. Target → calvings: 1,000 L/day × 30 days = 30,440 L/month; at 2,100 L per lactation that needs ~15.4 fresh calvings EVERY month, all year.",
        "4. Why every month: each month-of-lactation stage of the curve yields a different amount, so the tank stays flat only if the herd holds animals in every stage and new calvings arrive continuously.",
        "5. Calvings → herd: one cycle lasts ~13.7 months, so ~15.4 calvings/month needs a breeding herd of ~204. On an average day ~167 (82%) are milking and ~37 (18%) are dry.",
        "6. How purchases fill the gap: the plan buys ~144 in-milk animals at MIXED lactation stages, staged over 1 month.",
      ],
      notes: ["To land calvings in month T, start AI about 12 months earlier."],
    };
    const user = userEvent.setup();
    await renderLoaded({
      farms: [DAIRY_FARM],
      defaults: DAIRY_DEFAULTS,
      onMilkPlan: () => {},
      milkPlanResult: milkReport,
    });

    await user.click(screen.getByRole("button", { name: "Plan milk" }));

    // The backward-math story renders with its own numbers.
    expect(await screen.findByText(/Why this herd — the math behind/)).toBeInTheDocument();
    expect(screen.getByText(/it STOPS/)).toBeInTheDocument();
    expect(screen.getByText(/~15\.4 fresh calvings EVERY month/)).toBeInTheDocument();
    expect(screen.getByText(/MIXED lactation stages/)).toBeInTheDocument();
    // Dry-period arithmetic in the herd-design headline.
    expect(screen.getByText(/3\.7 dry months per 13\.7-month cycle/)).toBeInTheDocument();
    expect(screen.getByText(/\(82% in milk\)/)).toBeInTheDocument();
    // The curve table shows the peak month and the dry rows after dry-off.
    // It starts collapsed: open it first.
    await user.click(screen.getByText(/The lactation curve the plan runs on/));
    expect(screen.getByText("3 (peak)")).toBeInTheDocument();
    expect(screen.getAllByText("0 (dry)").length).toBeGreaterThan(0);
    expect(screen.getByText(/late pregnancy — eating, not milking/)).toBeInTheDocument();
  });
});
