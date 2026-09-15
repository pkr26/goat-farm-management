/**
 * Planner page — below-md mobile card lists (md:hidden) for the sale-targets
 * editor (fully editable fields, ≥44px Remove), the evaluation report, the
 * stage plan and the saved plans, alongside the untouched desktop tables
 * (hidden md:block with their min-w floors). Mirrors the tasks board's
 * worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import PlannerPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI relies on.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

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
    after: null,
    recommended_purchases: [],
    gaps_closed: false,
    probabilities: [{ month: 13, animal_class: "male_grower", requested: 20, p_full: 0.82, p_eighty: 0.97 }],
    notes: [],
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
  ],
  actions: [],
  chains: [],
  notes: [],
};

const SAVED_PLAN = {
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
};

function registerHandlers() {
  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
    http.get("/api/planner/plans", () =>
      HttpResponse.json({ items: [SAVED_PLAN], total: 1, limit: 50, offset: 0 }),
    ),
    http.post("/api/planner/plan", () => HttpResponse.json(PLAN_REPORT)),
  );
}

/** A desktop table by its min-w floor (each planner table has a distinct one
 * — except the two 720px tables, disambiguated by order). */
function desktopTables(minW: string): HTMLElement[] {
  return Array.from(
    document.querySelectorAll(`[class~="md:block"] table[class*="${minW}"]`),
  ) as HTMLElement[];
}

/** The below-md card list inside the section card with the given title. */
function mobileCardsOf(title: string | RegExp): HTMLElement {
  const section = screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
  const list = section.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

async function renderLoaded() {
  registerHandlers();
  renderWithProviders(<PlannerPage />, createTestQueryClient());
  expect(await screen.findByText("Sale targets")).toBeInTheDocument();
}

describe("PlannerPage mobile card lists", () => {
  it("renders the sale-targets editor as editable cards with a ≥44px Remove", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add target" }));

    const cards = mobileCardsOf("Sale targets");
    expect(within(cards).getByLabelText("Sale month")).toBeInTheDocument();
    expect(within(cards).getByLabelText("Class")).toBeInTheDocument();
    expect(within(cards).getByLabelText("Count")).toBeInTheDocument();
    expect(
      within(cards).getByRole("button", { name: /^Remove target / }),
    ).toHaveClass("h-11");

    // The desktop editor table keeps its 720px floor.
    expect(desktopTables("min-w-[720px]").length).toBeGreaterThan(0);
  });

  it("renders the evaluation report and stage plan as cards after a run", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await screen.findByText(/Shortfall 5\.8 head/);

    // Evaluation card: month/class heading with the fill and risk figures.
    const evaluationCards = mobileCardsOf(/The plan cannot fully close/);
    expect(within(evaluationCards).getByText(/Male grower/)).toBeInTheDocument();
    expect(within(evaluationCards).getByText("⚠ 14.2")).toBeInTheDocument();
    expect(within(evaluationCards).getByText("82%")).toBeInTheDocument();
    expect(desktopTables("min-w-[820px]")).toHaveLength(1);

    // Stage-plan card: month, total head and the flows line.
    const stageCards = mobileCardsOf(/Stage plan/);
    expect(within(stageCards).getByText("72.7 head")).toBeInTheDocument();
    expect(within(stageCards).getByText(/Born 7\.8 · Died 0\.6/)).toBeInTheDocument();
    expect(within(stageCards).getByText("28.4")).toBeInTheDocument();
    expect(desktopTables("min-w-[1100px]")).toHaveLength(1);

    // The 16-column matrix carries a screen-reader caption summarizing it.
    const stageTable = desktopTables("min-w-[1100px]")[0]!;
    expect(stageTable.querySelector("caption")).toHaveTextContent(
      "End-of-month herd head by stage",
    );
  });

  it("renders saved plans as cards with ≥44px Open/Delete actions", async () => {
    await renderLoaded();
    await screen.findAllByText("Festival plan");

    const cards = mobileCardsOf("Saved plans");
    expect(within(cards).getByText("Festival plan")).toBeInTheDocument();
    expect(within(cards).getByText(/20 Male grower/)).toBeInTheDocument();
    expect(within(cards).getByRole("button", { name: "Open" })).toHaveClass("h-11");
    expect(
      within(cards).getByRole("button", { name: "Delete plan Festival plan" }),
    ).toHaveClass("h-11");

    // The desktop saved-plans table keeps compact actions.
    const tables = desktopTables("min-w-[720px]");
    const savedTable = tables.find((table) => within(table).queryByText("Festival plan"));
    expect(savedTable).toBeDefined();
    expect(within(savedTable!).getByRole("button", { name: "Open" })).toHaveClass("h-9");
  });
});
