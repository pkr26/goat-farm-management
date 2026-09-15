/**
 * Mutation-hardening tests for the planner page. Each test pins behaviour
 * Stryker found unguarded: validation boundaries (month format, horizon
 * ceiling, count bounds), class-label capitalisation from the farm
 * vocabulary, NumberField commit/blur semantics, permission gates for the
 * herd/calibration basis, the breed-preset query contract, stale-report
 * flags, success-status gates and number formatting (null → "—", one
 * decimal for expected head counts).
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_FARMS } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import PlannerPage from "./page";

/** Targets-editor fields and report/plan rows also render in below-md card
 * lists (md:hidden) — scope to a desktop table by its min-w floor so
 * duplicated content stays unambiguous. */
function desktopTable(minW: string): HTMLElement {
  const table = document.querySelector(`[class~="md:block"] table[class*="${minW}"]`);
  expect(table).not.toBeNull();
  return table as HTMLElement;
}


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

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthLabel(yearMonth: string): string {
  const [year, month] = yearMonth.split("-").map(Number);
  return `${MONTH_NAMES[month - 1]} ${year}`;
}

function planReport(overrides: Record<string, unknown> = {}) {
  const targetMonth = addMonths(currentYearMonth(), 12);
  return {
    start_year_month: currentYearMonth(),
    horizon_months: 60,
    targets_echo: [{ year_month: targetMonth, animal_class: "male_grower", count: 20 }],
    plan: {
      before: { targets: [], npv: 0, minimum_cash_balance: 0, minimum_cash_month: 1, additional_working_capital_required: 0, total_shortfall: 0, all_met: false },
      after: {
        targets: [{ month: 13, animal_class: "male_grower", requested: 20, filled: 20, shortfall: 0, price_per_head: 9800, revenue: 196000, met: true }],
        npv: 100,
        minimum_cash_balance: 0,
        minimum_cash_month: 1,
        additional_working_capital_required: 0,
        total_shortfall: 0,
        all_met: true,
      },
      recommended_purchases: [],
      gaps_closed: true,
      probabilities: [],
      notes: [],
    },
    stage_plan: [{ month: 1, year_month: currentYearMonth(), female_kids: 3.7, male_kids: 3.7, female_weaners: 3.5, male_weaners: 3.5, female_growers: 3.2, male_growers: 3.2, open_does: 6.5, pregnant_does: 28.4, lactating_does: 14.9, bucks: 2.1, total_head: 72.7, births: 7.8, deaths: 0.6, culls_head: 0, sales_head: 0, purchases_head: 1 }],
    actions: [],
    chains: [],
    notes: [],
    ...overrides,
  };
}

interface Options {
  defaults?: unknown;
  farms?: unknown;
  permissions?: string[];
  planStatus?: number;
  planResult?: unknown;
  onPlan?: (body: unknown) => void;
  onDefaults?: (query: URL) => void;
  snapshotStatus?: number;
  snapshotBody?: unknown;
  snapshotNetworkError?: boolean;
  calibrationStatus?: number;
  calibrationBody?: unknown;
  calibrationNetworkError?: boolean;
  savedPlans?: unknown[];
}

async function renderLoaded(options: Options = {}) {
  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json(options.farms ?? TEST_FARMS)),
    ...(options.permissions ? [permissionsHandler(options.permissions)] : []),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi", "sirohi"], systems: ["stall_fed", "semi_intensive"] }),
    ),
    http.get("/api/simulation/defaults", ({ request }) => {
      options.onDefaults?.(new URL(request.url));
      return HttpResponse.json(options.defaults ?? GOAT_DEFAULTS);
    }),
    http.get("/api/simulation/herd-snapshot", () =>
      options.snapshotNetworkError
        ? HttpResponse.error()
        : HttpResponse.json(options.snapshotBody ?? {
        does: 50, bucks: 2, f_kids: 3, f_weaners: 2, f_growers: 1, m_kids: 3, m_weaners: 2, m_growers: 1, total_head: 64,
      }, { status: options.snapshotStatus ?? 200 }),
    ),
    http.get("/api/simulation/calibration", () =>
      options.calibrationNetworkError
        ? HttpResponse.error()
        : HttpResponse.json(options.calibrationBody ?? { assumptions: GOAT_DEFAULTS, evidence: [1, 2, 3] }, { status: options.calibrationStatus ?? 200 }),
    ),
    http.get("/api/planner/plans", () =>
      HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
    ),
    http.post("/api/planner/plan", async ({ request }) => {
      options.onPlan?.(await request.json());
      return HttpResponse.json(options.planResult ?? planReport(), { status: options.planStatus ?? 200 });
    }),
  );
  renderWithProviders(<PlannerPage />, createTestQueryClient());
  expect(await screen.findByText("Sale targets")).toBeInTheDocument();
}

async function addTarget(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Add target" }));
}

function saleMonthInput(): HTMLInputElement {
  return within(desktopTable("min-w-[720px]")).getByLabelText("Sale month") as HTMLInputElement;
}

const countInput = () => within(desktopTable("min-w-[720px]")).getByLabelText("Count") as HTMLInputElement;

describe("PlannerPage mutation hardening: target validation", () => {
  it("rejects malformed year-months (format, month 13, trailing garbage)", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);

    fireEvent.change(saleMonthInput(), { target: { value: "2026-01-99" } });
    expect(screen.getByText(/pick a real month \(YYYY-MM\)/i)).toBeInTheDocument();

    fireEvent.change(saleMonthInput(), { target: { value: "2026-13" } });
    expect(screen.getByText(/pick a real month \(YYYY-MM\)/i)).toBeInTheDocument();

    fireEvent.change(saleMonthInput(), { target: { value: "26-01" } });
    expect(screen.getByText(/pick a real month \(YYYY-MM\)/i)).toBeInTheDocument();
  });

  it("rejects a sale in the plan's own start month, naming the month", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);

    fireEvent.change(saleMonthInput(), { target: { value: currentYearMonth() } });
    expect(
      screen.getByText(
        `Target 1: the month must come after the plan start (${monthLabel(currentYearMonth())}).`,
      ),
    ).toBeInTheDocument();
  });

  it("accepts a sale at the 240-month ceiling and rejects one beyond it", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);

    fireEvent.change(saleMonthInput(), { target: { value: addMonths(currentYearMonth(), 239) } });
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Plan" })).toBeEnabled();

    fireEvent.change(saleMonthInput(), { target: { value: addMonths(currentYearMonth(), 240) } });
    expect(screen.getByText(/beyond the 20-year planning ceiling/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Plan" })).toBeDisabled();
  });

  it("accepts a count of exactly 100 000 and rejects more", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);

    await user.clear(countInput());
    await user.type(countInput(), "100000");
    expect(screen.queryAllByRole("alert")).toHaveLength(0);

    await user.clear(countInput());
    await user.type(countInput(), "100001");
    expect(
      screen.getByText(/count must be greater than 0 and at most 100,000/i),
    ).toBeInTheDocument();
  });

  it("numbers the target rows in order", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);
    await addTarget(user);
    await fireEvent.change(within(desktopTable("min-w-[720px]")).getAllByLabelText("Sale month")[1], {
      target: { value: currentYearMonth() },
    });
    expect(screen.getByText(/Target 2: the month must come after/i)).toBeInTheDocument();
  });
});

describe("PlannerPage mutation hardening: class vocabulary and number field", () => {
  it("labels classes with capitalised farm vocabulary (Doe, Buck, Female kid…)", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);

    await user.click(within(desktopTable("min-w-[720px]")).getByLabelText("Class"));
    for (const label of ["Doe", "Buck", "Female kid", "Male kid", "Female weaner", "Male weaner", "Female grower", "Male grower"]) {
      expect(await screen.findByRole("option", { name: label })).toBeInTheDocument();
    }
  });

  it("renders class labels with the farm's own vocabulary in the report", async () => {
    const user = userEvent.setup();
    const targetMonth = addMonths(currentYearMonth(), 12);
    await renderLoaded({
      planResult: planReport({
        chains: [{ year_month: targetMonth, animal_class: "doe", count: 9, achievable: true, steps: [], explanation: "" }],
      }),
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(/9 Doe in/)).toBeInTheDocument();
  });

  it("does not commit a cleared count and restores the stored value on blur", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);

    await user.clear(countInput());
    expect(countInput().value).toBe("");
    await user.tab();
    expect(countInput().value).toBe("20");
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Plan" })).toBeEnabled();
  });

  it("formats requested counts as whole/one-decimal and null heads as an em dash", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      planResult: planReport({
        plan: {
          before: { targets: [{ month: 13, animal_class: "male_grower", requested: 20.5, filled: 18.25, shortfall: 2.25, price_per_head: 9800, revenue: 179000, met: false }], npv: 0, minimum_cash_balance: 0, minimum_cash_month: 1, additional_working_capital_required: 0, total_shortfall: 5.25, all_met: false },
          after: {
            targets: [{ month: 13, animal_class: "male_grower", requested: 20.5, filled: 18.25, shortfall: 2.25, price_per_head: 9800, revenue: 179000, met: false }],
            npv: 100, minimum_cash_balance: 0, minimum_cash_month: 1, additional_working_capital_required: 0, total_shortfall: 5.25, all_met: false,
          },
          recommended_purchases: [], gaps_closed: false, probabilities: [], notes: [],
        },
        stage_plan: [{ month: 1, year_month: currentYearMonth(), female_kids: null, male_kids: 3.7, female_weaners: 3.5, male_weaners: 3.5, female_growers: 3.2, male_growers: 3.2, open_does: 6.5, pregnant_does: 28.4, lactating_does: 14.9, bucks: 2.1, total_head: 72.7, births: null, deaths: 0.6, culls_head: 0, sales_head: 0, purchases_head: 1 }] as unknown[],
      }),
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));

    expect(await screen.findByText(/Shortfall 5\.3 head/)).toBeInTheDocument();
    // The evaluation table (and its below-md card mirror) both carry these
    // figures — scope to the desktop table.
    const evaluationTable = within(desktopTable("min-w-[820px]"));
    expect(evaluationTable.getByText("20.5")).toBeInTheDocument();
    expect(evaluationTable.getAllByText("⚠ 18.3").length).toBe(2);
    // Null stage-plan cells render the em dash, not "null" or "NaN".
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("PlannerPage mutation hardening: plan basis", () => {
  it("loads the species-default breed preset with the stall-fed system", async () => {
    const seen = new Set<string>();
    await renderLoaded({
      onDefaults: (url) => seen.add(url.searchParams.toString()),
    });
    await waitFor(() => expect(seen.size).toBeGreaterThan(0));
    expect([...seen][0]).toContain("breed=osmanabadi");
    expect([...seen][0]).toContain("system=stall_fed");
    expect(await screen.findByText(/Starting from breed-preset defaults\./)).toBeInTheDocument();
  });

  it("adopts the live herd with a toast and a basis note", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    expect(await screen.findByText(/Starting from your live herd's head counts/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(
        "Starting stock set to your current herd (64 head).",
      ),
    );
  });

  it("surfaces a herd-snapshot failure with the server detail", async () => {
    const user = userEvent.setup();
    await renderLoaded({ snapshotStatus: 409, snapshotBody: { detail: "herd locked" } });
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("herd locked"));
  });

  it("falls back to a generic message when the snapshot request fails at the network level", async () => {
    const user = userEvent.setup();
    await renderLoaded({ snapshotNetworkError: true });
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not load the herd snapshot."),
    );
  });

  it("calibrates from farm records with an evidence count", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    expect(
      await screen.findByText(/Starting from assumptions calibrated against/i),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Calibrated 3 assumptions from your farm records."),
    );
  });

  it("falls back when the calibration request fails at the network level", async () => {
    const user = userEvent.setup();
    await renderLoaded({ calibrationNetworkError: true });
    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not calibrate from farm records."),
    );
  });

  it("gates the herd and calibration buttons behind their permissions", async () => {
    await renderLoaded({
      permissions: ["simulation.view", "simulation.manage"],
    });
    const herd = await screen.findByRole("button", { name: "Use my herd" });
    expect(herd).toBeDisabled();
    const calibrate = screen.getByRole("button", { name: "Use farm records" });
    expect(calibrate).toBeDisabled();
    expect(calibrate).toHaveAttribute(
      "title",
      "Needs animals, breeding, births, feeding and finance read access.",
    );
  });

  it("keeps both basis buttons enabled for a full owner", async () => {
    await renderLoaded();
    expect(await screen.findByRole("button", { name: "Use my herd" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Use farm records" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Use farm records" })).not.toHaveAttribute("title");
  });
});

describe("PlannerPage mutation hardening: run gates and staleness", () => {
  it("treats a non-200 plan response as a failure (no report rendered)", async () => {
    const user = userEvent.setup();
    await renderLoaded({ planStatus: 201, planResult: planReport() });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.queryByText("Shortfall")).toBeNull());
  });

  it("flags the report stale once the targets change after a run", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(/Shortfall 0 head/)).toBeInTheDocument();
    expect(screen.queryByText(/stale — re-run after edits/)).toBeNull();

    await user.clear(countInput());
    await user.type(countInput(), "25");
    expect(await screen.findByText(/stale — re-run after edits/)).toBeInTheDocument();
  });



});
