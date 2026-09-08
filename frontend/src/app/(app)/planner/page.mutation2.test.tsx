/**
 * Mutation-hardening tests for the planner page, round 2. Walks the remaining
 * Stryker survivor clusters from reports/mutation/app1.json:
 *
 *  - month-string parsing/formatting passthrough (regex anchors, guard)
 *  - formatHead null/NaN → "—"
 *  - permission surface: loading skeleton, error state, access-denied with
 *    queries disabled, partial calibration permission set (.every vs .some)
 *  - plan-basis machinery: query contracts (breed param, enabled flags, plan
 *    list params), herd/calibration status gates and payload merging
 *  - targets editor: empty-state copy, per-row update/remove, class select,
 *    MAX_TARGETS cap, row keys
 *  - run gates: pending label, non-200 status gates, error clearing, report
 *    rendering (missed-deadline styling, joiners, ✓/⚠ cells, risk %, notes)
 *  - save/update/delete/open flows incl. name trimming, revision toasts,
 *    list invalidation and the breed-preset reset on open
 *  - saved-plans table: valid filter, targets/notes cells, pagination note
 *
 * Mutants provably equivalent (documented, not tested):
 *  - page.tsx L153 `value === null`/`value === undefined` → false and both
 *    `&&` groupings: `!Number.isFinite(null|undefined)` already returns true,
 *    and non-finite NUMBERS cannot arrive — the values cross JSON, where
 *    NaN/Infinity serialize to null.
 *  - L265 acceptDefaultsRef.current = false → true inside the defaults
 *    effect: the only later reader is gated behind loadBreedDefaults which
 *    re-arms the ref, so no observable difference.
 *  - L293/L326 `res.data?.status` optional chaining: the envelope client
 *    always yields data when !isError.
 *  - L350/L611 counter++ → counter--: keys stay unique.
 *  - L391/L449 `report !== null` → true: the render is already gated on
 *    `{report && evaluation && …}`.
 *  - L395/L405/L454/L501/L505/L535/L537 guard operands: the Plan/Plan
 *    Save/Update buttons are disabled by the same conditions
 *    (!assumptions, no targets, target errors, empty trimmed name, no open
 *    plan), so the in-handler re-checks are unreachable defensive depth.
 *  - L586 catch {} in client_get_plan: swallowing the error and returning
 *    null are indistinguishable to the only caller (`if (refreshed)`).
 *  - L996 `report?.start_year_month ?? startMonth` chaining/&&: evaluated
 *    only inside `{report && …}` with a non-null start_year_month.
 *  - L147 formatPlanCount "—": reachable only for non-finite numbers, which
 *    JSON cannot transport. L209 NumberField value ?? "": the value prop is
 *    always supplied. L318/L319, L336/L337 catch fallbacks: react-query
 *    captures handler rejections into res.isError, so refetch() never
 *    rejects and the catches are unreachable.
 *  - L1134/L1273 `?? []` fallbacks inside .map(...): v8 coverage attributes
 *    these to the sibling length-check expressions (whose mutants ARE
 *    killed by the null-notes/null-explanations tests); the rendered output
 *    of the fallback swap is asserted there too ("Stryker was here" absent).
 *  - React `key` template literals (L965, L999, L1031, L1057, L1103, L1135,
 *    L1275, L1300, L1348, L1415): static lists; empty keys render the same
 *    DOM (the data-level key generators at L350/L611 ARE tested via
 *    two-row removal).
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_FARMS } from "@/test/msw-server";
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
  herd: { does: 50, bucks: 2, yearling_does: 9 },
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
  const start = currentYearMonth();
  return {
    start_year_month: start,
    horizon_months: 60,
    targets_echo: [{ year_month: addMonths(start, 12), animal_class: "male_grower", count: 20 }],
    plan: {
      before: {
        targets: [{ month: 13, animal_class: "male_grower", requested: 20.5, filled: 18.25, shortfall: 2.25, price_per_head: 9800, revenue: 179000, met: true }],
        npv: 100,
        minimum_cash_balance: 0,
        minimum_cash_month: 1,
        additional_working_capital_required: 0,
        total_shortfall: 5,
        all_met: false,
      },
      after: {
        targets: [{ month: 13, animal_class: "male_grower", requested: 20.5, filled: 20, shortfall: 0, price_per_head: 9800, revenue: 196000, met: true }],
        npv: 200,
        minimum_cash_balance: 0,
        minimum_cash_month: 1,
        additional_working_capital_required: 0,
        total_shortfall: 5,
        all_met: true,
      },
      recommended_purchases: [{ month: 2, kind: "purchase", animal_class: "doe", count: 12 }],
      gaps_closed: false,
      probabilities: [{ month: 13, animal_class: "male_grower", requested: 20, p_full: 0.82, p_eighty: 0.97 }],
      notes: [],
    },
    stage_plan: [{ month: 1, year_month: start, female_kids: 3.7, male_kids: 3.7, female_weaners: 3.5, male_weaners: 3.5, female_growers: 3.2, male_growers: 3.2, open_does: 6.5, pregnant_does: 28.4, lactating_does: 14.9, bucks: 2.1, total_head: 72.7, births: 7.8, deaths: 0.6, culls_head: 0, sales_head: 0, purchases_head: 1 }],
    actions: [],
    chains: [],
    notes: [],
    ...overrides,
  };
}

function savedPlanRow(overrides: Record<string, unknown> = {}) {
  const start = "2027-01";
  return {
    id: 7,
    farm_id: 1,
    name: "Festival plan",
    notes: "Keep",
    start_year_month: start,
    targets: [
      { year_month: addMonths(start, 12), animal_class: "male_grower", count: 20 },
      { year_month: addMonths(start, 14), animal_class: "doe", count: 4 },
    ],
    assumptions: { ...GOAT_DEFAULTS, herd: { ...GOAT_DEFAULTS.herd, does: 77 } },
    valid: true,
    validation_error: null,
    revision: 1,
    created_at: "2026-09-02T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
    ...overrides,
  };
}

interface PlanResponse {
  status: number;
  body: Record<string, unknown>;
}

interface Options {
  farms?: unknown;
  permissions?: string[];
  permissionsError?: boolean;
  defaults?: unknown;
  defaultsStatus?: number;
  defaultsNeverResolve?: boolean;
  breedsStatus?: number;
  onDefaults?: (query: URL) => void;
  onBreeds?: (query: URL) => void;
  onPlans?: (query: URL) => void;
  onSnapshot?: (query: URL) => void;
  onCalibration?: (query: URL) => void;
  onPlan?: (body: unknown) => void;
  onSave?: (body: Record<string, unknown>) => void;
  onUpdate?: (query: Record<string, string>, body: Record<string, unknown>) => void;
  onDelete?: (id: string) => void;
  snapshotStatus?: number;
  snapshotBody?: unknown;
  deferSnapshot?: boolean;
  calibrationStatus?: number;
  calibrationBody?: unknown;
  deferCalibration?: boolean;
  planResult?: unknown;
  planStatus?: number;
  planNetworkError?: boolean;
  planResponses?: PlanResponse[];
  deferPlan?: boolean;
  savedPlans?: unknown[];
  savedPlansTotal?: number;
  saveStatus?: number;
  saveNetworkError?: boolean;
  updateStatus?: number;
  updateNetworkError?: boolean;
  getPlanStatus?: number;
  deleteStatus?: number;
  deleteNetworkError?: boolean;
  defaultsByBreed?: Record<string, unknown>;
  updateName?: string;
  waitForPage?: boolean;
}

interface HarnessState {
  defaultsBreedCalls: { breed: string; system: string }[];
  snapshotUrls: URL[];
  calibrationUrls: URL[];
  plansUrls: URL[];
  breedsUrls: URL[];
  deletedIds: string[];
  savedBodies: Record<string, unknown>[];
  updatedBodies: Record<string, unknown>[];
  planBodies: unknown[];
  plansItems: unknown[];
  plansTotal: number;
  permissionsErrorCalls: number;
  getPlanCalls: number;
  resolveDefaults?: () => void;
  resolvePlan?: () => void;
  resolveSnapshot?: () => void;
  resolveCalibration?: () => void;
}

async function renderLoaded2(options: Options = {}): Promise<HarnessState> {
  const state: HarnessState = {
    defaultsBreedCalls: [],
    snapshotUrls: [],
    calibrationUrls: [],
    plansUrls: [],
    breedsUrls: [],
    deletedIds: [],
    savedBodies: [],
    updatedBodies: [],
    planBodies: [],
    plansItems: options.savedPlans ? [...options.savedPlans] : [],
    plansTotal: options.savedPlansTotal ?? (options.savedPlans?.length ?? 0),
    permissionsErrorCalls: 0,
    getPlanCalls: 0,
  };
  let planCallCount = 0;
  let patchCallCount = 0;

  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json(options.farms ?? TEST_FARMS)),
    ...(options.permissions ? [permissionsHandler(options.permissions)] : []),
    ...(options.permissionsError
      ? [
          http.get("/api/auth/permissions", () => {
            state.permissionsErrorCalls += 1;
            return HttpResponse.json({ detail: "permissions down" }, { status: 500 });
          }),
        ]
      : []),
    http.get("/api/simulation/defaults/breeds", ({ request }) => {
      state.breedsUrls.push(new URL(request.url));
      return HttpResponse.json(
        { breeds: ["osmanabadi", "sirohi"], systems: ["stall_fed", "semi_intensive"] },
        { status: options.breedsStatus ?? 200 },
      );
    }),
    http.get("/api/simulation/defaults", ({ request }) => {
      const url = new URL(request.url);
      options.onDefaults?.(url);
      const breed = url.searchParams.get("breed") ?? "";
      state.defaultsBreedCalls.push({
        breed,
        system: url.searchParams.get("system") ?? "",
      });
      const body = options.defaultsByBreed?.[breed] ?? options.defaults ?? GOAT_DEFAULTS;
      if (options.defaultsNeverResolve) {
        return new Promise<Response>((resolve) => {
          state.resolveDefaults = () => resolve(HttpResponse.json(body));
        });
      }
      return HttpResponse.json(body, {
        status: options.defaultsStatus ?? 200,
      });
    }),
    http.get("/api/simulation/herd-snapshot", ({ request }) => {
      state.snapshotUrls.push(new URL(request.url));
      const respond = () =>
        HttpResponse.json(
          options.snapshotBody ?? {
            does: 61,
            bucks: 3,
            f_kids: 3,
            f_weaners: 2,
            f_growers: 1,
            m_kids: 3,
            m_weaners: 2,
            m_growers: 1,
            total_head: 76,
          },
          { status: options.snapshotStatus ?? 200 },
        );
      if (options.deferSnapshot) {
        return new Promise<Response>((resolve) => {
          state.resolveSnapshot = () => resolve(respond());
        });
      }
      return respond();
    }),
    http.get("/api/simulation/calibration", ({ request }) => {
      state.calibrationUrls.push(new URL(request.url));
      const respond = () =>
        HttpResponse.json(
          options.calibrationBody ?? {
            assumptions: { ...GOAT_DEFAULTS, herd: { ...GOAT_DEFAULTS.herd, does: 99 } },
            evidence: [1, 2, 3],
          },
          { status: options.calibrationStatus ?? 200 },
        );
      if (options.deferCalibration) {
        return new Promise<Response>((resolve) => {
          state.resolveCalibration = () => resolve(respond());
        });
      }
      return respond();
    }),
    http.get("/api/planner/plans", ({ request }) => {
      const url = new URL(request.url);
      options.onPlans?.(url);
      state.plansUrls.push(url);
      return HttpResponse.json({
        items: state.plansItems,
        total: state.plansTotal,
        limit: 50,
        offset: 0,
      });
    }),
    http.post("/api/planner/plan", async ({ request }) => {
      const body = await request.json();
      options.onPlan?.(body);
      state.planBodies.push(body);
      if (options.planResponses) {
        const response = options.planResponses[Math.min(planCallCount, options.planResponses.length - 1)];
        planCallCount += 1;
        return HttpResponse.json(response.body, { status: response.status });
      }
      if (options.deferPlan) {
        return new Promise<Response>((resolve) => {
          state.resolvePlan = () =>
            resolve(
              HttpResponse.json(options.planResult ?? planReport(), {
                status: options.planStatus ?? 200,
              }),
            );
        });
      }
      if (options.planNetworkError) return HttpResponse.error();
      return HttpResponse.json(options.planResult ?? planReport(), {
        status: options.planStatus ?? 200,
      });
    }),
    http.post("/api/planner/plans", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      options.onSave?.(body);
      state.savedBodies.push(body);
      if (options.saveNetworkError) return HttpResponse.error();
      if ((options.saveStatus ?? 201) >= 400) {
        return HttpResponse.json({ detail: "quota exceeded" }, { status: options.saveStatus });
      }
      const row = savedPlanRow({
        name: (body.name as string) ?? "Festival plan",
        targets: body.targets,
        assumptions: body.assumptions,
        start_year_month: body.start_year_month,
      });
      state.plansItems = [row];
      state.plansTotal = 1;
      return HttpResponse.json(row, { status: options.saveStatus ?? 201 });
    }),
    http.patch("/api/planner/plans/:id", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      options.onUpdate?.(
        Object.fromEntries(new URL(request.url).searchParams),
        body,
      );
      state.updatedBodies.push(body);
      if (options.updateNetworkError) return HttpResponse.error();
      const firstPatchStatus = patchCallCount === 0 ? (options.updateStatus ?? 200) : 200;
      patchCallCount += 1;
      if (firstPatchStatus >= 400) {
        return HttpResponse.json({ detail: "stale revision" }, { status: firstPatchStatus });
      }
      const row = savedPlanRow({
        name: options.updateName ?? ((body.name as string) ?? "Renamed plan"),
        targets: body.targets,
        assumptions: body.assumptions,
        start_year_month: body.start_year_month,
        revision: 2,
      });
      state.plansItems = [row];
      return HttpResponse.json(row, { status: firstPatchStatus });
    }),
    http.get("/api/planner/plans/:id", () => {
      state.getPlanCalls += 1;
      return HttpResponse.json(
        savedPlanRow({ name: "Festival plan v2", revision: 2 }),
        { status: options.getPlanStatus ?? 200 },
      );
    }),
    http.delete("/api/planner/plans/:id", ({ params }) => {
      state.deletedIds.push(String(params.id));
      options.onDelete?.(String(params.id));
      if (options.deleteNetworkError) return HttpResponse.error();
      if ((options.deleteStatus ?? 204) !== 204) {
        return HttpResponse.json({ detail: "not allowed" }, { status: options.deleteStatus });
      }
      state.plansItems = [];
      state.plansTotal = 0;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  renderWithProviders(<PlannerPage />, createTestQueryClient());
  if (options.waitForPage !== false) {
    expect(await screen.findByText("Sale targets")).toBeInTheDocument();
  }
  return state;
}

async function addTarget(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Add target" }));
}

function saleMonthInput(): HTMLInputElement {
  return screen.getByLabelText("Sale month") as HTMLInputElement;
}

const countInput = () => screen.getByLabelText("Count") as HTMLInputElement;

const noteStartingWith = (prefix: string) => (_: string, el: Element | null) =>
  el?.tagName === "P" && el.getAttribute("role") === "note" && !!el.textContent?.startsWith(prefix);


const paragraphWithExactText = (text: string) => (_: string, el: Element | null) =>
  el?.tagName === "P" && el.textContent === text;

beforeEach(() => {
  toastMocks.error.mockClear();
  toastMocks.success.mockClear();
});

describe("PlannerPage mutation round 2: month parsing and number formatting", () => {
  it("passes malformed API year-months through verbatim instead of formatting them", async () => {
    const user = userEvent.setup();
    await renderLoaded2({
      planResult: planReport({
        targets_echo: [
          { year_month: "2026-13", animal_class: "male_grower", count: 20 },
          { year_month: "12026-01", animal_class: "male_grower", count: 20 },
          { year_month: "2026-01-99", animal_class: "male_grower", count: 20 },
        ],
        plan: {
          ...planReport().plan,
          before: {
            ...planReport().plan.before,
            targets: [
              { month: 13, animal_class: "male_grower", requested: 20, filled: 20, shortfall: 0, price_per_head: 9800, revenue: 196000, met: true },
              { month: 14, animal_class: "male_grower", requested: 20, filled: 20, shortfall: 0, price_per_head: 9800, revenue: 196000, met: true },
              { month: 15, animal_class: "male_grower", requested: 20, filled: 20, shortfall: 0, price_per_head: 9800, revenue: 196000, met: true },
            ],
          },
        },
      }),
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    // "2026-13" (month 13), "12026-01" (5-digit year) and "2026-01-99"
    // (trailing garbage) all fail validation and must surface raw — not
    // "Jan 2026" / "Jan 12026" / "Jan 2026" respectively.
    expect(await screen.findByText("2026-13")).toBeInTheDocument();
    expect(screen.getByText("12026-01")).toBeInTheDocument();
    expect(screen.getByText("2026-01-99")).toBeInTheDocument();
    expect(screen.queryByText("Jan 2026")).toBeNull();
    expect(screen.queryByText("Jan 12026")).toBeNull();
  });

  it("labels the remove button with the raw month when it is malformed, and accepts months 10-12", async () => {
    const user = userEvent.setup();
    await renderLoaded2();
    await addTarget(user);

    // jsdom keeps 5-digit years in month inputs; the raw value must survive.
    fireEvent.change(saleMonthInput(), { target: { value: "12026-01" } });
    expect(
      screen.getByRole("button", { name: "Remove target 12026-01" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Jan 12026/ })).toBeNull();

    // A real month in the 10-12 band is valid (no validation alert).
    fireEvent.change(saleMonthInput(), { target: { value: "2026-10" } });
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Plan" })).toBeEnabled();
  });

  it("renders exactly the null head counts as em dashes", async () => {
    const user = userEvent.setup();
    await renderLoaded2({
      planResult: planReport({
        stage_plan: [
          {
            month: 1,
            year_month: currentYearMonth(),
            female_kids: null,
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
            births: null,
            deaths: 0.6,
            culls_head: 0,
            sales_head: 0,
            purchases_head: 1,
          },
        ] as unknown[],
      }),
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await screen.findByText(/Shortfall 5 head/);
    // Risk renders "82%" and the after column is filled, so the ONLY em
    // dashes on the page are the two null stage-plan cells.
    expect(screen.getAllByText("—").length).toBe(2);
  });
});

describe("PlannerPage mutation round 2: permission surface", () => {
  it("shows the loading skeleton (not the page or an access denial) while permissions load", async () => {
    let resolvePerms!: (value: Response) => void;
    server.use(
      http.get(
        "/api/auth/permissions",
        () => new Promise<Response>((resolve) => (resolvePerms = resolve)),
      ),
    );
    renderWithProviders(<PlannerPage />, createTestQueryClient());

    expect(screen.queryByText("Sale targets")).toBeNull();
    expect(screen.queryByText("You don't have access to this page.")).toBeNull();
    expect(document.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0);

    await waitFor(() => expect(typeof resolvePerms).toBe("function"));
    resolvePerms!(
      HttpResponse.json({ is_owner: true, permissions: ["simulation.view", "simulation.manage", "animals.view"] }),
    );
    expect(await screen.findByText("Sale targets")).toBeInTheDocument();
  });

  it("shows the permissions error state with a retry control", async () => {
    const state = await renderLoaded2({ permissionsError: true, waitForPage: false });
    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry permissions" });
    expect(retry).toBeInTheDocument();
    expect(screen.queryByText("Sale targets")).toBeNull();
    const callsBeforeRetry = state.permissionsErrorCalls;
    fireEvent.click(retry);
    await waitFor(() =>
      expect(state.permissionsErrorCalls).toBeGreaterThan(callsBeforeRetry),
    );
  });

  it("denies the page without simulation.view and fires no planner queries", async () => {
    const state = await renderLoaded2({
      permissions: ["animals.view"],
      waitForPage: false,
    });
    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(state.defaultsBreedCalls).toHaveLength(0);
    expect(state.breedsUrls).toHaveLength(0);
    expect(state.plansUrls).toHaveLength(0);
    expect(state.snapshotUrls).toHaveLength(0);
    expect(state.calibrationUrls).toHaveLength(0);
  });

  it("keeps calibration disabled when only part of the five permissions is granted", async () => {
    await renderLoaded2({
      permissions: ["simulation.view", "animals.view", "breeding.view"],
    });
    const calibrate = await screen.findByRole("button", { name: "Use farm records" });
    expect(calibrate).toBeDisabled();
    expect(calibrate).toHaveAttribute(
      "title",
      "Needs animals, breeding, births, feeding and finance read access.",
    );
  });

});

describe("PlannerPage mutation round 2: plan basis", () => {
  it("lists plans with the fixed paging params and shows the empty state", async () => {
    let plansUrl: URL | undefined;
    await renderLoaded2({ onPlans: (url) => (plansUrl = url) });
    await waitFor(() => expect(plansUrl).toBeDefined());
    expect(plansUrl?.searchParams.get("limit")).toBe("50");
    expect(plansUrl?.searchParams.get("offset")).toBe("0");
    expect(screen.getByText("No saved plans")).toBeInTheDocument();
  });

  it("shows the full preset basis note and none of the other basis lines", async () => {
    await renderLoaded2();
    await waitFor(() =>
      expect(screen.getByText(noteStartingWith("Starting from breed-preset defaults."))).toBeInTheDocument(),
    );
    expect(
      screen.getByText(noteStartingWith("Starting from breed-preset defaults.")).textContent,
    ).toBe(
      "Starting from breed-preset defaults. For full control of every assumption (feed, prices, finance), build them in Simulation and calibrate there first.",
    );
    expect(screen.queryByText(noteStartingWith("Starting from your live herd"))).toBeNull();
    expect(screen.queryByText(noteStartingWith("Starting from assumptions calibrated"))).toBeNull();
    expect(screen.queryByText(noteStartingWith("Starting from a saved plan"))).toBeNull();
  });

  it("adopts the herd snapshot by breed with the counts merged into the preset", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await waitFor(() => expect(state.defaultsBreedCalls.length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() => expect(state.snapshotUrls.length).toBeGreaterThan(0));
    expect(state.snapshotUrls[0]?.searchParams.get("breed")).toBe("osmanabadi");
    expect(
      (await screen.findByText(noteStartingWith("Starting from your live herd's"))).textContent,
    ).toBe(
      "Starting from your live herd's head counts on top of the osmanabadi preset. For full control of every assumption (feed, prices, finance), build them in Simulation and calibrate there first.",
    );
    expect(screen.queryByText(noteStartingWith("Starting from breed-preset"))).toBeNull();

    // The snapshot counts replace the preset herd; other preset fields (and
    // herd keys the snapshot does not carry) survive into the run payload.
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(state.planBodies.length).toBe(1));
    const herd = (state.planBodies[0] as { assumptions: { herd: Record<string, number> } }).assumptions.herd;
    expect(herd.does).toBe(61);
    expect(herd.yearling_does).toBe(9);
    expect((state.planBodies[0] as { assumptions: { reproduction: unknown } }).assumptions.reproduction).toEqual(
      GOAT_DEFAULTS.reproduction,
    );
  });

  it("treats a 2xx-but-not-200 snapshot as a failure", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ snapshotStatus: 201, snapshotBody: { does: 1 } });
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not load the herd snapshot."),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.queryByText(noteStartingWith("Starting from your live herd"))).toBeNull();
  });

  it("calibrates from farm records with the documented query and a fresh assumptions document", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await waitFor(() => expect(state.defaultsBreedCalls.length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() => expect(state.calibrationUrls.length).toBeGreaterThan(0));
    const calibrationUrl = state.calibrationUrls[0]!;
    expect(calibrationUrl.searchParams.get("breed")).toBe("osmanabadi");
    expect(calibrationUrl.searchParams.get("system")).toBe("stall_fed");
    expect(calibrationUrl.searchParams.get("lookback_months")).toBe("24");
    expect(
      (await screen.findByText(noteStartingWith("Starting from assumptions calibrated"))).textContent,
    ).toBe(
      "Starting from assumptions calibrated against this farm's own records. For full control of every assumption (feed, prices, finance), build them in Simulation and calibrate there first.",
    );
    expect(screen.queryByText(noteStartingWith("Starting from breed-preset"))).toBeNull();

    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(state.planBodies.length).toBe(1));
    expect(
      (state.planBodies[0] as { assumptions: { herd: { does: number } } }).assumptions.herd.does,
    ).toBe(99);
  });

  it("treats a 2xx-but-not-200 calibration as a failure and surfaces 409 details", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ calibrationStatus: 201, calibrationBody: { assumptions: GOAT_DEFAULTS } });
    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not calibrate from farm records."),
    );
    expect(screen.queryByText(noteStartingWith("Starting from assumptions calibrated"))).toBeNull();

    const user2 = userEvent.setup();
    await renderLoaded2({ calibrationStatus: 409, calibrationBody: { detail: "farm busy" } });
    await user2.click(screen.getByRole("button", { name: "Use farm records" }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("farm busy"));
  });

  it("holds every run gated while the breed preset has not loaded, and refuses herd adoption", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({
      defaultsNeverResolve: true,
    });
    expect(await screen.findByText("Sale targets")).toBeInTheDocument();
    // The preset basis line shows even before defaults land (initial state).
    expect(screen.getByText(noteStartingWith("Starting from breed-preset defaults."))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add target" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Plan" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "The breed preset is still loading — try again in a moment.",
      ),
    );
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(state.snapshotUrls).toHaveLength(0);
    expect(screen.queryByText(noteStartingWith("Starting from your live herd"))).toBeNull();

    state.resolveDefaults!();
    await waitFor(() => expect(screen.getByRole("button", { name: "Add target" })).toBeEnabled());
  });
});

describe("PlannerPage mutation round 2: targets editor", () => {
  it("shows the empty state with vocabulary-aware copy while there are no targets", async () => {
    await renderLoaded2();
    const empty = screen.getByText("No sale targets yet");
    expect(empty).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          !!el.textContent?.startsWith("Add your first target — e.g. “200 goats in"),
      ).textContent,
    ).toContain(`“200 goats in ${monthLabel(addMonths(currentYearMonth(), 16))}”`);
    expect(
      screen.getByText(/State what must be sold and when/),
    ).toBeInTheDocument();
    // Once the preset has loaded, the ONLY thing keeping Plan disabled is the
    // empty target list.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add target" })).toBeEnabled(),
    );
    expect(screen.getByRole("button", { name: "Plan" })).toBeDisabled();
  });

  it("ignores a malformed plan-start month typed into the input", async () => {
    await renderLoaded2();
    const start = screen.getByLabelText("Plan start") as HTMLInputElement;
    await waitFor(() => expect(screen.getByRole("button", { name: "Add target" })).toBeEnabled());
    // jsdom sanitizes "2026-13" to "" in month inputs; the handler must not
    // adopt it, so the stored start month stays the current month.
    fireEvent.change(start, { target: { value: "2026-13" } });
    expect(start.value).toBe(currentYearMonth());
  });

  it("removes only the requested row and keeps the sibling's month", async () => {
    const user = userEvent.setup();
    await renderLoaded2();
    await addTarget(user);
    await addTarget(user);

    const months = screen.getAllByLabelText("Sale month") as HTMLInputElement[];
    expect(months[0]!.value).toBe(addMonths(currentYearMonth(), 12));
    fireEvent.change(months[1]!, { target: { value: "2027-06" } });
    expect((screen.getAllByLabelText("Sale month")[0] as HTMLInputElement).value).toBe(
      addMonths(currentYearMonth(), 12),
    );

    // Removing the first row leaves exactly the edited second row.
    await user.click(screen.getAllByRole("button", { name: /^Remove target / })[0]!);
    const remaining = screen.getAllByLabelText("Sale month") as HTMLInputElement[];
    expect(remaining).toHaveLength(1);
    expect(remaining[0]!.value).toBe("2027-06");
    expect(screen.queryByText("No sale targets yet")).toBeNull();
  });

  it("labels the remove button with the formatted month", async () => {
    const user = userEvent.setup();
    await renderLoaded2();
    await addTarget(user);
    expect(
      screen.getByRole("button", {
        name: `Remove target ${monthLabel(addMonths(currentYearMonth(), 12))}`,
      }),
    ).toBeInTheDocument();
  });

  it("commits a changed class to the row and the run payload", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await addTarget(user);
    await user.click(screen.getByLabelText("Class"));
    await user.click(await screen.findByRole("option", { name: "Doe" }));
    expect(screen.getByLabelText("Class")).toHaveTextContent("Doe");

    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(state.planBodies.length).toBe(1));
    expect((state.planBodies[0] as { targets: { animal_class: string }[] }).targets[0]?.animal_class).toBe("doe");
  });

  it("caps the target list at 50 rows", async () => {
    await renderLoaded2();
    const addButton = screen.getByRole("button", { name: "Add target" });
    await waitFor(() => expect(addButton).toBeEnabled());
    for (let i = 0; i < 50; i += 1) fireEvent.click(addButton);
    expect(screen.getAllByLabelText("Sale month")).toHaveLength(50);
    expect(addButton).toBeDisabled();
  });
});

describe("PlannerPage mutation round 2: run gates and report rendering", () => {
  it("ignores a 2xx-but-not-200 plan response", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ planStatus: 201, planResult: planReport() });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.queryByText(/Shortfall \d+ head/)).toBeNull());
    expect(screen.queryByText(/The plan is feasible|The plan cannot fully close/)).toBeNull();
  });

  it("surfaces the fallback message on a network-level plan failure", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ planNetworkError: true });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Plan failed");
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Plan failed"));
  });

  it("clears a previous plan error after a successful re-run", async () => {
    const user = userEvent.setup();
    await renderLoaded2({
      planResponses: [
        { status: 422, body: { detail: "first fail" } },
        { status: 200, body: planReport() },
      ],
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("first fail");
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(/Shortfall 5 head/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("labels the button Planning… and disables it while the run is in flight", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ deferPlan: true });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    const pending = screen.getByRole("button", { name: "Planning…" });
    expect(pending).toBeDisabled();
    state.resolvePlan!();
    expect(await screen.findByText(/Shortfall 5 head/)).toBeInTheDocument();
  });

  it("renders the infeasible verdict, after-purchase NPV, filled cells and risk", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ planResult: planReport() });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));

    expect(await screen.findByText("The plan cannot fully close")).toBeInTheDocument();
    const desc = screen.getByText(/Shortfall 5 head after recommendations/);
    expect(desc.textContent).toBe(
      "Shortfall 5 head after recommendations · 1 recommended purchase event(s) · NPV ₹100 before purchases, ₹200 after.",
    );
    expect(screen.getByText("20.5")).toBeInTheDocument();
    expect(screen.getByText("✓ 18.3")).toBeInTheDocument();
    expect(screen.getByText("✓ 20")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("falls back to the before-plan when no after-plan exists", async () => {
    const user = userEvent.setup();
    const base = planReport();
    await renderLoaded2({
      planResult: { ...base, plan: { ...base.plan, after: null } },
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(/Shortfall 5 head/)).toBeInTheDocument();
    const desc = screen.getByText(/Shortfall 5 head/);
    expect(desc.textContent).toContain("NPV ₹100 before purchases.");
    expect(desc.textContent).not.toContain("after.");
    const row = screen.getByText("✓ 18.3").closest("tr")!;
    expect(within(row).getAllByRole("cell")[4]!.textContent).toBe("—");
  });

  it("renders no report cards when both plan evaluations are absent", async () => {
    const user = userEvent.setup();
    const base = planReport();
    await renderLoaded2({
      planResult: { ...base, plan: { ...base.plan, before: null, after: null } },
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.queryByText(/Shortfall \d+ head/)).toBeNull());
    expect(screen.queryByText("What to do and when")).toBeNull();
  });

  it("renders a null probabilities array without crashing", async () => {
    const user = userEvent.setup();
    const base = planReport();
    await renderLoaded2({
      planResult: { ...base, plan: { ...base.plan, probabilities: null } },
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(/Shortfall 5 head/)).toBeInTheDocument();
    const cells = screen.getByText("✓ 20").closest("tr")!.querySelectorAll("td");
    expect(cells[6]!.textContent).toBe("—");
  });

  it("styles only pre-start actions as missed deadlines and joins the action labels", async () => {
    const user = userEvent.setup();
    const start = currentYearMonth();
    await renderLoaded2({
      planResult: planReport({
        actions: [
          { month: 0, year_month: addMonths(start, -1), kind: "purchase", headline: "Buy early", detail: "detail-a" },
          { month: 1, year_month: start, kind: "breed", headline: "Breed now", detail: "detail-b" },
          { month: 6, year_month: addMonths(start, 5), kind: "sell", headline: "Sell later", detail: "detail-c" },
        ],
      }),
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));

    const missed = screen.getByText("Buy early").closest("div.rounded-lg")!;
    expect(missed.className).toContain("bg-warning-tint/30");
    expect(missed.className).toContain("border-warning-tint-border");
    const atStart = screen.getByText("Breed now").closest("div.rounded-lg")!;
    expect(atStart.className).not.toContain("bg-warning-tint");
    expect(atStart.className).toContain("border-border");
    const later = screen.getByText("Sell later").closest("div.rounded-lg")!;
    expect(later.className).not.toContain("bg-warning-tint");

    expect(
      screen.getByText(paragraphWithExactText(`${monthLabel(addMonths(start, -1))} · Buy: Buy early`)),
    ).toBeInTheDocument();
    expect(
      screen.getByText(paragraphWithExactText(`${monthLabel(start)} · Breed: Breed now`)),
    ).toBeInTheDocument();
  });

  it("joins the requirement-chain header with spaces and renders its steps", async () => {
    const user = userEvent.setup();
    const start = currentYearMonth();
    await renderLoaded2({
      planResult: planReport({
        chains: [
          {
            year_month: addMonths(start, 12),
            animal_class: "doe",
            count: 9,
            achievable: false,
            steps: [{ year_month: addMonths(start, 5), quantity: 34, label: "doe(s) bred" }],
            explanation: "chain explanation",
          },
        ],
      }),
    });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(`9 Doe in ${monthLabel(addMonths(start, 12))}`)).toBeInTheDocument();
    expect(
      screen.getByText(
        paragraphWithExactText(
          `9 Doe in ${monthLabel(addMonths(start, 12))} not achievable as planned`,
        ),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("not achievable as planned")).toBeInTheDocument();
    expect(screen.getByText("doe(s) bred")).toBeInTheDocument();
    expect(screen.getByText("34")).toBeInTheDocument();
  });

  it("renders plan notes when present", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ planResult: planReport({ notes: ["Plan note A"] }) });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText("Plan note A")).toBeInTheDocument();
    expect(screen.getByText("Notes")).toBeInTheDocument();
  });

  it("hides the plan notes card for an empty notes array", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ planResult: planReport({ notes: [] }) });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await screen.findByText(/Shortfall 5 head/);
    expect(screen.queryByText("Notes")).toBeNull();
  });

  it("hides the plan notes card when notes are absent entirely", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ planResult: planReport({ notes: null }) });
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    await screen.findByText(/Shortfall 5 head/);
    expect(screen.queryByText("Notes")).toBeNull();
    expect(screen.queryByText("Stryker was here")).toBeNull();
  });
});

describe("PlannerPage mutation round 2: save, update, delete and open", () => {
  it("saves a trimmed plan name, opens it and refreshes the saved list", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await addTarget(user);
    await user.type(screen.getByLabelText("Plan name"), "  Festival  ");
    const defaultsBeforeSave = state.defaultsBreedCalls.length;
    await user.click(screen.getByRole("button", { name: "Save plan" }));

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Saved plan “Festival”."),
    );
    expect(screen.getByRole("button", { name: "Update “Festival”" })).toBeInTheDocument();
    expect(await screen.findByText("Festival")).toBeInTheDocument();
    // Saving invalidates the plans list only — the preset query is untouched.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(state.defaultsBreedCalls.length).toBe(defaultsBeforeSave);
  });

  it("surfaces a rejected save with the server detail", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ saveStatus: 422 });
    await addTarget(user);
    await user.type(screen.getByLabelText("Plan name"), "Festival");
    await user.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("quota exceeded"));
    expect(screen.queryByRole("button", { name: /Update “/ })).toBeNull();
  });

  it("surfaces a network-level save failure with the fallback message", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ saveNetworkError: true });
    await addTarget(user);
    await user.type(screen.getByLabelText("Plan name"), "Festival");
    await user.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not save the plan."),
    );
  });

  it("keeps Save disabled for an empty or blank name, with no targets, or with target errors", async () => {
    const user = userEvent.setup();
    await renderLoaded2();
    await user.type(screen.getByLabelText("Plan name"), "X");
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();

    await addTarget(user);
    expect(screen.getByRole("button", { name: "Save plan" })).toBeEnabled();

    await user.clear(countInput());
    await user.type(countInput(), "0");
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();

    await user.clear(countInput());
    await user.type(countInput(), "5");
    await user.clear(screen.getByLabelText("Plan name"));
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();
    await user.type(screen.getByLabelText("Plan name"), " ");
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();
  });

  it("ignores a save response that is not 201", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ saveStatus: 200 });
    await addTarget(user);
    await user.type(screen.getByLabelText("Plan name"), "Festival");
    await user.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Plan" })).toBeInTheDocument());
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Update “/ })).toBeNull();
  });

  it("requires a name to update, sends a trimmed name with the full target list", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Open" }));

    await user.clear(screen.getByLabelText("Plan name"));
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Give the plan a name before updating it."),
    );
    expect(state.updatedBodies).toHaveLength(0);

    await user.type(screen.getByLabelText("Plan name"), "  Renamed plan  ");
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() => expect(state.updatedBodies.length).toBe(1));
    const body = state.updatedBodies[0]!;
    expect(body.name).toBe("Renamed plan");
    expect(body.expected_revision).toBe(1);
    expect(body.targets).toEqual([
      { year_month: addMonths("2027-01", 12), animal_class: "male_grower", count: 20 },
      { year_month: addMonths("2027-01", 14), animal_class: "doe", count: 4 },
    ]);
  });

  it("confirms an update with the server's name, relabels the button and refreshes the list", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    await user.clear(screen.getByLabelText("Plan name"));
    await user.type(screen.getByLabelText("Plan name"), "Renamed plan");
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Updated “Renamed plan”."),
    );
    expect(screen.getByRole("button", { name: "Update “Renamed plan”" })).toBeInTheDocument();
    expect(await screen.findByText("Renamed plan")).toBeInTheDocument();
  });

  it("surfaces a rejected update with the server detail", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ savedPlans: [savedPlanRow()], updateStatus: 422 });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    toastMocks.success.mockClear();
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("stale revision"));
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument();
  });

  it("recovers from a 409 conflict by adopting the fresh revision and retrying", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()], updateStatus: 409 });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "This plan changed in another tab. The latest revision was loaded — press Update again.",
      ),
    );
    // The refreshed row (revision 2, new name) is adopted for the retry.
    expect(await screen.findByRole("button", { name: /Update “Festival plan v2”/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Update “Festival plan v2”/ }));
    await waitFor(() => expect(state.updatedBodies.length).toBe(2));
    expect(state.updatedBodies[1]!.expected_revision).toBe(2);
  });

  it("keeps the open plan when the conflict refresh returns a non-200 body", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({
      savedPlans: [savedPlanRow()],
      updateStatus: 409,
      getPlanStatus: 201,
    });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "This plan changed in another tab. The latest revision was loaded — press Update again.",
      ),
    );
    await waitFor(() => expect(state.getPlanCalls).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 150));
    // A 2xx-but-not-200 refresh is not adopted: the open plan survives.
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument();
  });

  it("keeps the open plan when the conflict refresh request itself fails", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({
      savedPlans: [savedPlanRow()],
      updateStatus: 409,
      getPlanStatus: 500,
    });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "This plan changed in another tab. The latest revision was loaded — press Update again.",
      ),
    );
    await waitFor(() => expect(state.getPlanCalls).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument();
  });

  it("surfaces a network-level update failure with the fallback message", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ savedPlans: [savedPlanRow()], updateNetworkError: true });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    toastMocks.error.mockClear();
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not update the saved plan."),
    );
  });

  it("ignores an update response that is 2xx-but-not-200", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ savedPlans: [savedPlanRow()], updateStatus: 201 });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    toastMocks.success.mockClear();
    await user.click(screen.getByRole("button", { name: /Update “Festival plan”/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument();
  });

  it("keeps Update disabled without targets or with target errors", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    const update = screen.getByRole("button", { name: /Update “Festival plan”/ });
    expect(update).toBeEnabled();

    await user.click(screen.getAllByRole("button", { name: /^Remove target / })[0]!);
    await user.click(screen.getAllByRole("button", { name: /^Remove target / })[0]!);
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeDisabled();

    await addTarget(user);
    await user.clear(countInput());
    await user.type(countInput(), "0");
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeDisabled();
  });

  it("disables Save, Update and Delete for a view-only user", async () => {
    const user = userEvent.setup();
    await renderLoaded2({
      permissions: ["simulation.view"],
      savedPlans: [savedPlanRow()],
    });
    await screen.findByRole("button", { name: "Open" });
    // With a valid target and a name, ONLY the missing simulation.manage
    // permission keeps Save disabled.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add target" })).toBeEnabled(),
    );
    await addTarget(user);
    await user.type(screen.getByLabelText("Plan name"), "X");
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete plan Festival plan" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const update = await screen.findByRole("button", { name: /Update “Festival plan”/ });
    expect(update).toBeDisabled();
  });

  it("does not delete until the confirm dialog is accepted", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Delete plan Festival plan" }));
    // Staged only: the DELETE must not fire from the row button alone.
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(state.deletedIds).toEqual([]);

    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(state.deletedIds).toEqual(["7"]));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Deleted “Festival plan”."),
    );
  });

  it("cancels the delete-confirm dialog without deleting", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Delete plan Festival plan" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(state.deletedIds).toEqual([]);
    expect(screen.getByText("Festival plan")).toBeInTheDocument();
  });

  it("deletes a saved plan and clears an open plan", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete plan Festival plan" }));
    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(state.deletedIds).toEqual(["7"]));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Deleted “Festival plan”."),
    );
    expect(screen.queryByRole("button", { name: /Update “/ })).toBeNull();
    expect(screen.getByText("No saved plans")).toBeInTheDocument();
    expect((screen.getByLabelText("Plan name") as HTMLInputElement).value).toBe("");
  });

  it("ignores a delete response that is not 204 (no open plan involved)", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({
      savedPlans: [savedPlanRow()],
      deleteStatus: 202,
    });
    await user.click(await screen.findByRole("button", { name: "Delete plan Festival plan" }));
    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(state.deletedIds).toEqual(["7"]));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(await screen.findByText("Festival plan")).toBeInTheDocument();
  });

  it("deletes a plan without one open (no open-plan state to consult)", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await user.click(await screen.findByRole("button", { name: "Delete plan Festival plan" }));
    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    await waitFor(() => expect(state.deletedIds).toEqual(["7"]));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Deleted “Festival plan”."),
    );
    expect(screen.getByText("No saved plans")).toBeInTheDocument();
  });

  it("keeps an unrelated open plan when another plan is deleted", async () => {
    const user = userEvent.setup();
    await renderLoaded2({
      savedPlans: [savedPlanRow(), savedPlanRow({ id: 8, name: "Other plan" })],
    });
    await user.click((await screen.findAllByRole("button", { name: "Open" }))[0]!);
    await user.click(screen.getByRole("button", { name: "Delete plan Other plan" }));
    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Deleted “Other plan”."),
    );
    // The open plan is untouched: the update control and name survive.
    expect(screen.getByRole("button", { name: /Update “Festival plan”/ })).toBeInTheDocument();
    expect((screen.getByLabelText("Plan name") as HTMLInputElement).value).toBe("Festival plan");
  });

  it("surfaces a failed delete with the fallback message", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ savedPlans: [savedPlanRow()], deleteNetworkError: true });
    await user.click(await screen.findByRole("button", { name: "Delete plan Festival plan" }));
    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Could not delete the plan."),
    );
  });

  it("ignores an Open click on a plan without assumptions", async () => {
    const user = userEvent.setup();
    await renderLoaded2({
      savedPlans: [savedPlanRow({ assumptions: null, targets: [] })],
    });
    await user.click(await screen.findByRole("button", { name: "Open" }));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(screen.getByText("No sale targets yet")).toBeInTheDocument();
    expect(screen.queryByText(noteStartingWith("Starting from a saved plan"))).toBeNull();
  });

  it("adopts a saved plan: dates, name, basis, presets reset, stale report cleared, assumptions re-runnable", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ savedPlans: [savedPlanRow()] });

    // Produce a report first — opening a plan must clear it.
    await addTarget(user);
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(await screen.findByText(/Shortfall 5 head/)).toBeInTheDocument();

    // Re-anchor away from the species defaults, then open the plan.
    await user.click(screen.getByLabelText("Breed preset"));
    await user.click(await screen.findByRole("option", { name: "sirohi" }));
    await waitFor(() =>
      expect(state.defaultsBreedCalls.some((c) => c.breed === "sirohi")).toBe(true),
    );
    await user.click(screen.getByLabelText("Production system"));
    await user.click(await screen.findByRole("option", { name: "Semi-intensive" }));
    await waitFor(() =>
      expect(state.defaultsBreedCalls.some((c) => c.system === "semi_intensive")).toBe(true),
    );

    await user.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(
        "Opened “Festival plan” — press Plan to re-run it against today's biology.",
      ),
    );
    expect((screen.getByLabelText("Plan start") as HTMLInputElement).value).toBe("2027-01");
    expect((screen.getByLabelText("Plan name") as HTMLInputElement).value).toBe("Festival plan");
    expect(screen.getByLabelText("Production system")).toHaveTextContent("Stall-fed");
    expect(screen.getByLabelText("Production system")).not.toHaveTextContent("Semi-intensive");
    expect(screen.getAllByLabelText("Sale month")).toHaveLength(2);
    expect(screen.queryByText(/Shortfall \d+ head/)).toBeNull();
    expect(
      (await screen.findByText(noteStartingWith("Starting from a saved plan's assumptions."))).textContent,
    ).toBe(
      "Starting from a saved plan's assumptions. For full control of every assumption (feed, prices, finance), build them in Simulation and calibrate there first.",
    );

    // The preset re-anchoring refetches the species defaults, but the saved
    // basis (and its assumptions document) must survive that landing.
    await waitFor(() =>
      expect(
        state.defaultsBreedCalls.filter((c) => c.breed === "osmanabadi" && c.system === "stall_fed")
          .length,
      ).toBeGreaterThanOrEqual(2),
    );
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.getByText(noteStartingWith("Starting from a saved plan's assumptions."))).toBeInTheDocument();
    expect(screen.queryByText(noteStartingWith("Starting from breed-preset"))).toBeNull();

    // Row keys stay per-row: removing one of the two rows leaves one.
    await user.click(screen.getAllByRole("button", { name: /^Remove target / })[0]!);
    expect(screen.getAllByLabelText("Sale month")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(state.planBodies.length).toBe(2));
    expect(
      (state.planBodies[1] as { assumptions: { herd: { does: number } } }).assumptions.herd.does,
    ).toBe(77);
  });

});

describe("PlannerPage mutation round 2: saved plans table", () => {
  it("hides invalid plans from the list", async () => {
    await renderLoaded2({
      savedPlans: [savedPlanRow({ valid: false, name: "Broken plan" })],
    });
    expect(await screen.findByText("No saved plans")).toBeInTheDocument();
    expect(screen.queryByText("Broken plan")).toBeNull();
  });

  it("renders each plan's targets and notes with em-dash fallbacks", async () => {
    await renderLoaded2({
      savedPlans: [
        savedPlanRow(),
        savedPlanRow({
          id: 8,
          name: "Empty plan",
          notes: "",
          targets: null,
        }),
      ],
    });
    const festivalRow = (await screen.findByText("Festival plan")).closest("tr")!;
    expect(
      within(festivalRow).getByText(
        `20 Male grower ${monthLabel(addMonths("2027-01", 12))}; 4 Doe ${monthLabel(addMonths("2027-01", 14))}`,
      ),
    ).toBeInTheDocument();
    expect(within(festivalRow).getByText("Keep")).toBeInTheDocument();

    const emptyRow = screen.getByText("Empty plan").closest("tr")!;
    const cells = within(emptyRow).getAllByRole("cell");
    expect(cells[2]!.textContent).toBe("—");
    expect(cells[3]!.textContent).toBe("—");
  });

  it("notes when the list is truncated", async () => {
    await renderLoaded2({
      savedPlans: [savedPlanRow()],
      savedPlansTotal: 3,
    });
    expect(
      await screen.findByText("Showing the first 1 of 3 saved plans."),
    ).toBeInTheDocument();
  });

  it("stays quiet when every saved plan fits on the page", async () => {
    await renderLoaded2({ savedPlans: [savedPlanRow()] });
    await screen.findByText("Festival plan");
    expect(screen.queryByText(/Showing the first/)).toBeNull();
  });
});

describe("PlannerPage mutation round 2: breed and system presets", () => {
  it("offers the server's breeds and reloads defaults on breed change", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await waitFor(() => expect(state.defaultsBreedCalls.length).toBeGreaterThan(0));
    await user.click(screen.getByLabelText("Breed preset"));
    expect(await screen.findByRole("option", { name: "osmanabadi" })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "sirohi" }));
    await waitFor(() =>
      expect(state.defaultsBreedCalls.some((c) => c.breed === "sirohi")).toBe(true),
    );
    expect(screen.getByLabelText("Breed preset")).toHaveTextContent("sirohi");
  });

  it("labels the production system trigger from the items map and reloads on change", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await waitFor(() => expect(state.defaultsBreedCalls.length).toBeGreaterThan(0));
    expect(screen.getByLabelText("Production system")).toHaveTextContent("Stall-fed");
    await user.click(screen.getByLabelText("Production system"));
    await user.click(await screen.findByRole("option", { name: "Semi-intensive" }));
    await waitFor(() =>
      expect(state.defaultsBreedCalls.some((c) => c.system === "semi_intensive")).toBe(true),
    );
    expect(screen.getByLabelText("Production system")).toHaveTextContent("Semi-intensive");
  });

  it("falls back to no breed options when the breeds endpoint fails", async () => {
    const user = userEvent.setup();
    await renderLoaded2({ breedsStatus: 500 });
    await waitFor(() => expect(screen.getByLabelText("Breed preset")).toBeInTheDocument());
    await user.click(screen.getByLabelText("Breed preset"));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });
});

describe("PlannerPage mutation round 2: basis buttons", () => {
  it("labels the basis buttons Loading…/Calibrating… while their requests run", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2({ deferSnapshot: true, deferCalibration: true });
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
    state.resolveSnapshot!();
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Starting stock set to your current herd (76 head)."),
    );
    await user.click(screen.getByRole("button", { name: "Use farm records" }));
    expect(screen.getByRole("button", { name: "Calibrating…" })).toBeDisabled();
    state.resolveCalibration!();
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Calibrated 3 assumptions from your farm records."),
    );
  });

  it("names the breed inside the herd basis note after switching presets", async () => {
    const user = userEvent.setup();
    const state = await renderLoaded2();
    await waitFor(() => expect(state.defaultsBreedCalls.length).toBeGreaterThan(0));
    await user.click(screen.getByLabelText("Breed preset"));
    await user.click(await screen.findByRole("option", { name: "sirohi" }));
    await waitFor(() =>
      expect(state.defaultsBreedCalls.some((c) => c.breed === "sirohi")).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Use my herd" }));
    expect(
      (await screen.findByText(noteStartingWith("Starting from your live herd's"))).textContent,
    ).toBe(
      "Starting from your live herd's head counts on top of the sirohi preset. For full control of every assumption (feed, prices, finance), build them in Simulation and calibrate there first.",
    );
  });
});
